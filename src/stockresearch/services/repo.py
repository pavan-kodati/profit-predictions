"""Data access helpers shared by the services. Converts between database rows and domain objects."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from stockresearch.config import IST
from stockresearch.db import models as m
from stockresearch.domain import corporate_actions, verification
from stockresearch.domain.models import (
    EventFact,
    EventType,
    Fundamentals,
    Ownership,
    Quarter,
    SourceRef,
    Technicals,
    Tier,
    Verification,
)
from stockresearch.domain.technicals import compute as compute_technicals


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def today_ist() -> date:
    return now_ist().date()


# ---------------------------------------------------------------- stocks

def upsert_stock(s: Session, symbol: str, name: str, industry: str | None, isin: str | None, in_universe: bool) -> None:
    row = s.get(m.Stock, symbol)
    if row is None:
        s.add(m.Stock(symbol=symbol, name=name, industry=industry, isin=isin, in_universe=in_universe,
                      updated_at=now_ist()))
        return
    row.name = name or row.name
    row.industry = industry or row.industry
    row.isin = isin or row.isin
    row.in_universe = in_universe or row.in_universe
    row.updated_at = now_ist()


def universe_symbols(s: Session) -> list[str]:
    return list(s.scalars(select(m.Stock.symbol).where(m.Stock.in_universe.is_(True))))


def holding_symbols(s: Session) -> list[str]:
    return sorted(set(s.scalars(select(m.Holding.symbol).where(m.Holding.closed.is_(False)))))


def stock_name(s: Session, symbol: str) -> str:
    row = s.get(m.Stock, symbol)
    return row.name if row else symbol


# ---------------------------------------------------------------- prices

def store_prices(s: Session, rows: list[dict[str, Any]]) -> int:
    """Upsert daily bars. NSE rows (with delivery) override Yahoo rows for the same day."""
    if not rows:
        return 0
    if s.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        dialect_insert = sqlite_insert  # type: ignore[assignment]
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        stmt = dialect_insert(m.DailyPrice).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "date"],
            set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume", "delivery_pct", "series", "source",
                                               "basis_date")},
            where=(m.DailyPrice.source != "nse") | (stmt.excluded.source == "nse"),
        )
        s.execute(stmt)
    return len(rows)


MARKET_SETTLED_HOUR = 16  # NSE closes at 15:30 IST; before 16:00 today's bar is still moving


def session_finished(day: date, now: datetime) -> bool:
    """True if `day`'s trading session is over, so its bar is a real closing price."""
    return day < now.date() or (day == now.date() and now.hour >= MARKET_SETTLED_HOUR)


def frame_to_rows(symbol: str, df: pd.DataFrame, source: str) -> list[dict[str, Any]]:
    """Convert a price frame to rows, dropping today's bar while the market is still open (it is not a close)."""
    out = []
    now = now_ist()
    for ts, r in zip(pd.to_datetime(df.index), df.to_dict("records"), strict=True):
        # Yahoo prices are already split-adjusted up to the day they are downloaded: that day is their basis.
        if pd.isna(r["close"]) or not session_finished(ts.date(), now):
            continue
        out.append({
            "symbol": symbol, "date": ts.date(), "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]), "volume": float(r.get("volume") or 0),
            "delivery_pct": None, "series": None, "source": source, "basis_date": now.date(),
        })
    return out


def load_bars(
    s: Session, symbols: list[str] | None = None, days: int = 400, until: date | None = None,
) -> dict[str, pd.DataFrame]:
    since = (until or today_ist()) - timedelta(days=days)
    q = select(m.DailyPrice).where(m.DailyPrice.date >= since)
    if until:
        q = q.where(m.DailyPrice.date <= until)
    if symbols is not None:
        q = q.where(m.DailyPrice.symbol.in_(symbols))
    df = pd.read_sql(q, s.connection())
    out: dict[str, pd.DataFrame] = {}
    if df.empty:
        return out
    df["date"] = pd.to_datetime(df["date"])
    actions = load_actions(s, sorted(set(df["symbol"])))
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("date").set_index("date")
        frame = g[["open", "high", "low", "close", "volume", "delivery_pct", "series"]]
        acts = actions.get(str(sym))
        if acts:  # express every row in the latest share units (splits and bonus issues)
            basis = [pd.Timestamp(b).date() if pd.notna(b) else d.date()
                     for b, d in zip(g["basis_date"], g.index, strict=True)]
            frame = corporate_actions.to_latest(frame, pd.Series(basis, index=frame.index), acts)
        out[str(sym)] = frame
    return out


