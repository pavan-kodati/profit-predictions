"""Plan v4: when the server may switch itself off, and the filings catch-up after it was off."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.services import collect, idle, market, repo
from stockresearch.services.runs import JobContext, run_job
from stockresearch.web.app import create_app
from tests import fakes

WEDNESDAY_NIGHT = datetime(2026, 11, 11, 21, 30)
WEDNESDAY_NOON = datetime(2026, 11, 11, 12, 0)
SATURDAY = datetime(2026, 11, 14, 11, 0)


class TestDecision:
    def ok(self, now: datetime = WEDNESDAY_NIGHT, **kw: object) -> idle.Decision:
        args: dict[str, object] = {"uptime_min": 120.0, "running_jobs": 0, "queue_items": 0, "last_visit": None}
        args.update(kw)
        return idle.decide(now, **args)  # type: ignore[arg-type]

    def test_idle_in_the_evening_is_fine(self) -> None:
        assert self.ok().ok

    def test_idle_at_the_weekend_is_fine(self) -> None:
        assert self.ok(SATURDAY).ok

    def test_never_inside_the_weekday_work_window(self) -> None:
        d = self.ok(WEDNESDAY_NOON)
        assert not d.ok and "work window" in d.reason
        assert not self.ok(datetime(2026, 11, 11, 7, 30)).ok and self.ok(datetime(2026, 11, 11, 7, 0)).ok is True
        assert not self.ok(datetime(2026, 11, 11, 20, 29)).ok and self.ok(datetime(2026, 11, 11, 20, 30)).ok

    def test_not_right_after_a_start_or_when_uptime_is_unknown(self) -> None:
        assert "started only" in self.ok(uptime_min=5.0).reason
        assert not self.ok(uptime_min=None).ok

    def test_waits_for_jobs_and_the_research_queue(self) -> None:
        assert "job(s) are still running" in self.ok(running_jobs=1).reason
        assert "waiting to be researched" in self.ok(queue_items=3).reason

    def test_waits_while_the_user_is_on_the_website(self) -> None:
        recent = WEDNESDAY_NIGHT - timedelta(minutes=10)
        assert "used the website" in self.ok(last_visit=recent).reason
        assert self.ok(last_visit=WEDNESDAY_NIGHT - timedelta(minutes=31)).ok


class TestCheckFromTheDatabase:
    def check(self, c: Container, now: datetime = WEDNESDAY_NIGHT) -> idle.Decision:
        return idle.check(c.settings, c.sessions, now=now, uptime_min=120.0)

    def test_empty_database_is_idle(self, container: Container) -> None:
        assert self.check(container).ok

    def test_recent_queue_item_blocks_but_an_old_one_does_not(self, container: Container) -> None:
        with session_scope(container.sessions) as s:
            s.add(m.ResearchQueueItem(symbol="GOODCO", reason="x", priority=1, status="pending",
                                      requested_at=WEDNESDAY_NIGHT - timedelta(hours=1)))
        assert not self.check(container).ok
        with session_scope(container.sessions) as s:
            item = s.query(m.ResearchQueueItem).one()
            item.requested_at = WEDNESDAY_NIGHT - timedelta(hours=7)
        assert self.check(container).ok  # stuck for hours: it no longer keeps the server on

    def test_running_job_blocks_but_a_leftover_does_not(self, container: Container) -> None:
        with session_scope(container.sessions) as s:
            s.add(m.Run(job="research", started_at=WEDNESDAY_NIGHT - timedelta(minutes=10), status="running", notes=""))
        assert not self.check(container).ok
        with session_scope(container.sessions) as s:
            s.query(m.Run).one().started_at = WEDNESDAY_NIGHT - timedelta(hours=5)
        assert self.check(container).ok

    def test_visit_file_blocks(self, container: Container) -> None:
        idle.touch_visit(container.settings.data_dir, WEDNESDAY_NIGHT - timedelta(minutes=5))
        assert not self.check(container).ok


class TestVisits:
    def test_written_at_most_once_a_minute(self, tmp_path: Path) -> None:
        t0 = datetime(2026, 11, 11, 21, 0)
        idle.touch_visit(tmp_path, t0)
        idle.touch_visit(tmp_path, t0 + timedelta(seconds=30))
        assert idle.read_last_visit(tmp_path) == t0
        idle.touch_visit(tmp_path, t0 + timedelta(seconds=61))
        assert idle.read_last_visit(tmp_path) == t0 + timedelta(seconds=61)

    def test_missing_or_broken_file_means_no_visit(self, tmp_path: Path) -> None:
        assert idle.read_last_visit(tmp_path) is None
        (tmp_path / idle.VISIT_FILE).write_text("not a date")
        assert idle.read_last_visit(tmp_path) is None

    def test_only_signed_in_page_views_count(self, container: Container) -> None:
        client = TestClient(create_app(container), base_url="http://testserver")
        client.get("/login")  # anyone on the internet can open this, so it must not keep the server on
        client.get("/health")
        assert idle.read_last_visit(container.settings.data_dir) is None
        client.post("/login", data={"username": "pavan", "password": "correct-horse-battery"})
        client.get("/how-it-works")
        assert idle.read_last_visit(container.settings.data_dir) is not None


class TestFilingsCatchUp:
    today = date(2026, 11, 16)  # a Monday

    def test_normal_hourly_run_is_unchanged(self) -> None:
        assert collect.collection_start(self.today, 14, self.today) == self.today
        assert collect.collection_start(self.today, 8, self.today) == self.today - timedelta(days=1)
        assert collect.collection_start(self.today, 14, None) == self.today

    def test_after_a_weekend_off_it_goes_back_to_the_last_successful_run(self) -> None:
        friday = date(2026, 11, 13)
        assert collect.collection_start(self.today, 8, friday) == friday

    def test_never_looks_back_more_than_five_days(self) -> None:
        assert collect.collection_start(self.today, 8, date(2026, 10, 1)) == self.today - timedelta(days=5)

    def test_collect_picks_up_a_weekend_filing(self, container: Container, monkeypatch: pytest.MonkeyPatch) -> None:
        """The last good run was Friday evening; a filing was announced on Saturday; Monday's first run finds it."""
        monkeypatch.setattr(repo, "today_ist", lambda: date(2026, 11, 16))
        monkeypatch.setattr(repo, "now_ist", lambda: datetime(2026, 11, 16, 7, 40))
        def job(ctx: JobContext) -> str:
            with ctx.stage("universe") as d:
                market.refresh_universe(ctx, d)
            return "ok"

        assert run_job(container, "bootstrap", job) == 0
        assert isinstance(container.exchange, fakes.FakeExchange)
        container.exchange.filings.append(fakes.Filing(
            "GOODCO", "Good Company Limited", "Bagging/Receiving of orders/contracts",
            "Good Company Limited has received an order worth Rs 120 crore.",
            "https://nsearchives.nseindia.com/corporate/GOODCO_weekend.pdf", datetime(2026, 11, 14, 11, 0)))
        with session_scope(container.sessions) as s:
            s.add(m.Run(job="collect", started_at=datetime(2026, 11, 13, 21, 0), finished_at=datetime(2026, 11, 13, 21, 1),
                        status="ok", notes=""))
        assert run_job(container, "collect", collect.collect) == 0
        with session_scope(container.sessions) as s:
            urls = {d.url for d in s.query(m.SourceDocument)}
        assert "https://nsearchives.nseindia.com/corporate/GOODCO_weekend.pdf" in urls
