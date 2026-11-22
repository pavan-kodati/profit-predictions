"""Research queue worker: gathers everything about one stock, has the AI read it, and validates what comes back.

Validation rules (plan section 2.3): every AI claim must quote a document we gave it, the quote must appear in our
saved copy, numbers in a claim must appear in its quote, and opinion documents may only inform sentiment.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from stockresearch.adapters.http import SourceUnavailable
from stockresearch.adapters.news import short_name
from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import filings, sources, verification
from stockresearch.domain.models import EventType, Tier
from stockresearch.ports import ResearchDocument
from stockresearch.services import repo
from stockresearch.services.runs import JobContext

log = logging.getLogger(__name__)
FILING_LOOKBACK_DAYS = 30
NEWS_LOOKBACK_DAYS = 14
MAX_PDFS = 4
MAX_DOCS_FOR_AI = 30
STALE_RUNNING = timedelta(hours=1)
_last_ai_call = 0.0


# ---------------------------------------------------------------- gather

def _gather_fundamentals(c: Container, symbol: str, notes: list[str]) -> None:
    try:
        data = c.market.fundamentals(symbol)
    except SourceUnavailable as e:
        notes.append(str(e))
        return
    with session_scope(c.sessions) as s:
        s.add(m.FundamentalsSnapshot(symbol=symbol, fetched_at=repo.now_ist(), source="yahoo",
                                     data={"info": data.info, "annual": [[str(d), r, p] for d, r, p in data.annual]}))
        for period, rev, profit in data.quarters:
            row = s.get(m.QuarterlyResult, (symbol, period))
            if row is None:
                s.add(m.QuarterlyResult(symbol=symbol, period_end=period, revenue_cr=rev, profit_cr=profit, source="yahoo"))
            else:
                row.revenue_cr, row.profit_cr = rev, profit


def _gather_ownership(c: Container, symbol: str, today: date, notes: list[str]) -> float | None:
    """Store shareholding, pledges and insider trades; create red-flag events. Returns insider net buying (crore)."""
    try:
        rows = c.exchange.shareholding(symbol)
        pledge = c.exchange.pledge(symbol)
        trades = c.exchange.insider_trades(symbol)
    except SourceUnavailable as e:
        notes.append(f"NSE ownership data unavailable: {e}")
        return None
    net = 0.0
    with session_scope(c.sessions) as s:
        prev_pledge = s.scalar(select(m.ShareholdingQuarterly.pledged_pct).where(
            m.ShareholdingQuarterly.symbol == symbol, m.ShareholdingQuarterly.pledged_pct.is_not(None))
            .order_by(m.ShareholdingQuarterly.period_end.desc()).limit(1))
        for r in rows[:8]:
            row = s.get(m.ShareholdingQuarterly, (symbol, r.period_end))
            if row is None:
                row = m.ShareholdingQuarterly(symbol=symbol, period_end=r.period_end)
                s.add(row)
            row.promoter_pct, row.public_pct, row.source_url = r.promoter_pct, r.public_pct, r.url
            if r.url and r.promoter_pct is not None:
                repo.save_document(s, url=r.url, symbol=symbol, kind="shareholding", category="Shareholding pattern",
                                   title=f"Shareholding pattern as of {r.period_end:%d %b %Y}",
                                   text=f"Promoter and promoter group holding: {r.promoter_pct:.2f}% as of "
                                        f"{r.period_end:%d %b %Y}. Public holding: {r.public_pct or 0:.2f}%.",
                                   published_at=datetime.combine(r.period_end, datetime.min.time()),
                                   tier=Tier.OFFICIAL, publisher="NSE")
        if pledge.period_end and pledge.pledged_pct is not None:
            row = s.get(m.ShareholdingQuarterly, (symbol, pledge.period_end))
            if row is not None:
                row.pledged_pct = pledge.pledged_pct
            if prev_pledge is not None and pledge.pledged_pct - prev_pledge >= 5:
                url = f"https://www.nseindia.com/companies-listing/corporate-filings-pledged-data#{symbol}-{pledge.period_end}"
                doc = repo.save_document(s, url=url, symbol=symbol, kind="filing", category="Pledge",
                                         title=f"Promoter pledge rose to {pledge.pledged_pct:.1f}%",
                                         text=f"Promoter shares pledged: {pledge.pledged_pct:.2f}% (previously "
                                              f"{prev_pledge:.2f}%).",
                                         published_at=repo.now_ist(), tier=Tier.OFFICIAL, publisher="NSE")
                repo.upsert_event(s, key=f"pledge:{symbol}:{pledge.period_end}", symbol=symbol,
                                  event_type=EventType.NEGATIVE, polarity=-1, documents=[doc], origin="rule",
                                  title=f"Promoter pledge rose from {prev_pledge:.1f}% to {pledge.pledged_pct:.1f}%",
                                  event_date=today)
        for t in trades:
            if t.trade_date < today - timedelta(days=90) or "promoter" not in t.category.lower():
                continue
            value_cr = t.value_rs / 1e7
            net += value_cr if t.side == "buy" else -value_cr
            if t.trade_date < today - timedelta(days=30) or value_cr < 1:
                continue
            verb = "bought" if t.side == "buy" else "sold"
            url = t.url or ("https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"
                            f"#{symbol}-{t.trade_date}-{repo.slug(t.person, 30)}")
            doc = repo.save_document(s, url=url, symbol=symbol, kind="insider", category="Insider trading",
                                     title=f"{t.person} ({t.category}) {verb} shares",
                                     text=f"On {t.trade_date:%d %b %Y}, {t.person} ({t.category}) {verb} shares "
                                          f"worth Rs {value_cr:,.2f} crore.",
                                     published_at=datetime.combine(t.trade_date, datetime.min.time()),
                                     tier=Tier.OFFICIAL, publisher="NSE")
            repo.upsert_event(
                s, key=f"insider:{url}", symbol=symbol, origin="rule", documents=[doc], event_date=t.trade_date,
                event_type=EventType.STAKE_BUY if t.side == "buy" else EventType.NEGATIVE,
                polarity=1 if t.side == "buy" else -1, value_cr=round(value_cr, 2),
                title=f"Promoter {t.person} {verb} shares worth Rs {value_cr:,.1f} crore",
            )
    return round(net, 2)


def _gather_filings(c: Container, symbol: str, today: date, notes: list[str]) -> None:
    from stockresearch.services.collect import filing_event, store_filing

    try:
        rows = c.exchange.announcements(today - timedelta(days=FILING_LOOKBACK_DAYS), today, symbol=symbol)
    except SourceUnavailable as e:
        notes.append(f"NSE filings unavailable: {e}")
        return
    material = [f for f in rows if filings.is_material(f.category)]
    with session_scope(c.sessions) as s:
        docs = [(f, store_filing(s, f)) for f in material]
        for f, doc in docs:
            filing_event(s, f, doc)
        to_read = [(f.url, doc.id) for f, doc in docs if filings.needs_reading(f.category) and len(doc.text) < 600]
    for url, doc_id in to_read[:MAX_PDFS]:
        text = c.exchange.document_text(url)
        if text:
            with session_scope(c.sessions) as s:
                stored = s.get(m.SourceDocument, doc_id)
                if stored is not None and len(text) > len(stored.text):
                    stored.text = f"{stored.text}\n\n{text}"
                    stored.content_hash = repo.content_hash(stored.text)


def _results_rule(c: Container, symbol: str, today: date) -> None:
    """A fresh results filing becomes a results event, with its direction set by year-on-year quarterly profit."""
    with session_scope(c.sessions) as s:
        quarters = list(s.scalars(select(m.QuarterlyResult).where(m.QuarterlyResult.symbol == symbol)
                                  .order_by(m.QuarterlyResult.period_end.desc())))
        if not quarters:
            return
        latest = quarters[0]
        year_ago = next((q for q in quarters[1:] if abs((latest.period_end - q.period_end).days - 365) <= 20), None)
        now_profit = latest.profit_cr
        then_profit = year_ago.profit_cr if year_ago else None
        if year_ago is None or now_profit is None or then_profit is None or then_profit <= 0:
            return
        growth = now_profit / then_profit - 1
        if abs(growth) < 0.15:
            return
        since = today - timedelta(days=10)
        for ev in s.scalars(select(m.Event).where(m.Event.symbol == symbol, m.Event.event_date >= since,
                                                  m.Event.polarity == 0, m.Event.origin == "rule")):
            text = " ".join(f"{d.category} {d.text[:400]}" for d in ev.documents).lower()
            if "result" in text and ("financial result" in text or "board meeting" in text):
                ev.event_type = EventType.RESULTS.value
                ev.polarity = 1 if growth > 0 else -1
                ev.title = (f"Quarterly results filed for the quarter to {latest.period_end:%b %Y}: profit "
                            f"{growth * 100:+.0f}% year on year (Rs {now_profit:,.1f} crore vs Rs {then_profit:,.1f} crore; "
                            "figures from Yahoo Finance's copy of the results)")


def _gather_news(c: Container, symbol: str, name: str) -> int:
    """Store company articles from approved outlets. Returns how many items were blocked."""
    blocked = 0
    articles = c.news.company_news(name, NEWS_LOOKBACK_DAYS)
    with session_scope(c.sessions) as s:
        for a in articles:
            tier, publisher = sources.classify(a.publisher_url or a.url, a.title)
            if tier == Tier.BLOCKED or not a.url:
                blocked += 1
                continue
            repo.save_document(s, url=a.url, symbol=symbol, kind="article", category=None, title=a.title,
                               text=f"{a.title}. {a.summary}".strip(), published_at=a.published_at, tier=tier,
                               publisher=publisher)
    return blocked


def _documents_for_ai(s: Session, symbol: str, today: date) -> list[m.SourceDocument]:
    since = datetime.combine(today - timedelta(days=FILING_LOOKBACK_DAYS), datetime.min.time())
    docs = list(s.scalars(select(m.SourceDocument).where(
        m.SourceDocument.symbol == symbol, m.SourceDocument.published_at >= since,
        m.SourceDocument.kind.in_(("filing", "article", "deal", "insider")),
        m.SourceDocument.tier != int(Tier.BLOCKED)).order_by(m.SourceDocument.published_at.desc())))
    official = [d for d in docs if d.tier == Tier.OFFICIAL][: MAX_DOCS_FOR_AI // 2]
    others = [d for d in docs if d.tier != Tier.OFFICIAL][: MAX_DOCS_FOR_AI - len(official)]
    return official + others


def _facts(s: Session, symbol: str, today: date, insider_net: float | None) -> dict[str, Any]:
    """Public company facts for the AI. Never includes anything about the user's own holdings."""
    f = repo.fundamentals_for(s, symbol)
    t = repo.technicals_for(repo.load_bars(s, [symbol], days=420).get(symbol))
    shp = list(s.scalars(select(m.ShareholdingQuarterly).where(m.ShareholdingQuarterly.symbol == symbol)
                         .order_by(m.ShareholdingQuarterly.period_end.desc()).limit(4)))
    stock = s.get(m.Stock, symbol)
    return {
        "industry": stock.industry if stock else None,
        "price": None if t is None else {
            "close": t.close, "as_of": str(t.as_of), "return_5d": t.ret_5d, "return_1m": t.ret_20d,
            "return_3m": t.ret_60d, "return_1y": t.ret_250d, "volume_vs_normal": t.vol_ratio,
            "from_52w_high": t.from_high, "delivery_pct_5d": t.delivery_5d, "delivery_pct_usual": t.delivery_50d,
        },
        "financials": {
            "market_cap_cr": f.market_cap_cr, "pe": f.pe, "sales_growth_yoy": f.revenue_growth,
            "profit_growth_yoy": f.earnings_growth, "profit_margin": f.profit_margin, "debt_to_equity": f.debt_to_equity,
            "return_on_equity": f.roe, "sales_cagr_3y": f.revenue_cagr_3y, "profit_cagr_3y": f.profit_cagr_3y,
            "annual_sales_cr": f.annual_revenue_cr,
        },
        "quarters_cr": [{"period": str(q.period_end), "sales": q.revenue_cr, "profit": q.profit_cr} for q in f.quarters],
        "promoter_holding": [{"period": str(r.period_end), "pct": r.promoter_pct, "pledged_pct": r.pledged_pct} for r in shp],
        "insider_net_cr_90d": insider_net,
    }


