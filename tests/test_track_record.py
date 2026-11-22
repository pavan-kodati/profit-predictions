"""Plan v3 end to end: size groups, no repeat picks, rejected stocks, and why a pretend trade moved."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
from sqlalchemy import select

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.services import practice, publish, rejections, repo, reports
from stockresearch.services.runs import run_job
from tests import fakes
from tests.test_pipeline import full_day
from tests.test_practice_flow import add_days


def test_size_groups_come_from_the_official_lists(container: Container) -> None:
    full_day(container)
    with session_scope(container.sessions) as s:
        groups = {st.symbol: st.cap_group for st in s.scalars(select(m.Stock).where(m.Stock.in_universe.is_(True)))}
        assert groups["GOODCO"] == "large" and groups["WEAKCO"] == "mid" and groups["ILLQ"] == "small"
        report = s.scalar(select(m.Report).join(m.Pick, m.Pick.report_id == m.Report.id))
        assert report is not None and report.data["cap_group"] == "large"


def test_a_stock_picked_before_and_still_running_is_not_picked_again(container: Container) -> None:
    full_day(container)
    with session_scope(container.sessions) as s:
        first = s.scalar(select(m.Pick))
        assert first is not None
        first.pick_date = fakes.TODAY - timedelta(days=1)  # as if it had been picked yesterday, trade not yet bought
        pick_id = first.id
    assert run_job(container, "publish", publish.publish) == 0
    with session_scope(container.sessions) as s:
        today_picks = list(s.scalars(select(m.Pick).where(m.Pick.pick_date == repo.today_ist())))
        assert today_picks == []
        report = s.scalar(select(m.Report).where(m.Report.symbol == "GOODCO").order_by(m.Report.id.desc()))
        assert report is not None and not report.is_pick
        assert "still running" in report.data["held_back"]
        assert s.get(m.Pick, pick_id) is not None  # yesterday's pick stays in the record


def test_picked_again_only_after_it_finished_and_something_new_was_confirmed(container: Container) -> None:
    full_day(container)
    with session_scope(container.sessions) as s:
        pick = s.scalar(select(m.Pick))
        assert pick is not None
        ref = pick.ref_close
        pick.pick_date = fakes.TODAY - timedelta(days=1)
        for e in s.scalars(select(m.Event).where(m.Event.symbol == "GOODCO")):
            e.event_date = fakes.TODAY - timedelta(days=2)  # the confirmed news is older than the last pick
        pick_id = pick.id
        s.add(m.PracticeTrade(
            pick_id=pick_id, symbol="GOODCO", rules_version=2, pick_date=pick.pick_date, pick_type=pick.pick_type,
            rating=pick.rating, status="closed", amount=10000, entry_date=pick.pick_date, entry_price=ref,
            quantity=1, buy_value=ref, stop_price=ref * 0.9, hold_days=15, exit_date=fakes.TODAY - timedelta(days=1),
            exit_price=ref, exit_reason="window", net_pnl=0.0, created_at=fakes.NOW, updated_at=fakes.NOW))
    assert run_job(container, "publish", publish.publish) == 0
    with session_scope(container.sessions) as s:
        report = s.scalar(select(m.Report).where(m.Report.symbol == "GOODCO").order_by(m.Report.id.desc()))
        assert report is not None and not report.is_pick and "Nothing new" in report.data["held_back"]
        # a fresh confirmed event after the last pick lifts the block
        for e in s.scalars(select(m.Event).where(m.Event.symbol == "GOODCO")):
            e.event_date = fakes.TODAY
    assert run_job(container, "publish", publish.publish) == 0
    with session_scope(container.sessions) as s:
        assert [p.symbol for p in s.scalars(select(m.Pick).where(m.Pick.pick_date == repo.today_ist()))] == ["GOODCO"]


def test_a_full_size_group_holds_back_the_weaker_picks(container: Container) -> None:
    container.settings.picks_per_group = 1
    full_day(container)
    with session_scope(container.sessions) as s:
        top = s.scalar(select(m.Report).where(m.Report.is_pick.is_(True)))
        assert top is not None
        rows = [r for r in s.scalars(select(m.Report).where(m.Report.run_id == top.run_id)) if not r.is_pick]
        assert all("held_back" not in r.data or "filled its size group" in r.data["held_back"] or "still running"
                   in r.data["held_back"] for r in rows)


def test_rejected_stocks_are_followed_and_judged(container: Container) -> None:
    full_day(container)
    rid = reports.build_and_save(container, "WEAKCO", purpose="daily")
    assert rid is not None
    with session_scope(container.sessions) as s:
        report = s.get(m.Report, rid)
        assert report is not None and not report.is_pick
        assert rejections.record(s, [report], repo.today_ist(), 3.5) == 1
        assert rejections.record(s, [report], repo.today_ist(), 3.5) == 0  # not recorded twice
    with session_scope(container.sessions) as s:
        row = s.scalar(select(m.Rejection))
        assert row is not None and row.symbol == "WEAKCO" and row.reason and row.verdict is None
        ref = row.ref_close
        assert rejections.summarise(s).following == 1
    # the stock falls over the next 10 market days while the Nifty is flat: skipping it was right
    add_days(container, "WEAKCO", [(ref * (1 - 0.01 * i), ref * (1 - 0.01 * i), ref * (1 - 0.012 * i), ref * (1 - 0.01 * i))
                                   for i in range(1, 12)])
    rejections.update(container, [])
    with session_scope(container.sessions) as s:
        r = s.scalar(select(m.Rejection))
        assert r is not None and r.final and r.verdict == "right" and r.days_tracked == 10
        out = rejections.summarise(s)
        assert out.judged == 1 and out.right == 1 and out.following == 0


def test_every_trade_gets_a_reason_and_it_is_frozen_when_sold(container: Container) -> None:
    full_day(container)
    with session_scope(container.sessions) as s:
        ref = s.scalar(select(m.Pick.ref_close))
    assert ref is not None
    add_days(container, "GOODCO", [(ref, ref * 1.03, ref * 0.99, ref * 1.02), (ref * 1.02, ref * 1.07, ref * 1.01, ref * 1.06)])
    practice.sync(container, [])
    with session_scope(container.sessions) as s:
        t = s.scalar(select(m.PracticeTrade))
        assert t is not None and t.reason_key in {"business", "market", "sector", "hype", "flat", "unknown"} and t.reason_text
        assert t.status == "open"
    # make it end: the price opens below the safety level on the third trading day
    with session_scope(container.sessions) as s:
        stop = s.scalar(select(m.PracticeTrade.stop_price))
    assert stop is not None
    day = pd.bdate_range(fakes.TODAY + timedelta(days=3), periods=1)[0].date()
    with session_scope(container.sessions) as s:
        repo.store_prices(s, [
            {"symbol": "GOODCO", "date": day, "open": stop * 0.9, "high": stop * 0.9, "low": stop * 0.8, "close": stop * 0.85,
             "volume": 1e6, "delivery_pct": 50.0, "series": "EQ", "source": "nse"},
            {"symbol": "^NSEI", "date": day, "open": 24000.0, "high": 24100.0, "low": 23900.0, "close": 24050.0,
             "volume": 1e6, "delivery_pct": None, "series": None, "source": "yahoo"}])
    practice.sync(container, [])
    with session_scope(container.sessions) as s:
        t = s.scalar(select(m.PracticeTrade))
        assert t is not None and t.status == "closed" and t.exit_reason in {"gap", "stop"}
        assert t.reason_key and t.reason_text
        frozen = (t.reason_key, t.reason_text)
    practice.sync(container, [])
    with session_scope(container.sessions) as s:
        t = s.scalar(select(m.PracticeTrade))
        assert t is not None and (t.reason_key, t.reason_text) == frozen


def test_screen_still_shortlists_when_size_lists_were_never_loaded(container: Container) -> None:
    from stockresearch.adapters.http import SourceUnavailable

    def broken() -> dict[str, set[str]]:
        raise SourceUnavailable("NSE blocked")

    container.exchange.cap_lists = broken  # type: ignore[method-assign]
    full_day(container)
    with session_scope(container.sessions) as s:
        assert "GOODCO" in set(s.scalars(select(m.ScreenCandidate.symbol)))
        assert all(st.cap_group is None for st in s.scalars(select(m.Stock)))
        report = s.scalar(select(m.Report).join(m.Pick, m.Pick.report_id == m.Report.id))
        assert report is not None and report.data["cap_group"] in {"large", "mid", "small"}  # from market value
