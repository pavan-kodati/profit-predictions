"""Hourly collection: new exchange filings and approved-press headlines, stored with a saved copy (plan section 6)."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import filings, sources
from stockresearch.domain.models import EventType, Tier
from stockresearch.ports import Filing
from stockresearch.services import repo
from stockresearch.services.reasons import MARKET_CATEGORY
from stockresearch.services.runs import JobContext

log = logging.getLogger(__name__)
MIN_NAME_LEN = 6
MARKET_WIDE = re.compile(r"\b(nifty|sensex|stock markets?|markets? (?:crash|rally|fall|slump|surge|tumble|plunge))\b",
                         re.IGNORECASE)


def store_filing(s: Session, f: Filing) -> m.SourceDocument:
    text = f"{f.category}. {f.summary}".strip()
    return repo.save_document(s, url=f.url, symbol=f.symbol, kind="filing", category=f.category,
                              title=f"{f.company}: {f.category}", text=text, published_at=f.published_at,
                              tier=Tier.OFFICIAL, publisher="NSE")


def filing_event(s: Session, f: Filing, doc: m.SourceDocument) -> m.Event | None:
    """Clear-cut filings become scored events now; ambiguous ones become neutral events the AI can read later."""
    clear = filings.classify_filing(f.category)
    if clear:
        etype, polarity = clear
        title = f.summary[:200] or f.category
    elif filings.needs_reading(f.category):
        etype, polarity, title = EventType.OTHER, 0, f"{f.category}: {f.summary[:160]}"
    else:
        return None
    return repo.upsert_event(s, key=f"filing:{f.url}", symbol=f.symbol, event_type=etype, polarity=polarity,
                             title=title, event_date=f.published_at.date(), documents=[doc], origin="rule")


def collect_filings(ctx: JobContext, detail: list[str], start: date, end: date) -> None:
    c = ctx.c
    rows = c.exchange.announcements(start, end)
    with session_scope(c.sessions) as s:
        universe = set(repo.universe_symbols(s))
        held = set(repo.holding_symbols(s))
        latest = s.scalar(select(m.ScreenCandidate.screen_date).order_by(m.ScreenCandidate.screen_date.desc()).limit(1))
        candidates: set[str] = set()
        if latest:
            candidates = set(s.scalars(select(m.ScreenCandidate.symbol).where(m.ScreenCandidate.screen_date == latest)))
        stored = queued = 0
        for f in rows:
            if f.symbol not in universe and f.symbol not in held:
                continue
            if not filings.is_material(f.category):
                continue
            doc = store_filing(s, f)
            ev = filing_event(s, f, doc)
            stored += 1
            if f.symbol in held:
                queued += repo.enqueue(s, f.symbol, f"New filing on a holding: {f.category}", priority=10)
            elif ev is not None and ev.polarity > 0:
                queued += repo.enqueue(s, f.symbol, f"Positive filing: {f.category}", priority=20)
            elif f.symbol in candidates:
                queued += repo.enqueue(s, f.symbol, f"New filing on a candidate: {f.category}", priority=25)
    detail.append(f"{len(rows)} filings on NSE, {stored} material on tracked stocks, {queued} research requests")


def _name_patterns(names: dict[str, str]) -> list[tuple[str, re.Pattern[str]]]:
    from stockresearch.adapters.news import short_name

    out = []
    for sym, name in names.items():
        short = short_name(name)
        if len(short) >= MIN_NAME_LEN and " " in short:
            out.append((sym, re.compile(rf"\b{re.escape(short)}\b", re.IGNORECASE)))
    return out


def collect_press(ctx: JobContext, detail: list[str]) -> None:
    c = ctx.c
    articles = c.news.market_feeds()
    with session_scope(c.sessions) as s:
        names = {st.symbol: st.name for st in s.scalars(select(m.Stock).where(m.Stock.in_universe.is_(True)))}
        patterns = _name_patterns(names)
        stored = blocked = market_wide = 0
        for a in articles:
            tier, publisher = sources.classify(a.publisher_url or a.url, a.title)
            if tier == Tier.BLOCKED:
                blocked += 1
                continue
            haystack = f"{a.title} {a.summary}"
            for sym, pat in patterns:
                if pat.search(haystack):
                    repo.save_document(s, url=a.url, symbol=sym, kind="article", category=None, title=a.title,
                                       text=f"{a.title}. {a.summary}".strip(), published_at=a.published_at,
                                       tier=tier, publisher=publisher)
                    stored += 1
                    break
            else:
                if tier == Tier.PRESS and MARKET_WIDE.search(a.title):
                    repo.save_document(s, url=a.url, symbol=None, kind="article", category=MARKET_CATEGORY,
                                       title=a.title, text=f"{a.title}. {a.summary}".strip(),
                                       published_at=a.published_at, tier=tier, publisher=publisher)
                    market_wide += 1
    detail.append(f"{len(articles)} headlines from approved outlets, {stored} matched to tracked stocks, "
                  f"{market_wide} about the whole market, {blocked} blocked")


MAX_LOOKBACK_DAYS = 5


def collection_start(today: date, hour: int, last_ok: date | None) -> date:
    """First day of filings to fetch. Normally today (yesterday before 10 am). After the server was switched off,
    go back to the day of the last successful run, so filings announced meanwhile are not missed. Filings are
    de-duplicated, so looking again is harmless."""
    start = today - timedelta(days=1) if hour < 10 else today
    if last_ok is not None:
        start = min(start, last_ok)
    return max(start, today - timedelta(days=MAX_LOOKBACK_DAYS))


def collect(ctx: JobContext) -> str:
    today = repo.today_ist()
    with session_scope(ctx.c.sessions) as s:
        last_ok = s.scalar(select(func.max(m.Run.finished_at)).where(m.Run.job == "collect", m.Run.status == "ok"))
    start = collection_start(today, repo.now_ist().hour, last_ok.date() if last_ok else None)
    ctx.soft_stage("Exchange filings", lambda d: collect_filings(ctx, d, start, today))
    ctx.soft_stage("Approved press headlines", lambda d: collect_press(ctx, d))
    if len(ctx.notes) >= 2:
        raise RuntimeError("Both collection sources failed: " + "; ".join(ctx.notes))
    return "Collection done" + (" (partial)" if ctx.notes else "")