# ---------------------------------------------------------------- AI validation

def _supports(doc: m.SourceDocument, company: str, value_cr: float | None) -> bool:
    """A secondary document counts as reporting the same event only if it names the company (and the value, if any)."""
    text = f"{doc.title} {doc.text}"
    if doc.tier == Tier.OFFICIAL:
        return True
    if company.lower() not in text.lower():
        return False
    return value_cr is None or verification.value_supported(value_cr, text)


def validate_findings(raw: dict[str, Any], docs: dict[int, m.SourceDocument], company: str) -> tuple[dict[str, Any], int]:
    """Keep only AI findings that are backed by an exact quote from a document we supplied. Returns (clean, dropped)."""
    dropped = 0

    def quoted(doc_id: int | None, quote: str | None) -> m.SourceDocument | None:
        if doc_id is None or not quote or doc_id not in docs:
            return None
        doc = docs[doc_id]
        return doc if verification.quote_in_document(quote, f"{doc.title}\n{doc.text}") else None

    events = []
    for e in raw.get("events") or []:
        doc = quoted(e.get("quote_doc_id"), e.get("quote"))
        try:
            etype = EventType(e.get("type"))
        except ValueError:
            etype = EventType.OTHER
        if (doc is None or doc.tier == Tier.OPINION
                or not verification.numbers_supported(e.get("title", ""), e["quote"])
                or not verification.value_supported(e.get("value_cr"), e["quote"])):
            dropped += 1
            continue
        support = {doc.id}
        for other in e.get("doc_ids") or []:
            if other in docs and docs[other].tier != Tier.OPINION and _supports(docs[other], company, e.get("value_cr")):
                support.add(other)
        events.append({**e, "type": etype.value, "doc_ids": sorted(support)})

    evidence = []
    for ev in raw.get("evidence") or []:
        doc = quoted(ev.get("doc_id"), ev.get("quote"))
        if doc is None or not verification.numbers_supported(ev.get("claim", ""), ev["quote"]) or (
                doc.tier == Tier.OPINION and ev.get("area") != "sentiment"):
            dropped += 1
            continue
        evidence.append(ev)

    order_book = None
    ob = raw.get("order_book") or {}
    if ob.get("value_cr") is not None:
        doc = quoted(ob.get("doc_id"), ob.get("quote"))
        if doc is not None and doc.tier != Tier.OPINION and verification.value_supported(ob["value_cr"], ob["quote"]):
            order_book = ob
        else:
            dropped += 1

    stances = [
        {"doc_id": st["doc_id"], "stance": st["stance"], "tier": docs[st["doc_id"]].tier}
        for st in raw.get("article_stances") or []
        if st.get("doc_id") in docs and docs[st["doc_id"]].tier in (Tier.PRESS, Tier.OPINION)
    ]
    clean = {
        "thesis": raw.get("thesis", ""), "track_record": raw.get("track_record", ""), "risks": raw.get("risks", []),
        "confidence": raw.get("confidence", "low"), "future_story": bool(raw.get("future_story")),
        "pick_type": raw.get("pick_type"), "events": events, "evidence": evidence, "order_book": order_book,
        "stances": stances, "dropped": dropped,
    }
    return clean, dropped


