"""Practice portfolio end to end, the new pages, plain-language wording, and the Friday email."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from stockresearch import presenters
from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.services import practice, repo
from stockresearch.web.app import create_app
from tests import fakes
from tests.test_pipeline import full_day


def add_days(c: Container, symbol: str, prices: list[tuple[float, float, float, float]]) -> None:
    """Store trading days after fakes.TODAY for one symbol (open, high, low, close), plus a flat Nifty."""
    days = pd.bdate_range(fakes.TODAY + timedelta(days=1), periods=len(prices))
    rows = []
    for d, (o, h, lo, cl) in zip(days, prices, strict=True):
        rows.append({"symbol": symbol, "date": d.date(), "open": o, "high": h, "low": lo, "close": cl, "volume": 1e6,
                     "delivery_pct": 50.0, "series": "EQ", "source": "nse"})
        rows.append({"symbol": "^NSEI", "date": d.date(), "open": 24000.0, "high": 24100.0, "low": 23900.0,
                     "close": 24050.0, "volume": 1e6, "delivery_pct": None, "series": None, "source": "yahoo"})
    with session_scope(c.sessions) as s:
        repo.store_prices(s, rows)


@pytest.fixture()
def after_pick(container: Container, monkeypatch: pytest.MonkeyPatch) -> Container:
    full_day(container)
    return container


def test_pick_becomes_practice_trade_at_next_open(after_pick: Container) -> None:
    c = after_pick
    with session_scope(c.sessions) as s:
        pick = s.scalar(select(m.Pick))
        assert pick is not None
        ref = pick.ref_close
    add_days(c, "GOODCO", [(ref * 1.01, ref * 1.03, ref * 1.0, ref * 1.02)])
    detail: list[str] = []
    practice.sync(c, detail)
    with session_scope(c.sessions) as s:
        t = s.scalar(select(m.PracticeTrade))
        assert t is not None and t.status == "open"
        assert t.entry_price == round(ref * 1.01, 2)
        assert t.quantity == max(int(c.settings.practice_amount // (ref * 1.01)), 1)  # every pick gets the same amount
        assert t.rules_version == 2 and t.hold_days == 15
    practice.sync(c, detail)  # idempotent
    with session_scope(c.sessions) as s:
        assert len(list(s.scalars(select(m.PracticeTrade)))) == 1


def test_window_end_closes_trade_with_costs(after_pick: Container) -> None:
    c = after_pick
    with session_scope(c.sessions) as s:
        ref = s.scalar(select(m.Pick.ref_close))
    assert ref is not None
    prices = [(ref * (1 + 0.004 * i), ref * (1 + 0.004 * i) * 1.01, ref * (1 + 0.004 * i) * 0.995, ref * (1 + 0.004 * i))
              for i in range(1, 17)]
    add_days(c, "GOODCO", prices)
    practice.sync(c, [])
    practice.sync(c, [])
    with session_scope(c.sessions) as s:
        t = s.scalar(select(m.PracticeTrade))
        assert t is not None and t.status == "closed" and t.exit_reason == "window"
        assert t.net_pnl is not None and t.charges and t.charges > 0
        summary = practice.summarise(c, s)
        assert summary.closed == 1 and summary.winners == (1 if t.net_pnl > 0 else 0)
        assert summary.verdict is not None and summary.verdict.key == "early"
        assert summary.curve and summary.fd_profit > 0


def test_stop_hit_closes_trade_as_loss(after_pick: Container) -> None:
    c = after_pick
    with session_scope(c.sessions) as s:
        ref = s.scalar(select(m.Pick.ref_close))
    assert ref is not None
    add_days(c, "GOODCO", [(ref, ref * 1.01, ref * 0.99, ref), (ref * 0.98, ref * 0.98, ref * 0.80, ref * 0.85)])
    practice.sync(c, [])
    practice.sync(c, [])
    with session_scope(c.sessions) as s:
        t = s.scalar(select(m.PracticeTrade))
        assert t is not None and t.status == "closed" and t.exit_reason == "stop"
        assert t.exit_price == round(t.stop_price, 2) and (t.net_pnl or 0) < 0


def test_track_record_and_help_pages(after_pick: Container) -> None:
    c = after_pick
    client = TestClient(create_app(c), base_url="http://testserver")
    client.post("/login", data={"username": "pavan", "password": "correct-horse-battery"})
    with session_scope(c.sessions) as s:
        ref = s.scalar(select(m.Pick.ref_close))
    assert ref is not None
    r = client.get("/track-record")  # the pick exists but has not been bought yet: the page is not empty
    assert r.status_code == 200 and "Waiting to buy" in r.text and "Good Company" in r.text
    add_days(c, "GOODCO", [(ref, ref * 1.02, ref * 0.99, ref * 1.01), (ref * 1.01, ref * 1.04, ref, ref * 1.03)])
    practice.sync(c, [])
    r = client.get("/track-record")
    assert r.status_code == 200
    assert "after all costs and tax" in r.text and "Too early to tell" in r.text and "Still holding" in r.text
    assert "fixed deposit" in r.text and "<svg class=\"chart\"" in r.text and "Why so far" in r.text
    assert "Stocks it said no to" in r.text
    r = client.get("/how-it-works")
    assert r.status_code == 200 and "Some real news, some excitement" in r.text and "Safety level" in r.text
    assert "Company sizes" in r.text and "Large companies" in r.text
    r = client.get("/")
    assert "Welcome. Here's how to use this" in r.text and "Why it might go up" in r.text
    assert "Large companies" in r.text and "Mid-size companies" in r.text and "Small companies" in r.text
    assert "If you put" in r.text and "Most you'd lose" in r.text
    client.post("/welcome/dismiss")
    assert "Welcome. Here's how to use this" not in client.get("/").text


def test_user_practice_trade_flow(after_pick: Container) -> None:
    c = after_pick
    client = TestClient(create_app(c), base_url="http://testserver")
    client.post("/login", data={"username": "pavan", "password": "correct-horse-battery"})
    with session_scope(c.sessions) as s:
        pick = s.scalar(select(m.Pick))
        assert pick is not None
        ref, report_id = pick.ref_close, pick.report_id
    r = client.post("/practice", data={"report_id": report_id, "amount": "15000"}, follow_redirects=False)
    assert r.status_code == 303 and "msg=Added" in r.headers["location"]
    again = client.post("/practice", data={"report_id": report_id, "amount": "15000"}, follow_redirects=False)
    assert "already%20have" in again.headers["location"] or "already have" in again.headers["location"]
    too_small = client.post("/practice", data={"report_id": report_id, "amount": "5"}, follow_redirects=False)
    assert "err=" in too_small.headers["location"]
    assert "Waiting to buy" in client.get("/practice").text

    add_days(c, "GOODCO", [(ref, ref * 1.02, ref * 0.99, ref * 1.01), (ref * 1.02, ref * 1.04, ref, ref * 1.03)])
    practice.sync(c, [])
    with session_scope(c.sessions) as s:
        u = s.scalar(select(m.UserTrade))
        assert u is not None and u.status == "open" and u.quantity == int(15000 // ref) and u.amount == 15000
        assert u.reason_key and s.scalar(select(m.PracticeTrade.id)) is not None  # the app's own book is separate
        trade_id = u.id
    page = client.get("/practice")
    assert "Sell now" in page.text and "Still holding" in page.text
    assert client.post(f"/practice/{trade_id}/sell", follow_redirects=False).status_code == 303
    practice.sync(c, [])
    with session_scope(c.sessions) as s:
        u = s.get(m.UserTrade, trade_id)
        assert u is not None and u.status == "closed" and u.exit_reason == "manual" and u.net_pnl is not None


def test_stock_costing_more_than_the_amount_buys_one_share(container: Container) -> None:
    from stockresearch.domain import practice as rules

    e = rules.plan_entry(25000.0, 23000.0, container.settings.practice_amount)
    assert e.quantity == 1 and e.skip_reason is None


def test_weekly_results_email(after_pick: Container) -> None:
    from stockresearch.services import notify

    c = after_pick
    with session_scope(c.sessions) as s:
        ref = s.scalar(select(m.Pick.ref_close))
    assert ref is not None
    add_days(c, "GOODCO", [(ref, ref * 1.02, ref * 0.99, ref * 1.01)])
    practice.sync(c, [])
    assert notify.weekly_results(c) == ["fake"]
    notifier = c.notifiers[0]
    assert isinstance(notifier, fakes.FakeNotifier)
    subject, html, text = notifier.sent[-1]
    assert subject.startswith("How the app's picks did, week to") and "after costs" in subject
    assert "fixed deposit" in html and "How the app" in html
    # what the app paid, the price now and the gain, in table form
    for needle in ("Bought at", "Price now", "Gain", "Still holding", "Put in", "Worth now", "In profit"):
        assert needle in html, needle
    assert "after costs" in html and "<table" in html
    assert "Full track record:" in text and "Good Company" in text


def test_morning_email_layout(after_pick: Container) -> None:
    from stockresearch.services import notify

    c = after_pick
    notifier = c.notifiers[0]
    assert isinstance(notifier, fakes.FakeNotifier)
    with session_scope(c.sessions) as s:
        run_id = s.scalar(select(m.Report.run_id).where(m.Report.is_pick.is_(True)))
    assert run_id is not None
    assert notify.daily_email(c, run_id, fakes.TODAY, pending=False) == ["fake"]
    subject, html, text = notifier.sent[-1]
    assert subject.startswith("1 new pick for") and "GOODCO" in subject
    for needle in ("Large companies", "Sell if below", "lose up to", "Most you&#39;d lose", "Check it yourself",
                   "Read the full report", "Details for each new pick", "Today&#39;s picks",
                   "No picks today in: mid-size companies, small companies"):
        assert needle in html, needle
    assert "₹10,000" in html  # the pretend amount, with lakh grouping
    assert "LARGE COMPANIES" in text and "Sell if it falls to" in text and "Most you'd lose" in text
    assert "<table" in html and "max-width:640px" in html and "prefers-color-scheme: dark" in html



class TestPresenters:
    def test_plain_translations(self) -> None:
        assert presenters.plain("+ Sales (latest year on year) growing fast (+38.3%)") == "Sales are up 38% on a year ago."
        assert "already jumped 22%" in presenters.plain("- Already up +22.1% this week and +19.7% above its 20-day average")
        assert "official reason" in presenters.plain("No verified trigger: capped at 3 stars")
        assert presenters.plain("+ 05 Nov 2026: Big order received [verified]") == "Big order received."

    def test_rating_words(self) -> None:
        assert presenters.rating_words(4.6)[0] == "Very strong case"
        assert presenters.rating_words(3.0)[0] == "Not strong enough yet: wait and watch"
        assert presenters.rating_words(1.5)[0] == "Avoid"


def test_inr_lakh_grouping() -> None:
    from stockresearch.formatting import inr

    assert inr(174903) == "₹1,74,903"
    assert inr(12345678.5, 2) == "₹1,23,45,678.50"
    assert inr(-598) == "-₹598" and inr(7038, signed=True) == "+₹7,038" and inr(0) == "₹0"
