"""Build a rated report for one stock from stored data: scores, caps, label, and checkable evidence (plan 2.4, 3)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import capgroup, corporate_actions, gates, rating, scoring, verification
from stockresearch.domain.models import TIER_LABEL, AreaScore, PickType, Tier, Verification
from stockresearch.services import repo

EVIDENCE_LIMIT = 25


def _evidence(doc: m.SourceDocument, area: str, stance: str, claim: str, quote: str,
              label: str | None = None) -> dict[str, Any]:
    return {
        "document_id": doc.id, "area": area, "stance": stance, "claim": claim[:500], "quote": quote[:600],
        "url": doc.url, "publisher": doc.publisher, "tier": doc.tier,
        "trust_label": label or verification.trust_label([repo.source_ref(doc)]),
        "published_at": doc.published_at,
    }


def _snippet(doc: m.SourceDocument, n: int = 300) -> str:
    text = (doc.text or doc.title).strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " ..."


def collect_evidence(s: Session, symbol: str, today: date, ai: dict[str, Any] | None,
                     ownership_score: float | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    def add(item: dict[str, Any]) -> None:
        key = (item["document_id"], item["claim"][:80])
        if key not in seen:
            seen.add(key)
            out.append(item)

    since = today - timedelta(days=scoring.NEGATIVE_WINDOW_DAYS)
    for ev in s.scalars(select(m.Event).where(m.Event.symbol == symbol, m.Event.event_date >= since,
                                              m.Event.polarity != 0).order_by(m.Event.event_date.desc())):
        if ev.status == Verification.UNVERIFIED.value or not ev.documents:
            continue
        doc = sorted(ev.documents, key=lambda d: d.tier)[0]
        label = verification.trust_label(repo.source_ref(d) for d in ev.documents)
        add(_evidence(doc, "trigger", "for" if ev.polarity > 0 else "against",
                      f"{ev.event_date:%d %b %Y}: {ev.title}", _snippet(doc), label))

    for e in (ai or {}).get("evidence", []):
        ai_doc = s.get(m.SourceDocument, e["doc_id"])
        if ai_doc is not None:
            add(_evidence(ai_doc, e["area"], e["stance"], e["claim"], e["quote"]))
    ob = (ai or {}).get("order_book")
    if ob:
        ob_doc = s.get(m.SourceDocument, ob["doc_id"])
        if ob_doc is not None:
            add(_evidence(ob_doc, "order_book", "for", f"Pending orders of about Rs {ob['value_cr']:,.0f} crore", ob["quote"]))

    shp = s.scalar(select(m.SourceDocument).where(m.SourceDocument.symbol == symbol,
                                                  m.SourceDocument.kind == "shareholding")
                   .order_by(m.SourceDocument.published_at.desc()).limit(1))
    if shp is not None:
        add(_evidence(shp, "ownership", "for" if (ownership_score or 3) >= 3 else "against", shp.title, shp.text))
    for d in s.scalars(select(m.SourceDocument).where(
            m.SourceDocument.symbol == symbol, m.SourceDocument.kind == "deal",
            m.SourceDocument.category == "institution").order_by(m.SourceDocument.published_at.desc()).limit(5)):
        add(_evidence(d, "ownership", "for" if " bought " in d.text else "against", d.title, d.text))
    # Strongest first: verified official evidence, then press, opinion last; "against" items are kept, not hidden.
    out.sort(key=lambda e: (e["tier"] if e["tier"] != Tier.OPINION else 9, e["stance"] != "for"))
    return out[:EVIDENCE_LIMIT]


def build(s: Session, c: Container, symbol: str, long_term: bool = False) -> dict[str, Any] | None:
    """Compute the report contents. Returns None if there is not enough price history."""
    today = repo.today_ist()
    stock = s.get(m.Stock, symbol)
    bars = repo.load_bars(s, [symbol], days=420).get(symbol)
    t = repo.technicals_for(bars, stock.price_band if stock else None)
    if t is None:
        return None
    research = repo.latest_research(s, symbol)
    rdata: dict[str, Any] = research.data if research else {}
    ai: dict[str, Any] | None = rdata.get("ai")
    f = repo.fundamentals_for(s, symbol)
    o = repo.ownership_for(s, symbol, today, rdata, f.institutional_pct)
    events = repo.event_facts(s, symbol, today - timedelta(days=90))
    next_results = date.fromisoformat(rdata["next_results"]) if rdata.get("next_results") else None

    stances = (ai or {}).get("stances", [])
    areas: dict[str, AreaScore] = {
        "quality": scoring.score_quality(f),
        "price": scoring.score_price(t),
        "trigger": scoring.score_trigger(events, f.annual_revenue_cr, today),
        "order_book": scoring.score_order_book(((ai or {}).get("order_book") or {}).get("value_cr"), events,
                                               f.annual_revenue_cr, today),
        "ownership": scoring.score_ownership(o),
        "sentiment": scoring.score_sentiment([x["stance"] for x in stances if x["tier"] == Tier.PRESS],
                                             [x["stance"] for x in stances if x["tier"] == Tier.OPINION]),
    }
    if research is None:
        areas["trigger"].reasons.insert(0, "This stock has not been researched yet; only stored filings were checked")
    if ai is None:
        since = datetime.combine(today - timedelta(days=14), datetime.min.time())
        articles = s.scalar(select(func.count()).select_from(m.SourceDocument).where(
            m.SourceDocument.symbol == symbol, m.SourceDocument.kind == "article",
            m.SourceDocument.published_at >= since)) or 0
        if articles:
            areas["sentiment"] = AreaScore(None, [
                f"{articles} article(s) from approved outlets found in 14 days, but their tone is only judged when "
                "AI reading is on (GEMINI_API_KEY)"])
    actions = repo.load_actions(s, [symbol]).get(symbol, [])
    gate = gates.check(t, o, repo.surveillance_for(s, symbol), next_results, today,
                       c.settings.min_turnover_cr, c.settings.max_pledge_pct,
                       corporate_actions.price_warning(actions, today))
    best = scoring.best_positive_status(events, today)
    negatives = scoring.verified_negatives(events, today)
    ai_pick = PickType(ai["pick_type"]) if ai and ai.get("pick_type") in {p.value for p in PickType} else None
    r = rating.rate(areas, t, gate, best, bool(negatives),
                    rating.AiSignals(future_story=bool((ai or {}).get("future_story")), ai_pick_type=ai_pick),
                    long_term=long_term)
    evidence = collect_evidence(s, symbol, today, ai, areas["ownership"].score)
    return {
        "symbol": symbol,
        "name": stock.name if stock else symbol,
        "industry": stock.industry if stock else None,
        "rating": r,
        "evidence": evidence,
        "data": {
            "areas": {k: {"score": v.score, "reasons": v.reasons, "label": rating.AREA_LABEL[k],
                          "weight": rating.WEIGHTS[k]} for k, v in areas.items()},
            "caps": r.caps,
            "gates": {"passed": gate.passed, "failures": gate.failures, "flags": gate.flags},
            "tech": asdict(t),
            "levels": rating.levels(t),
            "price_adjustments": corporate_actions.describe(actions, today - timedelta(days=420)),
            "fundamentals": asdict(f),
            "ownership": asdict(o),
            "hold_guide": r.hold_guide,
            "long_term": long_term,
            "thesis": (ai or {}).get("thesis") or "",
            "track_record": (ai or {}).get("track_record") or "",
            "risks": (ai or {}).get("risks") or [],
            "confidence": (ai or {}).get("confidence"),
            "ai_used": ai is not None,
            "ai_dropped": (ai or {}).get("dropped", 0),
            "blocked_news": rdata.get("blocked_news", 0),
            "research_at": research.created_at if research else None,
            "research_notes": rdata.get("notes", []),
            "next_results": rdata.get("next_results"),
            "not_found": [f"{rating.AREA_LABEL[k]}: {v.reasons[0] if v.reasons else 'not found'}"
                          for k, v in areas.items() if v.score is None],
            "verified_negatives": [{"date": e.event_date, "title": e.title} for e in negatives],
            "tier_labels": {int(k): v for k, v in TIER_LABEL.items()},
            "cap_group": capgroup.resolve(stock.cap_group if stock else None, f.market_cap_cr),
        },
    }


def save(s: Session, built: dict[str, Any], purpose: str, run_id: int | None) -> m.Report:
    r: rating.Rating = built["rating"]
    tech = built["data"]["tech"]
    report = m.Report(
        run_id=run_id, symbol=built["symbol"], created_at=repo.now_ist(), report_date=repo.today_ist(),
        data_as_of=tech["as_of"], purpose=purpose, rating=r.overall, stars=r.stars, pick_type=r.pick_type.value,
        completeness=r.completeness, is_pick=False, excluded_reason=r.excluded_reason,
        data=_jsonable({**built["data"], "name": built["name"], "industry": built["industry"]}),
    )
    for e in built["evidence"]:
        report.evidence.append(m.EvidenceRow(**e))
    s.add(report)
    s.flush()
    return report


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_jsonable(v) for v in x]
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, float) and x != x:
        return None
    return x


def build_and_save(c: Container, symbol: str, purpose: str, run_id: int | None = None,
                   long_term: bool = False) -> int | None:
    with session_scope(c.sessions) as s:
        built = build(s, c, symbol, long_term=long_term)
        if built is None:
            return None
        return save(s, built, purpose, run_id).id