def apply_ai_events(
    s: Session, symbol: str, events: list[dict[str, Any]], docs: dict[int, m.SourceDocument], today: date,
) -> None:
    for e in events:
        etype = EventType(e["type"])
        # The AI may report bad news, but only clear-cut rule-based events can exclude a stock (plan 2.3: code
        # decides the rating). An AI "negative" therefore lowers the trigger score without being a red flag.
        if etype == EventType.NEGATIVE:
            etype = EventType.OTHER
        support = [docs[i] for i in e["doc_ids"] if i in docs]
        try:
            event_date = date.fromisoformat(e["date"]) if e.get("date") else today
        except ValueError:
            event_date = today
        filing_event = None
        for d in support:
            if d.kind == "filing":
                filing_event = s.scalar(select(m.Event).where(m.Event.dedupe_key == f"filing:{d.url}"))
                if filing_event:
                    break
        if filing_event is not None:
            repo.update_event_reading(filing_event, etype, int(e["polarity"]), e["title"], e.get("value_cr"), support)
        else:
            repo.upsert_event(s, key=f"ai:{symbol}:{etype.value}:{event_date}:{repo.slug(e['title'], 40)}",
                              symbol=symbol, event_type=etype, polarity=int(e["polarity"]), title=e["title"],
                              event_date=event_date, documents=support, origin="ai", value_cr=e.get("value_cr"))


