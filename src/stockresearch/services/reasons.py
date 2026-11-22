"""Why a pretend trade moved (plan v3, section 4): gathers what moved with the price and applies the fixed rules
in `domain/attribution.py`. Updated daily while a trade is open and frozen when it is sold."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from stockresearch.db import models as m
from stockresearch.domain import attribution as rules
from stockresearch.domain.models import PickType, Tier
from stockresearch.services import repo

BIG_MARKET_DAY = 0.01  # headlines are shown only for a day the Nifty moved at least this much
MARKET_CATEGORY = "market"


def _close_on(df: pd.DataFrame | None, day: date) -> float | None:
    if df is None or df.empty:
        return None
    upto = df[df.index.date <= day]  # type: ignore[attr-defined]
    return float(upto["close"].iloc[-1]) if not upto.empty else None


def _open_on(df: pd.DataFrame | None, day: date) -> float | None:
    if df is None or df.empty:
        return None
    rows = df[df.index.date >= day]  # type: ignore[attr-defined]
    return float(rows["open"].iloc[0]) if not rows.empty else None


def _ret(df: pd.DataFrame | None, start: date, end: date) -> float | None:
    a, b = _open_on(df, start), _close_on(df, end)
    return b / a - 1 if a and b else None


def _events(s: Session, symbol: str, start: date, end: date) -> list[rules.EventNote]:
    rows = s.scalars(select(m.Event).where(m.Event.symbol == symbol, m.Event.event_date >= start,
                                           m.Event.event_date <= end, m.Event.status.in_(("verified", "reported"))))
    out = []
    for e in rows:
        docs = sorted(e.documents, key=lambda d: d.tier)
        out.append(rules.EventNote(e.event_date, e.title, e.polarity, docs[0].url if docs else None))
    return out


def _sector_ret(s: Session, symbol: str, start: date, end: date, cache: dict[str, float | None]) -> float | None:
    stock = s.get(m.Stock, symbol)
    if stock is None or not stock.industry:
        return None
    key = f"{stock.industry}|{start}|{end}"
    if key not in cache:
        peers = list(s.scalars(select(m.Stock.symbol).where(m.Stock.industry == stock.industry,
                                                            m.Stock.in_universe.is_(True), m.Stock.symbol != symbol)))
        bars = repo.load_bars(s, peers, days=(repo.today_ist() - start).days + 10) if len(peers) >= 3 else {}
        moves = [r for r in (_ret(df, start, end) for df in bars.values()) if r is not None]
        cache[key] = float(pd.Series(moves).median()) if len(moves) >= 3 else None
    return cache[key]


def _market_headlines(s: Session, index: pd.DataFrame | None, start: date, end: date) -> list[rules.Headline]:
    """Headlines from trusted newspapers on the day the Nifty moved most while the trade was held."""
    if index is None or index.empty:
        return []
    window = index[(index.index.date >= start) & (index.index.date <= end)]  # type: ignore[attr-defined]
    moves = (window["close"] / window["close"].shift(1).fillna(window["open"]) - 1).abs()
    if moves.empty or float(moves.max()) < BIG_MARKET_DAY:
        return []
    day = pd.Timestamp(str(moves.idxmax())).date()
    docs = s.scalars(select(m.SourceDocument).where(
        m.SourceDocument.category == MARKET_CATEGORY, m.SourceDocument.tier == int(Tier.PRESS),
        m.SourceDocument.published_at >= datetime.combine(day, time.min),
        m.SourceDocument.published_at <= datetime.combine(day, time.max),
    ).order_by(m.SourceDocument.published_at.desc()).limit(2))
    return [rules.Headline(d.title, d.url) for d in docs]


def update(s: Session, trades: list[Any], prices: dict[str, pd.DataFrame], index: pd.DataFrame | None,
           as_of: date | None) -> int:
    """Set the reason on each trade (app or user). `trades` are open trades and trades closed in this run."""
    cache: dict[str, float | None] = {}
    done = 0
    for t in trades:
        if t.entry_date is None or not t.entry_price:
            continue
        end = t.exit_date or as_of or t.entry_date
        last = t.exit_price if t.exit_price is not None else _close_on(prices.get(t.symbol), end)
        if last is None:
            continue
        reason = rules.explain(
            stock_ret=last / t.entry_price - 1,
            index_ret=_ret(index, t.entry_date, end),
            sector_ret=_sector_ret(s, t.symbol, t.entry_date, end, cache),
            events=_events(s, t.symbol, t.entry_date, end),
            is_hype=t.pick_type == PickType.HYPE.value,
            market_headlines=_market_headlines(s, index, t.entry_date, end),
        )
        t.reason_key, t.reason_text, t.reason_links = reason.key, reason.text, reason.links
        done += 1
    return done
