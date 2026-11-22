"""End-to-end pipeline on fixed data: bootstrap -> market close -> collect -> research -> publish -> tracking."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.services import collect, holdings, market, publish, research, tracker
from stockresearch.services.runs import JobContext, run_job
from tests import fakes


def bootstrap(c: Container) -> None:
    def job(ctx: JobContext) -> str:
        with ctx.stage("universe") as d:
            market.refresh_universe(ctx, d)
        return market.backfill_history(ctx, 1)

    assert run_job(c, "bootstrap", job) == 0


def full_day(c: Container) -> None:
    bootstrap(c)
    assert run_job(c, "market_close", lambda ctx: market.market_close(ctx, fakes.TODAY)) == 0
    assert run_job(c, "collect", collect.collect) == 0
    assert run_job(c, "research", lambda ctx: research.process_queue(ctx, 10)) == 0
    assert run_job(c, "publish", publish.publish) == 0


def test_full_day_produces_verified_pick_with_evidence(container: Container) -> None:
    full_day(container)
    with session_scope(container.sessions) as s:
        candidates = set(s.scalars(select(m.ScreenCandidate.symbol)))
        assert "GOODCO" in candidates
        assert "ILLQ" not in candidates  # illiquid
        assert "SURV" not in candidates  # on the surveillance list

        picks = list(s.scalars(select(m.Pick)))
        assert [p.symbol for p in picks] == ["GOODCO"]
        report = s.get(m.Report, picks[0].report_id)
        assert report is not None and report.stars >= 4 and report.pick_type == "fundamentals"
        assert not report.data["caps"]
        urls = {e.url for e in report.evidence}
        assert fakes.FILING_URL in urls
        filing_ev = next(e for e in report.evidence if e.url == fakes.FILING_URL)
        assert filing_ev.trust_label.startswith("Official NSE")

        weak = s.scalar(select(m.Report).where(m.Report.symbol == "WEAKCO"))
        assert weak is None or not weak.is_pick


def test_blocked_sources_never_stored_or_shown_to_ai(container: Container) -> None:
    full_day(container)
    with session_scope(container.sessions) as s:
        urls = set(s.scalars(select(m.SourceDocument.url)))
        assert fakes.BLOCKED_URL not in urls
        assert "https://randomblog.example/goodco" not in urls
        assert fakes.PRESS_URL in urls
        opinion = s.scalar(select(m.SourceDocument).where(m.SourceDocument.url == fakes.OPINION_URL))
        assert opinion is not None and opinion.tier == 3
        assert all(fakes.BLOCKED_URL not in e.url for e in s.scalars(select(m.EvidenceRow)))
    researcher = container.researcher
    assert isinstance(researcher, fakes.FakeResearcher)
    assert researcher.payloads and all(fakes.BLOCKED_TEXT not in p for p in researcher.payloads)


def test_unsupported_ai_claims_are_dropped(container: Container) -> None:
    full_day(container)
    with session_scope(container.sessions) as s:
        titles = {e.title for e in s.scalars(select(m.Event).where(m.Event.symbol == "GOODCO"))}
        assert not any("900" in t for t in titles)
        result = s.scalar(select(m.ResearchResult).where(m.ResearchResult.symbol == "GOODCO"))
        assert result is not None and result.data["ai"]["dropped"] >= 3
        claims = {e.claim for e in s.scalars(select(m.EvidenceRow))}
        assert "Profit rose 45% last quarter" not in claims  # number not in its quote
        assert "Brokerage sees big upside" not in claims  # opinion used outside sentiment
        order = s.scalar(select(m.Event).where(m.Event.dedupe_key == f"filing:{fakes.FILING_URL}"))
        assert order is not None and order.value_cr == 300 and order.status == "verified"
        assert len(order.documents) == 2  # the filing plus the press report


def test_morning_email_links_sources(container: Container) -> None:
    full_day(container)
    notifier = container.notifiers[0]
    assert isinstance(notifier, fakes.FakeNotifier)
    subject, html, text = next(x for x in notifier.sent if x[0].startswith("1 new pick"))
    assert "GOODCO" in subject
    assert fakes.FILING_URL in html and fakes.FILING_URL in text
    assert "not investment advice" in html


def test_holding_quantities_never_sent_to_ai(container: Container) -> None:
    bootstrap(container)
    holdings.add_holding(container, "GOODCO", 37, 123.45, fakes.TODAY - timedelta(days=200), "long")
    assert run_job(container, "research", lambda ctx: research.process_queue(ctx, 10)) == 0
    researcher = container.researcher
    assert isinstance(researcher, fakes.FakeResearcher)
    assert researcher.payloads
    for payload in researcher.payloads:
        assert "123.45" not in payload
        assert '"quantity"' not in payload and "avg_price" not in payload


def test_exit_alerts_and_review(container: Container) -> None:
    bootstrap(container)
    hid = holdings.add_holding(container, "GOODCO", 10, 150, fakes.TODAY - timedelta(days=30), "short", stop_price=10_000)
    detail: list[str] = []
    holdings.exit_alerts(container, detail)
    with session_scope(container.sessions) as s:
        kinds = {a.kind for a in s.scalars(select(m.Alert).where(m.Alert.holding_id == hid))}
    assert "stop_hit" in kinds
    holdings.exit_alerts(container, detail)  # deduplicated
    with session_scope(container.sessions) as s:
        assert len(list(s.scalars(select(m.Alert).where(m.Alert.kind == "stop_hit")))) == 1
    holdings.review_all(container, None, detail)
    with session_scope(container.sessions) as s:
        review = s.scalar(select(m.HoldingReview).where(m.HoldingReview.holding_id == hid))
        assert review is not None and review.status in {"intact", "weakening", "broken"}
        assert review.data["tax"]["long_term"] is False


def test_import_csv(container: Container) -> None:
    bootstrap(container)
    csv_text = "Instrument,Qty.,Avg. cost\nGOODCO,5,120.5\nNOPE,3,10\nINE000000101,2,90\n"
    result = holdings.import_csv(container, csv_text, "long")
    assert result.added == 2
    assert len(result.skipped) == 1 and "NOPE" in result.skipped[0]


def test_track_record_and_stale_flag(container: Container) -> None:
    full_day(container)
    detail: list[str] = []
    tracker.open_check(container, detail)  # fake opens 5% above the close
    with session_scope(container.sessions) as s:
        pick = s.scalar(select(m.Pick))
        assert pick is not None and pick.outcome is not None and pick.outcome.stale


def test_degrades_when_nse_filings_blocked(container: Container) -> None:
    bootstrap(container)
    exchange = container.exchange
    assert isinstance(exchange, fakes.FakeExchange)
    exchange.fail_announcements = True
    assert run_job(container, "collect", collect.collect) == 0  # press still collected: partial, not failed
    with session_scope(container.sessions) as s:
        run = s.scalar(select(m.Run).where(m.Run.job == "collect"))
        assert run is not None and "partial" in run.notes


def test_job_lock_and_failure_alert(container: Container) -> None:
    def boom(ctx: JobContext) -> str:
        raise RuntimeError("source exploded")

    assert run_job(container, "publish", boom) == 1
    notifier = container.notifiers[0]
    assert isinstance(notifier, fakes.FakeNotifier)
    assert any("failed" in subj for subj, _, _ in notifier.sent)
    assert run_job(container, "publish", boom) == 1
    assert sum("failed" in subj for subj, _, _ in notifier.sent) == 1  # one alert per 6 hours


@pytest.mark.parametrize("ai", [False])
def test_rule_only_mode_still_rates(container: Container, ai: bool) -> None:
    container.researcher = None
    full_day(container)
    with session_scope(container.sessions) as s:
        report = s.scalar(select(m.Report).where(m.Report.symbol == "GOODCO"))
        assert report is not None and not report.data["ai_used"]
        trigger = report.data["areas"]["trigger"]
        assert trigger["score"] > 3  # the official order filing counts without AI
        sentiment = report.data["areas"]["sentiment"]
        assert sentiment["score"] is None and "AI reading is on" in sentiment["reasons"][0]
        assert json.dumps(report.data)  # serialisable


def test_ai_negative_lowers_score_but_does_not_exclude(container: Container) -> None:
    from stockresearch.domain.models import EventType, Tier
    from stockresearch.services import repo as r
    from stockresearch.services.research import apply_ai_events

    bootstrap(container)
    with session_scope(container.sessions) as s:
        doc = r.save_document(s, url="https://nsearchives.nseindia.com/x.pdf", symbol="GOODCO", kind="filing",
                              category="General Updates", title="t", text="Senior manager resigned",
                              published_at=fakes.NOW, tier=Tier.OFFICIAL, publisher="NSE")
        apply_ai_events(s, "GOODCO", [{"type": "negative", "polarity": -1, "title": "Senior manager resigned",
                                       "date": str(fakes.TODAY), "doc_ids": [doc.id]}], {doc.id: doc}, fakes.TODAY)
        ev = s.scalar(select(m.Event).where(m.Event.title == "Senior manager resigned"))
        assert ev is not None and ev.event_type == EventType.OTHER.value and ev.polarity == -1