def _ai_allowed(c: Container) -> bool:
    if c.researcher is None:
        return False
    with session_scope(c.sessions) as s:
        return repo.ai_calls_today(s) < c.settings.gemini_daily_call_budget


def _pace_ai(c: Container) -> None:
    global _last_ai_call
    wait = c.settings.gemini_min_interval_seconds - (time.monotonic() - _last_ai_call)
    if wait > 0:
        time.sleep(wait)
    _last_ai_call = time.monotonic()


# ---------------------------------------------------------------- one stock

def research_symbol(c: Container, symbol: str) -> int:
    """Full research pass on one stock. Returns the ResearchResult id."""
    today = repo.today_ist()
    notes: list[str] = []
    with session_scope(c.sessions) as s:
        name = repo.stock_name(s, symbol)
        have_history = len(repo.load_bars(s, [symbol], days=420).get(symbol, [])) >= 200
    if not have_history:
        frames = c.market.history([symbol], period="2y")
        with session_scope(c.sessions) as s:
            for sym, df in frames.items():
                repo.store_prices(s, repo.frame_to_rows(sym, df, "yahoo"))
    _gather_fundamentals(c, symbol, notes)
    insider_net = _gather_ownership(c, symbol, today, notes)
    _gather_filings(c, symbol, today, notes)
    _results_rule(c, symbol, today)
    blocked = _gather_news(c, symbol, name)
    try:
        next_results = c.exchange.next_results_date(symbol, today)
    except SourceUnavailable as e:
        notes.append(f"Results calendar unavailable: {e}")
        next_results = None

    with session_scope(c.sessions) as s:
        facts = _facts(s, symbol, today, insider_net)
        docs = _documents_for_ai(s, symbol, today)
        doc_map = {d.id: d for d in docs}
        payload = [ResearchDocument(d.id, d.publisher, d.tier, f"{d.published_at:%Y-%m-%d}" if d.published_at else "",
                                    d.title, d.text) for d in docs]

    ai: dict[str, Any] | None = None
    if _ai_allowed(c) and c.researcher is not None:
        _pace_ai(c)
        try:
            with session_scope(c.sessions) as s:
                repo.record_ai_call(s)
            raw = c.researcher.analyse(symbol, name, facts, payload)
            with session_scope(c.sessions) as s:
                live = {d.id: d for d in s.scalars(select(m.SourceDocument).where(m.SourceDocument.id.in_(doc_map)))}
                ai, dropped = validate_findings(raw, live, short_name(name))
                apply_ai_events(s, symbol, ai["events"], live, today)
            if dropped:
                log.info("dropped %d unsupported AI findings", dropped, extra={"symbol": symbol})
        except SourceUnavailable as e:
            notes.append(f"AI research unavailable: {e}")
    elif c.researcher is not None:
        notes.append("Daily AI budget used up; rule-based scores only")

    with session_scope(c.sessions) as s:
        result = m.ResearchResult(
            symbol=symbol, created_at=repo.now_ist(), ai_used=ai is not None,
            model=c.researcher.model_name if (ai is not None and c.researcher) else None,
            data={"facts": facts, "next_results": str(next_results) if next_results else None,
                  "document_ids": list(doc_map), "blocked_news": blocked, "notes": notes, "ai": ai},
        )
        s.add(result)
        s.flush()
        return result.id


