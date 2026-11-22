"""Splits and bonus issues (plan v5): parsing, price adjustment, pretend trades, correction notes, cross-check."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from stockresearch.adapters.nse import parse_corporate_actions
from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import corporate_actions as ca
from stockresearch.domain import gates
from stockresearch.ports import CorporateActionRow
from stockresearch.services import corporate_actions, holdings, practice, repo, tracker
from tests import fakes
from tests.helpers import ownership, tech
from tests.test_pipeline import full_day
from tests.test_practice_flow import add_days

D1, D2, D3 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)

# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize(("text", "expected"), [
    ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share", [(ca.SPLIT, 5.0)]),
    ("Face Value Split (Sub-Division) - From Re 1/- Per Share To Re 0.50/- Per Share", [(ca.SPLIT, 2.0)]),
    ("Bonus 1:1", [(ca.BONUS, 2.0)]),
    ("Bonus 1:2", [(ca.BONUS, 1.5)]),
    ("Consolidation of shares from Rs 1 to Rs 10", [(ca.CONSOLIDATION, 0.1)]),
    ("Bonus 1:1 And Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share",
     [(ca.BONUS, 2.0), (ca.SPLIT, 2.0)]),
    ("Face Value Split (Sub-Division)", [(ca.UNREADABLE, None)]),
    ("Interim Dividend - Rs 5 Per Share", []),
    ("Annual General Meeting", []),
])
def test_parse_nse_subjects(text: str, expected: list[tuple[str, float | None]]) -> None:
    got = ca.parse(text)
    assert [k for k, _ in got] == [k for k, _ in expected]
    for (_, f), (_, e) in zip(got, expected, strict=True):
        assert f == pytest.approx(e) if e is not None else f is None


def test_adapter_parses_corporate_actions_reply() -> None:
    rows = parse_corporate_actions([
        {"symbol": "ABC ", "series": "EQ", "subject": "Bonus  1:1", "exDate": "17-Nov-2026"},
        {"symbol": "DEF", "series": "EQ", "subject": "Dividend", "exDate": "-"},
        {"symbol": "GHI", "series": "N1", "subject": "Bonus 1:1", "exDate": "17-Nov-2026"},  # a bond series
    ])
    assert rows == [CorporateActionRow("ABC", date(2026, 11, 17), "Bonus 1:1")]


# ---------------------------------------------------------------- adjusting prices


def test_factor_convert_and_rebase() -> None:
    acts = [ca.Action(D2, 5.0, ca.SPLIT), ca.Action(D3, 2.0, ca.BONUS), ca.Action(D3, None, ca.UNREADABLE)]
    assert ca.factor_between(acts, D1) == 10.0
    assert ca.factor_between(acts, D2) == 2.0  # the split on D2 is already in D2's own prices
    assert ca.factor_between(acts, D1, D2) == 5.0
    assert ca.convert(1000.0, acts, D1) == 100.0
    assert ca.convert(100.0, acts, D3, D1) == 1000.0
    latest = pd.DataFrame({"open": [10.0], "high": [10.0], "low": [10.0], "close": [10.0], "volume": [100.0]},
                          index=pd.to_datetime([D3]))
    back = ca.rebase(latest, acts, D1)
    assert back is not None
    assert back["close"].iloc[0] == 100.0 and back["volume"].iloc[0] == 10.0


def test_split_mid_series_becomes_smooth() -> None:
    idx = pd.to_datetime([D1, D2, D3])
    raw = pd.DataFrame({"open": [500.0, 100.0, 101.0], "high": [505.0, 102.0, 103.0], "low": [495.0, 99.0, 100.0],
                        "close": [500.0, 101.0, 102.0], "volume": [1000.0, 5000.0, 5000.0],
                        "delivery_pct": [50.0, 50.0, 50.0]}, index=idx)
    out = ca.to_latest(raw, pd.Series([D1, D2, D3], index=idx), [ca.Action(D2, 5.0, ca.SPLIT)])
    assert out["close"].tolist() == [100.0, 101.0, 102.0]
    assert out["volume"].tolist() == [5000.0, 5000.0, 5000.0]
    assert out["delivery_pct"].tolist() == [50.0, 50.0, 50.0]


def _row(sym: str, d: date, close: float, source: str = "nse", basis: date | None = None) -> dict[str, object]:
    return {"symbol": sym, "date": d, "open": close, "high": close, "low": close, "close": close, "volume": 1000.0,
            "delivery_pct": 50.0, "series": "EQ", "source": source, "basis_date": basis or d}


def test_load_bars_does_not_adjust_yahoo_rows_twice(container: Container) -> None:
    with session_scope(container.sessions) as s:
        # NSE raw before the split, and a Yahoo row downloaded after it (Yahoo had already adjusted it)
        repo.store_prices(s, [_row("SPL", D1, 500.0), _row("SPL", date(2026, 11, 2) - timedelta(days=3), 100.0,
                                                         source="yahoo", basis=D3),
                              _row("SPL", D2, 101.0), _row("SPL", D3, 102.0)])
        repo.add_action(s, "SPL", D2, ca.SPLIT, 5.0, "split", "nse")
    with session_scope(container.sessions) as s:
        df = repo.load_bars(s, ["SPL"], days=60)["SPL"]
    assert df["close"].tolist() == [100.0, 100.0, 101.0, 102.0]


# ---------------------------------------------------------------- pretend trades


def _split_after_pick(c: Container) -> tuple[float, date]:
    """Day 1 trades normally after the pick; day 2 the share splits 1 into 5. Returns (ref close, ex-date)."""
    with session_scope(c.sessions) as s:
        ref = s.scalar(select(m.Pick.ref_close))
    assert ref is not None
    add_days(c, "GOODCO", [(ref, ref * 1.01, ref * 0.99, ref), (ref / 5, ref * 1.02 / 5, ref * 0.99 / 5, ref * 1.01 / 5)])
    ex = pd.bdate_range(fakes.TODAY + timedelta(days=1), periods=2)[1].date()
    return ref, ex


@pytest.fixture()
def after_pick(container: Container) -> Container:
    full_day(container)
    return container


def test_trade_across_a_known_split_is_not_stopped_out(after_pick: Container) -> None:
    c = after_pick
    ref, ex = _split_after_pick(c)
    with session_scope(c.sessions) as s:
        repo.add_action(s, "GOODCO", ex, ca.SPLIT, 5.0, "Face Value Split From Rs 10 To Rs 2", "nse")
    practice.sync(c, [])
    practice.sync(c, [])
    with session_scope(c.sessions) as s:
        t = s.scalar(select(m.PracticeTrade))
        assert t is not None and t.status == "open"
        summary = practice.summarise(c, s)
        view = summary.trades[0]
        assert view.last_close == pytest.approx(ref * 1.01, rel=1e-6)  # in the trade's own share units
        assert view.open_net is not None and abs(view.open_net) < 0.05 * t.buy_value


def test_trade_settled_before_split_was_known_gets_correction_note(
        after_pick: Container, monkeypatch: pytest.MonkeyPatch) -> None:
    c = after_pick
    _, ex = _split_after_pick(c)
    practice.sync(c, [])
    practice.sync(c, [])
    with session_scope(c.sessions) as s:
        t = s.scalar(select(m.PracticeTrade))
        assert t is not None and t.status == "closed" and t.exit_reason == "gap"  # the fake crash: opened far below
        fake_loss, trade_id = t.net_pnl, t.id
    monkeypatch.setattr(repo, "now_ist", lambda: datetime.combine(ex + timedelta(days=3), datetime.min.time()))
    c.exchange.actions = [CorporateActionRow(  # type: ignore[attr-defined]
        "GOODCO", ex, "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share")]
    detail: list[str] = []
    corporate_actions.ingest(c, {"GOODCO"}, detail, today=ex)
    with session_scope(c.sessions) as s:
        t = s.get(m.PracticeTrade, trade_id)
        assert t is not None and t.net_pnl == fake_loss  # the trade itself is never edited
        notes = corporate_actions.corrections(s, "app")
        assert "split" in notes[trade_id] and "left out of the totals" in notes[trade_id]
        summary = practice.summarise(c, s)
        assert summary.corrected == 1 and summary.closed == 0 and summary.realised == 0
        assert summary.trades[0].correction
    from fastapi.testclient import TestClient

    from stockresearch.web.app import create_app

    client = TestClient(create_app(c), base_url="http://testserver")
    client.post("/login", data={"username": "pavan", "password": "correct-horse-battery"})
    page = client.get("/track-record").text
    assert "Correction:" in page and "not counted" in page and "1 trade left out of these figures" in page
    corporate_actions.ingest(c, {"GOODCO"}, detail, today=ex)  # idempotent
    with session_scope(c.sessions) as s:
        assert len(list(s.scalars(select(m.TradeCorrection)))) == 1
    assert any(a.symbol == "GOODCO" and "correction note" in a.detail for a in corporate_actions.check(c))


def test_pick_outcome_is_not_a_fake_crash(after_pick: Container) -> None:
    c = after_pick
    _, ex = _split_after_pick(c)
    with session_scope(c.sessions) as s:
        repo.add_action(s, "GOODCO", ex, ca.SPLIT, 5.0, "split", "nse")
    tracker.update_outcomes(c, [])
    with session_scope(c.sessions) as s:
        o = s.scalar(select(m.PickOutcome))
        assert o is not None and o.max_drawdown is not None and o.max_drawdown > -0.05


# ---------------------------------------------------------------- cross-check, gate, holdings


def test_unexplained_jump_is_adjusted_and_keeps_stock_out_of_picks(container: Container) -> None:
    with session_scope(container.sessions) as s:
        repo.store_prices(s, [_row("JMP", D1, 500.0)])
        day_df = pd.DataFrame([{"symbol": "JMP", "prev_close": 100.0}, {"symbol": "OK", "prev_close": 50.0}])
        assert corporate_actions.cross_check(s, day_df, D2, {"JMP", "OK"}) == ["JMP"]
        assert corporate_actions.cross_check(s, day_df, D2, {"JMP", "OK"}) == []  # recorded once
        acts = repo.load_actions(s, ["JMP"])["JMP"]
        assert acts[0].kind == ca.UNEXPLAINED and acts[0].factor == pytest.approx(5.0)
        assert s.scalar(select(m.Alert).where(m.Alert.symbol == "JMP")) is not None
        assert repo.load_bars(s, ["JMP"], days=60, until=D3)["JMP"]["close"].iloc[0] == pytest.approx(100.0)
    warning = ca.price_warning(acts, D2 + timedelta(days=3))
    assert warning and "unreliable" in warning
    assert ca.price_warning(acts, D2 + timedelta(days=ca.HOLD_OUT_DAYS + 1)) is None
    gate = gates.check(tech(), ownership(), None, None, D3, 5, 25, warning)
    assert not gate.passed and warning in gate.failures


def test_small_differences_are_not_jumps() -> None:
    assert ca.unexplained_ratio(100.0, 101.5) is None
    assert ca.unexplained_ratio(100.0, 50.0) == pytest.approx(2.0)


def test_holding_figures_follow_a_bonus(container: Container) -> None:
    h = m.Holding(symbol="BON", quantity=10, avg_price=400.0, stop_price=360.0, horizon="short", source="manual",
                  created_at=datetime(2026, 11, 1, 10, 0))
    lv = holdings.levels(h, [ca.Action(D2, 2.0, ca.BONUS)])
    assert (lv.quantity, lv.avg_price, lv.stop_price) == (20, 200.0, 180.0)
    assert lv.notes and "bonus" in lv.notes[0]
    assert holdings.levels(h, []).avg_price == 400.0


def test_manual_entry(container: Container) -> None:
    assert "Recorded ABC" in corporate_actions.add_manual(container, "abc", D2, 5.0, "")
    assert "Already recorded" in corporate_actions.add_manual(container, "ABC", D2, 5.0, "")
    with pytest.raises(ValueError):
        corporate_actions.add_manual(container, "ABC", D3, 0, "")