def load_actions(s: Session, symbols: list[str] | None = None) -> dict[str, list[corporate_actions.Action]]:
    """Splits, bonus issues and other changes in share count, by symbol."""
    q = select(m.CorporateAction)
    if symbols is not None:
        q = q.where(m.CorporateAction.symbol.in_(symbols))
    out: dict[str, list[corporate_actions.Action]] = {}
    for a in s.scalars(q.order_by(m.CorporateAction.ex_date)):
        out.setdefault(a.symbol, []).append(corporate_actions.Action(a.ex_date, a.factor, a.kind, a.text))
    return out


def add_action(s: Session, symbol: str, ex_date: date, kind: str, factor: float | None, text: str,
               source: str) -> bool:
    """Record a corporate action once. Returns True if it was new."""
    exists = s.scalar(select(m.CorporateAction.id).where(
        m.CorporateAction.symbol == symbol, m.CorporateAction.ex_date == ex_date, m.CorporateAction.kind == kind,
        m.CorporateAction.source == source))
    if exists:
        return False
    s.add(m.CorporateAction(symbol=symbol, ex_date=ex_date, kind=kind, factor=factor, text=text[:500], source=source,
                            created_at=now_ist()))
    s.flush()
    return True


def technicals_for(bars: pd.DataFrame | None, band_pct: float | None = None) -> Technicals | None:
    if bars is None or bars.empty:
        return None
    series = bars["series"].dropna()
    return compute_technicals(bars, str(series.iloc[-1]) if not series.empty else None, band_pct)


def update_price_bands(s: Session, bands: dict[str, float]) -> int:
    n = 0
    for stock in s.scalars(select(m.Stock).where(m.Stock.symbol.in_(list(bands)))):
        stock.price_band = bands[stock.symbol]
        n += 1
    return n


def latest_price_date(s: Session) -> date | None:
    return s.scalar(select(func.max(m.DailyPrice.date)).where(m.DailyPrice.symbol != "^NSEI"))


# ---------------------------------------------------------------- documents and events

def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


def save_document(
    s: Session, *, url: str, symbol: str | None, kind: str, category: str | None, title: str, text: str,
    published_at: datetime | None, tier: Tier, publisher: str,
) -> m.SourceDocument:
    """Store a document with a saved copy of its text. Blocked sources must never reach this function."""
    if tier == Tier.BLOCKED:
        raise ValueError(f"refusing to store a blocked source: {url}")
    doc = s.scalar(select(m.SourceDocument).where(m.SourceDocument.url == url))
    if doc is None:
        doc = m.SourceDocument(url=url, symbol=symbol, kind=kind, category=category, publisher=publisher,
                               tier=int(tier), title=title[:2000], text=text, content_hash=content_hash(text),
                               published_at=published_at, fetched_at=now_ist())
        s.add(doc)
        s.flush()
    elif len(text) > len(doc.text or ""):
        doc.text = text
        doc.content_hash = content_hash(text)
    return doc


def source_ref(doc: m.SourceDocument) -> SourceRef:
    return SourceRef(doc.id, doc.url, doc.publisher, Tier(doc.tier), doc.published_at, doc.title)


def upsert_event(
    s: Session, *, key: str, symbol: str, event_type: EventType, polarity: int, title: str, event_date: date,
    documents: list[m.SourceDocument], origin: str, value_cr: float | None = None,
) -> m.Event:
    ev = s.scalar(select(m.Event).where(m.Event.dedupe_key == key))
    if ev is None:
        ev = m.Event(dedupe_key=key, symbol=symbol, event_type=event_type.value, polarity=polarity, title=title[:500],
                     event_date=event_date, value_cr=value_cr, status=Verification.UNVERIFIED.value, origin=origin,
                     created_at=now_ist())
        s.add(ev)
    for d in documents:
        if d not in ev.documents:
            ev.documents.append(d)
    ev.status = verification.verify(source_ref(d) for d in ev.documents).value
    s.flush()
    return ev


