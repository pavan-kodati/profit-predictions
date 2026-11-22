"""Holdings: add and import, exit alerts (plan E1, E6), and portfolio review (plan 3.5).

Privacy: quantities and prices stay in the database. Nothing in this module calls the AI.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import exits, portfolio, scoring
from stockresearch.services import repo, reports

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- add and import

class HoldingError(ValueError):
    pass


def resolve_symbol(s: Session, text: str) -> str | None:
    t = text.strip().upper().removesuffix(".NS").removesuffix("-EQ")
    if not t:
        return None
    if s.get(m.Stock, t):
        return t
    if re.fullmatch(r"IN[A-Z0-9]{10}", t):
        return s.scalar(select(m.Stock.symbol).where(m.Stock.isin == t))
    return None


def add_holding(c: Container, symbol: str, quantity: float, avg_price: float, buy_date: date | None, horizon: str,
                stop_price: float | None = None, source: str = "manual", pick_id: int | None = None) -> int:
    if quantity <= 0 or avg_price <= 0:
        raise HoldingError("Quantity and price must be positive")
    if horizon not in {"short", "long"}:
        raise HoldingError("Horizon must be 'short' or 'long'")
    with session_scope(c.sessions) as s:
        sym = resolve_symbol(s, symbol)
        if sym is None:
            raise HoldingError(f"Unknown NSE symbol or ISIN: {symbol}")
        h = m.Holding(symbol=sym, quantity=quantity, avg_price=avg_price, buy_date=buy_date, horizon=horizon,
                      stop_price=stop_price, source=source, pick_id=pick_id, closed=False, created_at=repo.now_ist())
        s.add(h)
        s.flush()
        repo.enqueue(s, sym, "New holding", priority=10)
        return h.id


COLUMN_ALIASES = {
    "symbol": ("symbol", "instrument", "tradingsymbol", "trading symbol", "stock", "stock name", "scrip", "isin", "name"),
    "quantity": ("quantity", "qty", "qty.", "quantity available", "shares", "net qty"),
    "avg_price": ("avg. cost", "avg cost", "average price", "average buy price", "avg price", "buy price",
                  "average cost", "avg. price"),
    "buy_date": ("buy date", "date", "purchase date"),
}


@dataclass
class ImportResult:
    added: int
    skipped: list[str]


def _find(headers: list[str], field: str) -> str | None:
    lowered = {h.strip().lower(): h for h in headers}
    for alias in COLUMN_ALIASES[field]:
        if alias in lowered:
            return lowered[alias]
    return None


def _parse_date(text: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def import_csv(c: Container, content: str, horizon: str) -> ImportResult:
    """Import a broker holdings export (Zerodha, Groww, Upstox and similar, as CSV)."""
    reader = csv.DictReader(io.StringIO(content.lstrip("\N{ZERO WIDTH NO-BREAK SPACE}")))  # strip a UTF-8 BOM
    headers = list(reader.fieldnames or [])
    sym_col, qty_col, price_col = _find(headers, "symbol"), _find(headers, "quantity"), _find(headers, "avg_price")
    date_col = _find(headers, "buy_date")
    if not sym_col or not qty_col or not price_col:
        raise HoldingError("Could not find symbol, quantity and average price columns in the file. "
                           f"Columns found: {', '.join(headers)}")
    added, skipped = 0, []
    for i, row in enumerate(reader, start=2):
        raw = (row.get(sym_col) or "").strip()
        try:
            qty = float(str(row.get(qty_col) or "0").replace(",", ""))
            price = float(str(row.get(price_col) or "0").replace(",", ""))
            when = _parse_date(row.get(date_col) or "") if date_col else None
            add_holding(c, raw, qty, price, when, horizon, source="import")
            added += 1
        except (HoldingError, ValueError) as e:
            skipped.append(f"Row {i} ({raw or 'blank'}): {e}")
    return ImportResult(added, skipped)


# ---------------------------------------------------------------- exit alerts

def exit_alerts(c: Container, detail: list[str]) -> None:
    today = repo.today_ist()
    created = 0
    with session_scope(c.sessions) as s:
        open_holdings = list(s.scalars(select(m.Holding).where(m.Holding.closed.is_(False))))
        bars = repo.load_bars(s, sorted({h.symbol for h in open_holdings}), days=420)
        for h in open_holdings:
            t = repo.technicals_for(bars.get(h.symbol))
            if t is None:
                continue
            negatives = [e for e in scoring.verified_negatives(repo.event_facts(s, h.symbol, today - timedelta(days=3)), today)
                         if e.event_date >= today - timedelta(days=3)]
            for sig in exits.check(h.symbol, h.horizon, h.avg_price, h.buy_date, h.stop_price, t, negatives):
                created += repo.add_alert(s,f"h{h.id}:{sig.kind}:{sig.key}", h.symbol, sig.kind, sig.severity, sig.message,
                                      holding_id=h.id)
    detail.append(f"{created} new alert(s)")
    if created:
        from stockresearch.services import notify

        notify.send_pending_alerts(c)


# ---------------------------------------------------------------- portfolio review

def _return_between(bars, start: date | None) -> float | None:
    if bars is None or bars.empty or start is None:
        return None
    before = bars[bars.index.date <= start]
    if before.empty:
        return None
    return float(bars["close"].iloc[-1] / before["close"].iloc[-1] - 1)


def review_all(c: Container, run_id: int | None, detail: list[str]) -> None:
    today = repo.today_ist()
    with session_scope(c.sessions) as s:
        open_holdings = list(s.scalars(select(m.Holding).where(m.Holding.closed.is_(False))))
        ids = [(h.id, h.symbol, h.horizon) for h in open_holdings]
    counts: dict[str, int] = {}
    for hid, sym, horizon in ids:
        rid = reports.build_and_save(c, sym, purpose="holding", run_id=run_id, long_term=horizon == "long")
        with session_scope(c.sessions) as s:
            h = s.get(m.Holding, hid)
            if h is None:
                continue
            review = review_one(c, s, h, rid, today)
            s.add(review)
            counts[review.status] = counts.get(review.status, 0) + 1
    detail.append(", ".join(f"{v} {k}" for k, v in counts.items()) or "no holdings")


def review_one(c: Container, s, h: m.Holding, report_id: int | None, today: date) -> m.HoldingReview:
    report = s.get(m.Report, report_id) if report_id else None
    bars = repo.load_bars(s, [h.symbol, "^NSEI"], days=800)
    t = repo.technicals_for(bars.get(h.symbol))
    f = repo.fundamentals_for(s, h.symbol)
    research = repo.latest_research(s, h.symbol, max_age_days=10)
    o = repo.ownership_for(s, h.symbol, today, research.data if research else None, f.institutional_pct)
    negatives = scoring.verified_negatives(repo.event_facts(s, h.symbol, today - timedelta(days=60)), today)
    quality = report.data["areas"]["quality"]["score"] if report else None
    rating_value = report.rating if report else 3.0
    status = portfolio.health_status(rating_value, quality, f, o, t, negatives, h.horizon, c.settings.max_pledge_pct)
    averaging = None
    if t is not None:
        averaging = portfolio.averaging_check(h.avg_price, t, _return_between(bars.get(h.symbol), h.buy_date),
                                              _return_between(bars.get("^NSEI"), h.buy_date), status, quality)
    since = datetime.combine(h.buy_date or (today - timedelta(days=90)), datetime.min.time())
    changes = [
        {"date": d.published_at.date().isoformat() if d.published_at else None, "title": d.title, "url": d.url,
         "publisher": d.publisher, "tier": d.tier, "document_id": d.id}
        for d in s.scalars(select(m.SourceDocument).where(
            m.SourceDocument.symbol == h.symbol, m.SourceDocument.published_at >= since,
            m.SourceDocument.kind.in_(("filing", "insider", "deal", "shareholding")))
            .order_by(m.SourceDocument.published_at.desc()).limit(15))
    ]
    data: dict[str, Any] = {
        "reasons": status.reasons,
        "rating": rating_value,
        "close": t.close if t else None,
        "gain": (t.close / h.avg_price - 1) if t else None,
        "averaging": averaging,
        "tax": portfolio.tax_timing(h.buy_date, today, bool(t and t.close > h.avg_price)),
        "changes": changes,
    }
    return m.HoldingReview(holding_id=h.id, report_id=report_id, review_date=today, status=status.status.value,
                           data=reports._jsonable(data))


def portfolio_concentration(s: Session) -> dict[str, Any]:
    holdings = list(s.scalars(select(m.Holding).where(m.Holding.closed.is_(False))))
    bars = repo.load_bars(s, sorted({h.symbol for h in holdings}), days=30)
    values: dict[str, float] = {}
    sectors: dict[str, str | None] = {}
    for h in holdings:
        df = bars.get(h.symbol)
        price = float(df["close"].iloc[-1]) if df is not None and not df.empty else h.avg_price
        values[h.symbol] = values.get(h.symbol, 0) + price * h.quantity
        stock = s.get(m.Stock, h.symbol)
        sectors[h.symbol] = stock.industry if stock else None
    return portfolio.concentration(values, sectors)
