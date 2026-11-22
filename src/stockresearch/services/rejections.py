"""Stocks the app said no to (plan v3, section 4): record each rejection, then follow the price for 10 market days
to see whether saying no was right. No pretend money is involved, only the price change against the Nifty 50."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from stockresearch import presenters
from stockresearch.adapters.yahoo import INDEX_SYMBOL
from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import attribution as rules
from stockresearch.domain import corporate_actions as ca
from stockresearch.services import repo


def reason_for(r: m.Report, min_rating: float) -> str:
    raw = r.excluded_reason or next(iter(r.data.get("caps") or []), None)
    if raw:
        return presenters.plain(raw)
    return f"The case was not strong enough (rated {r.rating:.1f}; a pick needs {min_rating:g} or more)."


def record(s: Session, reports: list[m.Report], today: date, min_rating: float) -> int:
    """Start following today's rejected stocks. A stock already being followed is not recorded again."""
    following = set(s.scalars(select(m.Rejection.symbol).where(m.Rejection.final.is_(False))))
    known = set(s.scalars(select(m.Rejection.report_id).where(m.Rejection.report_id.in_([r.id for r in reports]))))
    added = 0
    for r in reports:
        if r.is_pick or r.purpose != "daily" or r.data.get("held_back") or r.symbol in following or r.id in known:
            continue
        s.add(m.Rejection(report_id=r.id, symbol=r.symbol, reject_date=today, ref_close=float(r.data["tech"]["close"]),
                          ref_date=r.data_as_of or today, rating=r.rating, reason=reason_for(r, min_rating),
                          days_tracked=0, final=False, updated_at=repo.now_ist()))
        following.add(r.symbol)
        added += 1
    return added


def _close_on(df: pd.DataFrame | None, day: date) -> float | None:
    if df is None or df.empty:
        return None
    upto = df[df.index.date <= day]  # type: ignore[attr-defined]
    return float(upto["close"].iloc[-1]) if not upto.empty else None


def _judged_before_a_split(s: Session) -> list[m.Rejection]:
    """Finished rejections whose 10 days spanned a split or bonus recorded only after they were judged: re-judged
    on adjusted prices (a rejection is a follow-up note, not a trade)."""
    out = []
    for a in s.scalars(select(m.CorporateAction).where(m.CorporateAction.factor.is_not(None))):
        out += [r for r in s.scalars(select(m.Rejection).where(
            m.Rejection.symbol == a.symbol, m.Rejection.final.is_(True), m.Rejection.ref_date < a.ex_date,
            m.Rejection.updated_at < a.created_at, m.Rejection.reject_date >= a.ex_date - timedelta(days=30)))
            if r not in out]
    return out


def update(c: Container, detail: list[str]) -> None:
    """Refresh the price change and verdict of every rejection still being followed."""
    updated = finished = 0
    with session_scope(c.sessions) as s:
        rows = list(s.scalars(select(m.Rejection).where(m.Rejection.final.is_(False))))
        rows += _judged_before_a_split(s)
        prices = repo.load_bars(s, sorted({r.symbol for r in rows} | {INDEX_SYMBOL}), days=90) if rows else {}
        actions = repo.load_actions(s, sorted(prices))
        index = prices.get(INDEX_SYMBOL)
        for r in rows:
            df = ca.rebase(prices.get(r.symbol), actions.get(r.symbol, []), r.ref_date)
            if df is None:
                continue
            after = df[df.index.date > r.ref_date]  # type: ignore[attr-defined]
            if after.empty or not r.ref_close:
                continue
            n = min(len(after), rules.REJECTION_TRACK_DAYS)
            end = after.index[n - 1].date()
            r.ret = float(after["close"].iloc[n - 1]) / r.ref_close - 1
            base, last = _close_on(index, r.ref_date), _close_on(index, end)
            r.index_ret = last / base - 1 if base and last else None
            r.days_tracked = n
            r.verdict = rules.skip_verdict(r.ret, r.index_ret)
            r.final = n >= rules.REJECTION_TRACK_DAYS
            r.updated_at = repo.now_ist()
            updated += 1
            finished += r.final
    detail.append(f"{updated} rejected stock(s) updated, {finished} finished")


@dataclass
class RejectionView:
    r: m.Rejection
    name: str

    @property
    def verdict_title(self) -> str:
        return rules.VERDICT_TITLE.get(self.r.verdict or "", "Too early to say")


@dataclass
class RejectionSummary:
    rows: list[RejectionView] = field(default_factory=list)
    judged: int = 0  # followed for the full 10 market days
    right: int = 0
    little: int = 0
    missed: int = 0

    @property
    def following(self) -> int:
        return len(self.rows) - self.judged


def summarise(s: Session, limit: int = 200) -> RejectionSummary:
    rows = list(s.scalars(select(m.Rejection).order_by(m.Rejection.reject_date.desc(), m.Rejection.id.desc()).limit(limit)))
    names = {st.symbol: st.name for st in s.scalars(select(m.Stock).where(m.Stock.symbol.in_({r.symbol for r in rows})))}
    out = RejectionSummary(rows=[RejectionView(r, names.get(r.symbol, r.symbol)) for r in rows])
    for r in rows:
        if r.final and r.verdict:
            out.judged += 1
            out.right += r.verdict == rules.RIGHT
            out.little += r.verdict == rules.LITTLE
            out.missed += r.verdict == rules.MISSED
    return out
