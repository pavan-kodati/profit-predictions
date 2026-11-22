"""Splits and bonus issues (plan v5): fetch them from NSE, catch the ones we missed with NSE's own previous close,
and note pretend trades that were settled on unadjusted prices before an action was known.

Stored prices are never rewritten: `repo.load_bars` adjusts them when they are read.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import corporate_actions as rules
from stockresearch.services import repo

log = logging.getLogger(__name__)
LOOK_BACK_DAYS = 30
LOOK_AHEAD_DAYS = 30
BOOKS: tuple[tuple[str, type[m.PracticeTrade] | type[m.UserTrade]], ...] = (("app", m.PracticeTrade), ("user", m.UserTrade))


def ingest(c: Container, symbols: set[str], detail: list[str], today: date | None = None) -> None:
    """Record splits, bonus issues and consolidations from NSE for the past month and the month ahead."""
    today = today or repo.today_ist()
    rows = c.exchange.corporate_actions(today - timedelta(days=LOOK_BACK_DAYS), today + timedelta(days=LOOK_AHEAD_DAYS))
    added = unreadable = 0
    with session_scope(c.sessions) as s:
        for r in rows:
            if r.symbol not in symbols:
                continue
            for kind, factor in rules.parse(r.subject):
                if repo.add_action(s, r.symbol, r.ex_date, kind, factor, r.subject, "nse"):
                    added += 1
                    unreadable += kind == rules.UNREADABLE
        added += record_corrections(s)
    detail.append(f"{len(rows)} corporate actions on NSE; {added} new split/bonus record(s) or correction note(s)"
                  + (f"; {unreadable} could not be read and are flagged" if unreadable else ""))


def cross_check(s: Session, day_df: pd.DataFrame, day: date, symbols: set[str]) -> list[str]:
    """Compare NSE's previous close (adjusted by NSE on an ex-date) with ours. An unexplained jump is recorded
    with the factor NSE implies, so prices are corrected, and the stock is kept out of picks for a while."""
    if "prev_close" not in day_df:
        return []
    nse_prev = {str(r["symbol"]): float(r["prev_close"]) for r in day_df.to_dict("records")
                if r["symbol"] in symbols and pd.notna(r.get("prev_close")) and r["prev_close"]}
    if not nse_prev:
        return []
    rows = s.execute(select(m.DailyPrice.symbol, m.DailyPrice.date, m.DailyPrice.close, m.DailyPrice.basis_date)
                     .where(m.DailyPrice.symbol.in_(list(nse_prev)), m.DailyPrice.date < day,
                            m.DailyPrice.date >= day - timedelta(days=15))
                     .order_by(m.DailyPrice.date)).all()
    last = {sym: (d, close, basis or d) for sym, d, close, basis in rows}  # the latest row wins
    actions = repo.load_actions(s, list(last))
    found = []
    for sym, (_, close, basis) in last.items():
        ours = rules.convert(close, actions.get(sym, []), basis, day)
        ratio = rules.unexplained_ratio(ours, nse_prev[sym])
        if ratio is None:
            continue
        text = (f"NSE's previous close for {sym} on {day:%d %b %Y} was Rs {nse_prev[sym]:,.2f}, ours Rs {ours:,.2f}: "
                f"the share count changed by a factor of {ratio:.4g} with no split or bonus on record")
        if repo.add_action(s, sym, day, rules.UNEXPLAINED, round(ratio, 6), text, "nse-prev-close"):
            found.append(sym)
            repo.add_alert(s, f"price-jump:{sym}:{day}", sym, "price_history", "warn",
                           f"{text}. Prices were adjusted using NSE's figure and the stock is kept out of picks for "
                           f"{rules.HOLD_OUT_DAYS} days.")
    if found:
        record_corrections(s)
    return found


def record_corrections(s: Session) -> int:
    """Add a correction note to each finished pretend trade that spanned an action recorded only after it ended."""
    actions = list(s.scalars(select(m.CorporateAction).where(m.CorporateAction.factor.is_not(None))))
    if not actions:
        return 0
    done = {(b, i) for b, i in s.execute(select(m.TradeCorrection.book, m.TradeCorrection.trade_id)).all()}
    by_symbol: dict[str, list[m.CorporateAction]] = {}
    for a in actions:
        by_symbol.setdefault(a.symbol, []).append(a)
    added = 0
    for book, table in BOOKS:
        closed: list[m.PracticeTrade | m.UserTrade] = list(s.scalars(
            select(table).where(table.status == "closed", table.symbol.in_(list(by_symbol)))))
        for t in closed:
            if (book, t.id) in done or t.entry_date is None or t.exit_date is None:
                continue
            for a in by_symbol[t.symbol]:
                if t.entry_date < a.ex_date <= t.exit_date and a.created_at.date() > t.exit_date:
                    what = rules.describe([rules.Action(a.ex_date, a.factor, a.kind)], t.entry_date)
                    s.add(m.TradeCorrection(
                        book=book, trade_id=t.id, symbol=t.symbol, action_id=a.id, created_at=repo.now_ist(),
                        note=(f"{what[0] if what else 'Share count changed'} while this trade was held. It was settled "
                              "on prices that were not yet adjusted, so its result is not reliable and is left out "
                              "of the totals. The trade itself is unchanged.")))
                    added += 1
                    break
    s.flush()
    return added


def corrections(s: Session, book: str) -> dict[int, str]:
    """Correction notes for one book, by trade id."""
    return {t: n for t, n in s.execute(select(m.TradeCorrection.trade_id, m.TradeCorrection.note)
                                       .where(m.TradeCorrection.book == book)).all()}


def add_manual(c: Container, symbol: str, ex_date: date, factor: float, note: str) -> str:
    if factor <= 0:
        raise ValueError("The factor must be above 0 (5 for a 1-into-5 split, 2 for a 1:1 bonus)")
    with session_scope(c.sessions) as s:
        if not repo.add_action(s, symbol.upper(), ex_date, rules.MANUAL, factor, note or "Entered by hand", "manual"):
            return f"Already recorded: {symbol.upper()} on {ex_date}"
        n = record_corrections(s)
    return f"Recorded {symbol.upper()} factor {factor:g} from {ex_date}" + (f"; {n} correction note(s) added" if n else "")


@dataclass
class Affected:
    what: str
    symbol: str
    detail: str


def check(c: Container) -> list[Affected]:
    """Everything whose dates span a recorded split or bonus: trades, holdings, picks and rejections."""
    out: list[Affected] = []
    with session_scope(c.sessions) as s:
        actions = repo.load_actions(s)
        if not actions:
            return out
        symbols = list(actions)

        def spans(sym: str, start: date | None, end: date | None) -> list[rules.Action]:
            if start is None:
                return []
            return [a for a in actions.get(sym, []) if a.factor and start < a.ex_date <= (end or date.max)]

        notes = {("app", i) for i in corrections(s, "app")} | {("user", i) for i in corrections(s, "user")}
        for book, table in BOOKS:
            trades: list[m.PracticeTrade | m.UserTrade] = list(s.scalars(select(table).where(table.symbol.in_(symbols))))
            for t in trades:
                for a in spans(t.symbol, t.entry_date, t.exit_date):
                    state = ("correction note added" if (book, t.id) in notes
                             else "valued on adjusted prices" if t.status == "open" else "settled on adjusted prices")
                    out.append(Affected(f"{'App' if book == 'app' else 'Your practice'} trade #{t.id} ({t.status})",
                                        t.symbol, f"{a.kind} on {a.ex_date}: {state}"))
        for h in s.scalars(select(m.Holding).where(m.Holding.closed.is_(False), m.Holding.symbol.in_(symbols))):
            for a in spans(h.symbol, h.created_at.date(), None):
                out.append(Affected(f"Holding #{h.id}", h.symbol,
                                    f"{a.kind} on {a.ex_date}: quantity, average and stop shown in new share units"))
        for p in s.scalars(select(m.Pick).where(m.Pick.symbol.in_(symbols))):
            for a in spans(p.symbol, p.ref_date, None):
                out.append(Affected(f"Pick #{p.id} of {p.pick_date}", p.symbol, f"{a.kind} on {a.ex_date}"))
        for r in s.scalars(select(m.Rejection).where(m.Rejection.symbol.in_(symbols))):
            for a in spans(r.symbol, r.ref_date, None):
                out.append(Affected(f"Rejection #{r.id} of {r.reject_date}", r.symbol, f"{a.kind} on {a.ex_date}"))
    return out