# ---------------------------------------------------------------- queue

def process_queue(ctx: JobContext, max_items: int | None = None) -> str:
    from stockresearch.services import reports

    c = ctx.c
    limit = max_items or c.settings.research_batch_size
    with session_scope(c.sessions) as s:
        for stuck in s.scalars(select(m.ResearchQueueItem).where(
                m.ResearchQueueItem.status == "running",
                m.ResearchQueueItem.started_at < repo.now_ist() - STALE_RUNNING)):
            stuck.status = "pending"
        pending = list(s.scalars(select(m.ResearchQueueItem).where(m.ResearchQueueItem.status == "pending")
                                 .order_by(m.ResearchQueueItem.on_demand.desc(), m.ResearchQueueItem.priority,
                                           m.ResearchQueueItem.requested_at)))
        batch = [i for i in pending if i.on_demand] + [i for i in pending if not i.on_demand][:limit]
        ids = [i.id for i in batch]
    done = failed = skipped = 0
    for item_id in ids:
        with session_scope(c.sessions) as s:
            item = s.get(m.ResearchQueueItem, item_id)
            if item is None or item.status != "pending":
                continue
            recent = repo.latest_research(s, item.symbol, max_age_days=1)
            if recent is not None and not item.on_demand and recent.created_at > repo.now_ist() - timedelta(hours=12):
                item.status, item.finished_at = "done", repo.now_ist()
                skipped += 1
                continue
            item.status, item.started_at, item.attempts = "running", repo.now_ist(), item.attempts + 1
            symbol, on_demand = item.symbol, item.on_demand
        try:
            with ctx.stage(f"Research {symbol}") as detail:
                research_symbol(c, symbol)
                if on_demand:
                    reports.build_and_save(c, symbol, purpose="on_demand")
                detail.append("on demand" if on_demand else "queued")
            status, error = "done", None
            done += 1
        except Exception as e:
            log.exception("research failed", extra={"symbol": symbol})
            status, error = "failed", f"{type(e).__name__}: {e}"
            failed += 1
        with session_scope(c.sessions) as s:
            finished = s.get(m.ResearchQueueItem, item_id)
            if finished is not None:
                if status == "failed" and finished.attempts < 3:
                    finished.status = "pending"
                else:
                    finished.status = status
                finished.finished_at, finished.error = repo.now_ist(), error
    return f"Researched {done}, failed {failed}, skipped {skipped} (already fresh)"