def update_event_reading(ev: m.Event, event_type: EventType, polarity: int, title: str, value_cr: float | None,
                         extra_docs: list[m.SourceDocument]) -> None:
    """Apply the AI's reading of a filing whose direction was unclear."""
    if ev.polarity == 0 or ev.event_type == EventType.OTHER.value:
        ev.event_type = event_type.value
        ev.polarity = polarity
        ev.title = title[:500]
    if value_cr is not None and ev.value_cr is None:
        ev.value_cr = value_cr
    for d in extra_docs:
        if d not in ev.documents:
            ev.documents.append(d)
    ev.status = verification.verify(source_ref(d) for d in ev.documents).value


def event_facts(s: Session, symbol: str, since: date) -> list[EventFact]:
    rows = s.scalars(select(m.Event).where(m.Event.symbol == symbol, m.Event.event_date >= since))
    return [
        EventFact(symbol=e.symbol, event_type=EventType(e.event_type), polarity=e.polarity, title=e.title,
                  event_date=e.event_date, status=Verification(e.status), value_cr=e.value_cr,
                  sources=tuple(source_ref(d) for d in e.documents))
        for e in rows
    ]


def slug(text: str, n: int = 50) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n]


# ---------------------------------------------------------------- fundamentals and ownership

def _f(x: Any) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _cagr(values: list[float | None]) -> float | None:
    """values newest first, yearly."""
    if len(values) < 4 or values[0] is None or values[3] is None or values[3] <= 0 or values[0] <= 0:
        return None
    return (values[0] / values[3]) ** (1 / 3) - 1


def fundamentals_for(s: Session, symbol: str) -> Fundamentals:
    snap = s.scalar(select(m.FundamentalsSnapshot).where(m.FundamentalsSnapshot.symbol == symbol)
                    .order_by(m.FundamentalsSnapshot.fetched_at.desc()).limit(1))
    quarters = list(s.scalars(select(m.QuarterlyResult).where(m.QuarterlyResult.symbol == symbol)
                              .order_by(m.QuarterlyResult.period_end.desc()).limit(6)))
    if snap is None and not quarters:
        return Fundamentals()
    info: dict[str, Any] = (snap.data if snap else {}).get("info", {})
    annual = (snap.data if snap else {}).get("annual", [])
    revenue = [_f(a[1]) for a in annual]
    profit = [_f(a[2]) for a in annual]
    dte = _f(info.get("debtToEquity"))
    mcap = _f(info.get("marketCap"))
    inst = _f(info.get("heldPercentInstitutions"))
    annual_rev = _f(info.get("totalRevenue"))
    return Fundamentals(
        sector=info.get("sector"),
        market_cap_cr=mcap / 1e7 if mcap else None,
        pe=_f(info.get("trailingPE")),
        revenue_growth=_f(info.get("revenueGrowth")),
        earnings_growth=_f(info.get("earningsGrowth")),
        profit_margin=_f(info.get("profitMargins")),
        debt_to_equity=dte / 100 if dte is not None else None,  # Yahoo reports a percentage
        roe=_f(info.get("returnOnEquity")),
        revenue_cagr_3y=_cagr(revenue),
        profit_cagr_3y=_cagr(profit),
        annual_revenue_cr=annual_rev / 1e7 if annual_rev else (revenue[0] if revenue else None),
        institutional_pct=inst * 100 if inst is not None else None,
        quarters=tuple(Quarter(q.period_end, q.revenue_cr, q.profit_cr) for q in quarters),
    )


