from datetime import date, timedelta

from stockresearch.domain import practice
from stockresearch.domain.costs import CostModel

D0 = date(2026, 11, 13)


def bars(*rows: tuple[float, float, float, float]) -> list[practice.Bar]:
    return [practice.Bar(D0 + timedelta(days=i), *r) for i, r in enumerate(rows)]


class TestEntry:
    def test_buys_whole_shares_with_its_share_of_the_money(self) -> None:
        e = practice.plan_entry(1088.0, 1000.0, 20000)
        assert e.quantity == 18 and e.skip_reason is None

    def test_buys_one_share_when_a_share_costs_more_than_the_amount(self) -> None:
        e = practice.plan_entry(25000.0, 23000.0, 10000)
        assert e.quantity == 1 and e.skip_reason is None

    def test_skips_when_it_opens_below_the_safety_level(self) -> None:
        e = practice.plan_entry(95.0, 96.0, 20000)
        assert e.quantity == 0 and "safety level" in (e.skip_reason or "")


class TestExit:
    def test_window_end_sells_at_close(self) -> None:
        b = bars((100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101, 103))
        x = practice.find_exit(b, stop=90, hold_days=3)
        assert x == practice.Exit(b[2].day, 103, practice.EXIT_WINDOW)

    def test_still_open_before_window_end(self) -> None:
        assert practice.find_exit(bars((100, 102, 99, 101)), stop=90, hold_days=3) is None

    def test_stop_hit_during_the_day_sells_at_stop(self) -> None:
        b = bars((100, 102, 99, 101), (100, 101, 89, 92))
        x = practice.find_exit(b, stop=90, hold_days=10)
        assert x is not None and x.price == 90 and x.reason == practice.EXIT_STOP

    def test_gap_below_stop_sells_at_the_open_not_the_stop(self) -> None:
        b = bars((100, 102, 99, 101), (85, 88, 84, 87))
        x = practice.find_exit(b, stop=90, hold_days=10)
        assert x is not None and x.price == 85 and x.reason == practice.EXIT_GAP

    def test_stop_on_entry_day(self) -> None:
        x = practice.find_exit(bars((100, 101, 88, 95)), stop=90, hold_days=10)
        assert x is not None and x.price == 90 and x.day == D0


class TestSettle:
    def test_profit_pays_charges_and_tax(self) -> None:
        r = practice.settle(18, 1000.0, 1100.0, CostModel())
        gross = 18 * 100
        assert r.charges > 0 and r.tax > 0
        assert r.net < gross * 0.8  # tax and charges come out
        assert abs(r.net - (gross - r.charges - r.tax)) < 0.02

    def test_loss_pays_no_tax(self) -> None:
        r = practice.settle(18, 1000.0, 900.0, CostModel())
        assert r.tax == 0 and r.net < -1800


class TestVerdictAndFd:
    def test_too_early(self) -> None:
        v = practice.verdict(6, 40, 0.05, 0.01)
        assert v.key == "early" and "14 more" in v.detail

    def test_ahead_and_behind(self) -> None:
        assert practice.verdict(25, 40, 0.05, 0.02).key == "ahead"
        assert practice.verdict(25, 40, 0.01, 0.02).key == "behind"

    def test_fd_interest(self) -> None:
        assert round(practice.fd_interest(36500, D0, D0 + timedelta(days=10)), 2) == 70.0


class TestEarliestEntry:
    def test_published_before_open_buys_same_morning(self) -> None:
        from datetime import datetime

        assert practice.earliest_entry_day(D0, datetime(2026, 11, 13, 8, 0)) == D0

    def test_published_after_open_buys_next_day(self) -> None:
        from datetime import datetime

        assert practice.earliest_entry_day(D0, datetime(2026, 11, 13, 14, 0)) == D0 + timedelta(days=1)

    def test_published_evening_before_buys_on_pick_date(self) -> None:
        from datetime import datetime

        assert practice.earliest_entry_day(D0, datetime(2026, 11, 12, 21, 0)) == D0


class TestRepeatRule:
    D = date(2026, 11, 9)

    def test_first_pick_is_allowed(self) -> None:
        assert practice.repeat_block(None, None, None) is None

    def test_blocked_while_waiting_or_open(self) -> None:
        assert "still running" in (practice.repeat_block(self.D, None, self.D + timedelta(days=2)) or "")
        assert "still running" in (practice.repeat_block(self.D, "open", self.D + timedelta(days=2)) or "")

    def test_after_it_finishes_needs_a_new_confirmed_event(self) -> None:
        assert "Nothing new" in (practice.repeat_block(self.D, "closed", self.D) or "")
        assert "Nothing new" in (practice.repeat_block(self.D, "closed", None) or "")
        assert practice.repeat_block(self.D, "closed", self.D + timedelta(days=3)) is None


class TestManualExit:
    def test_sells_at_the_first_open_on_or_after_the_request(self) -> None:
        b = bars((100, 102, 99, 101), (103, 105, 102, 104), (106, 108, 105, 107))
        x = practice.manual_exit(b, D0 + timedelta(days=1))
        assert x is not None and x.price == 103 and x.reason == practice.EXIT_MANUAL
        assert practice.manual_exit(b, D0 + timedelta(days=9)) is None
