from datetime import date, timedelta

import pytest

from stockresearch.domain import costs, exits, filings, portfolio, screening, technicals
from stockresearch.domain.models import EventType, Fundamentals, HoldingStatus, Quarter
from tests.helpers import TODAY, bars, event, good_fundamentals, ownership, tech


class TestTechnicals:
    def test_compute_from_bars(self) -> None:
        t = technicals.compute(bars())
        assert t is not None
        assert t.as_of == TODAY
        assert t.dma200 is not None and t.delivery_5d is not None and t.delivery_50d is not None
        assert t.high_52w >= t.close * 0.5

    def test_too_little_history(self) -> None:
        assert technicals.compute(bars(n=30)) is None

    def test_upper_limit_uses_official_band(self) -> None:
        # Large derivatives stocks have no fixed band: a 5% close at the high is not a lock.
        assert not technicals.at_upper_limit(105.0, 105.0, 0.05, band_pct=0.0)
        assert technicals.at_upper_limit(105.0, 105.0, 0.05, band_pct=5.0)
        assert not technicals.at_upper_limit(105.0, 105.0, 0.05, band_pct=20.0)
        assert technicals.at_upper_limit(120.0, 120.0, 0.20, band_pct=20.0)
        assert not technicals.at_upper_limit(104.0, 105.0, 0.05, band_pct=5.0)  # closed below the high
        assert technicals.at_upper_limit(105.0, 105.0, 0.0501, band_pct=None)  # unknown band: common bands tried

    def test_upper_circuit_detected(self) -> None:
        df = bars()
        prev = float(df["close"].iloc[-2])
        df.iloc[-1, df.columns.get_loc("close")] = prev * 1.05
        df.iloc[-1, df.columns.get_loc("high")] = prev * 1.05
        t = technicals.compute(df)
        assert t is not None and t.at_upper_circuit


class TestScreen:
    def test_ranks_active_liquid_uptrend_and_skips_illiquid(self) -> None:
        techs = {
            "HOT": tech(vol_ratio=3.0, ret_5d=0.08),
            "QUIET": tech(vol_ratio=0.9, ret_5d=0.0, from_high=-0.2),
            "ILLIQUID": tech(turnover_cr=1.0, vol_ratio=4.0),
            "DOWN": tech(close=80, dma50=92),
        }
        out = screening.screen(techs, set(), 5.0, 10)
        syms = [c.symbol for c in out]
        assert syms[0] == "HOT"
        assert "ILLIQUID" not in syms and "QUIET" not in syms and "DOWN" not in syms

    def test_fresh_filing_brings_in_stock(self) -> None:
        out = screening.screen({"FILER": tech(vol_ratio=0.9, ret_5d=0.0, from_high=-0.2)}, {"FILER"}, 5.0, 10)
        assert out and "fresh positive filing" in out[0].reason


class TestFilings:
    def test_categories(self) -> None:
        assert filings.classify_filing("Bagging/Receiving of orders/contracts") == (EventType.ORDER_WIN, 1)
        assert filings.classify_filing("Resignation of Statutory Auditor") == (EventType.NEGATIVE, -1)
        assert filings.classify_filing("Analysts/Institutional Investor Meet/Con. Call Updates") is None
        assert filings.needs_reading("Outcome of Board Meeting")
        assert not filings.needs_reading("Copy of Newspaper Publication")

    def test_institution_detection(self) -> None:
        assert filings.is_institution("MOTILAL OSWAL MUTUAL FUND")
        assert filings.is_institution("SBI LIFE INSURANCE CO LTD")
        assert not filings.is_institution("GRAVITON RESEARCH CAPITAL LLP")
        assert not filings.is_institution("VAIBHAV RAJENDRA DOSHI")


class TestCosts:
    def test_charges_and_tax_reduce_return(self) -> None:
        net = costs.net_return(0.10, 40000, costs.CostModel())
        assert 0.07 < net < 0.08

    def test_losses_not_taxed(self) -> None:
        net = costs.net_return(-0.05, 40000, costs.CostModel())
        assert -0.06 < net < -0.05

    def test_long_term_rate(self) -> None:
        assert costs.net_return(0.10, 40000, costs.CostModel(), long_term=True) > costs.net_return(0.10, 40000, costs.CostModel())


class TestExits:
    def test_stop_and_negative_event(self) -> None:
        sig = exits.check("X", "short", 100, None, 95, tech(close=94, dma10=96), [event(EventType.NEGATIVE, -1)])
        kinds = {s.kind for s in sig}
        assert {"stop_hit", "negative_event", "below_short_average"} <= kinds

    def test_long_term_only_watches_long_trend(self) -> None:
        sig = exits.check("X", "long", 100, None, None, tech(close=80, dma10=90, dma200=85, vol_ratio=0.3), [])
        assert [s.kind for s in sig] == ["below_long_average"]


class TestPortfolio:
    def test_broken_when_profit_falls_two_quarters(self) -> None:
        f = Fundamentals(quarters=(Quarter(date(2026, 9, 30), 100, 5), Quarter(date(2026, 6, 30), 100, 8),
                                   Quarter(date(2026, 3, 31), 100, 10)))
        r = portfolio.health_status(3.5, 3.5, f, ownership(), tech(), [], "long", 25)
        assert r.status == HoldingStatus.BROKEN

    def test_intact(self) -> None:
        r = portfolio.health_status(4.0, 4.2, good_fundamentals(), ownership(), tech(), [], "long", 25)
        assert r.status == HoldingStatus.INTACT

    def test_weakening(self) -> None:
        r = portfolio.health_status(2.8, 2.5, good_fundamentals(), ownership(), tech(), [], "long", 25)
        assert r.status == HoldingStatus.WEAKENING

    def test_averaging_check_company_specific_fall(self) -> None:
        status = portfolio.StatusResult(HoldingStatus.WEAKENING, ["weak"])
        out = portfolio.averaging_check(120, tech(close=90), -0.25, -0.02, status, 2.8)
        assert out is not None and out["company_specific"] is True and out["business_holds"] is False
        assert portfolio.averaging_check(80, tech(close=90), 0.1, 0.0, status, 4) is None

    def test_tax_timing_flag(self) -> None:
        out = portfolio.tax_timing(TODAY - timedelta(days=340), TODAY, in_profit=True)
        assert out is not None and out["flag"] is True
        done = portfolio.tax_timing(TODAY - timedelta(days=400), TODAY, in_profit=True)
        assert done is not None and done["long_term"] is True

    def test_concentration_flags(self) -> None:
        out = portfolio.concentration({"A": 60, "B": 20, "C": 20}, {"A": "IT", "B": "IT", "C": "Banks"})
        assert any("A is 60%" in f for f in out["flags"])  # type: ignore[union-attr]
        assert any("IT sector" in f for f in out["flags"])  # type: ignore[union-attr]
        assert portfolio.concentration({}, {})["total"] == 0


@pytest.mark.parametrize("value", [0.0])
def test_placeholder_parametrize_works(value: float) -> None:
    assert value == 0.0