def ownership_for(s: Session, symbol: str, today: date, research: dict[str, Any] | None,
                  institutional_pct: float | None) -> Ownership:
    rows = list(s.scalars(select(m.ShareholdingQuarterly).where(m.ShareholdingQuarterly.symbol == symbol)
                          .order_by(m.ShareholdingQuarterly.period_end.desc()).limit(5)))
    latest = rows[0] if rows else None

    def change(n: int) -> float | None:
        if len(rows) <= n:
            return None
        now, then = rows[0].promoter_pct, rows[n].promoter_pct
        return round(now - then, 2) if now is not None and then is not None else None

    pledged = next((r.pledged_pct for r in rows if r.pledged_pct is not None), None)
    buys, sells = fund_deal_counts(s, symbol, today)
    facts = (research or {}).get("facts", {})
    return Ownership(
        as_of=latest.period_end if latest else None,
        promoter_pct=latest.promoter_pct if latest else None,
        promoter_change_qoq=change(1),
        promoter_change_yoy=change(4),
        pledged_pct=pledged,
        institutional_pct=institutional_pct,
        promoter_net_buy_cr_90d=_f(facts.get("insider_net_cr_90d")),
        fund_bulk_buys_30d=buys,
        fund_bulk_sells_30d=sells,
    )


def fund_deal_counts(s: Session, symbol: str, today: date) -> tuple[int, int]:
    since = datetime.combine(today - timedelta(days=30), datetime.min.time())
    docs = s.scalars(select(m.SourceDocument).where(
        m.SourceDocument.symbol == symbol, m.SourceDocument.kind == "deal",
        m.SourceDocument.published_at >= since, m.SourceDocument.category == "institution"))
    buys = sells = 0
    for d in docs:
        if " bought " in d.text:
            buys += 1
        elif " sold " in d.text:
            sells += 1
    return buys, sells


def surveillance_for(s: Session, symbol: str) -> str | None:
    row = s.scalar(select(m.Surveillance).where(m.Surveillance.symbol == symbol).limit(1))
    return f"{row.list_name}: {row.stage}" if row else None


def replace_surveillance(s: Session, entries: dict[str, str], as_of: date) -> None:
    s.execute(delete(m.Surveillance))
    for sym, desc in entries.items():
        list_name = "GSM" if "GSM" in desc.upper() or "graded" in desc.lower() else "ASM"
        s.add(m.Surveillance(symbol=sym, list_name=list_name, stage=desc[:80], as_of=as_of))


# ---------------------------------------------------------------- alerts

def add_alert(s: Session, key: str, symbol: str, kind: str, severity: str, message: str,
              holding_id: int | None = None) -> bool:
    """Create an alert once per dedupe key. Returns True if it is new."""
    if s.scalar(select(m.Alert.id).where(m.Alert.dedupe_key == key)):
        return False
    s.add(m.Alert(dedupe_key=key, holding_id=holding_id, symbol=symbol, kind=kind, severity=severity, message=message,
                  created_at=now_ist(), sent=False))
    s.flush()
    return True


# ---------------------------------------------------------------- research queue and AI budget

def enqueue(s: Session, symbol: str, reason: str, priority: int, on_demand: bool = False) -> bool:
    existing = s.scalar(select(m.ResearchQueueItem).where(
        m.ResearchQueueItem.symbol == symbol, m.ResearchQueueItem.status.in_(("pending", "running"))))
    if existing:
        if priority < existing.priority:
            existing.priority = priority
            existing.reason = reason
        existing.on_demand = existing.on_demand or on_demand
        return False
    s.add(m.ResearchQueueItem(symbol=symbol, reason=reason[:200], priority=priority, on_demand=on_demand,
                              status="pending", requested_at=now_ist()))
    return True


def latest_research(s: Session, symbol: str, max_age_days: int = 5) -> m.ResearchResult | None:
    since = now_ist() - timedelta(days=max_age_days)
    return s.scalar(select(m.ResearchResult).where(m.ResearchResult.symbol == symbol,
                                                   m.ResearchResult.created_at >= since)
                    .order_by(m.ResearchResult.created_at.desc()).limit(1))


def ai_calls_today(s: Session) -> int:
    row = s.get(m.AiUsage, today_ist())
    return row.calls if row else 0


def record_ai_call(s: Session) -> None:
    row = s.get(m.AiUsage, today_ist())
    if row is None:
        s.add(m.AiUsage(day=today_ist(), calls=1))
    else:
        row.calls += 1


def on_demand_today(s: Session) -> int:
    start = datetime.combine(today_ist(), datetime.min.time())
    return s.scalar(select(func.count()).select_from(m.ResearchQueueItem).where(
        m.ResearchQueueItem.on_demand.is_(True), m.ResearchQueueItem.requested_at >= start)) or 0
