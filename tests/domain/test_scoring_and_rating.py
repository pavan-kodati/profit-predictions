from stockresearch.domain import gates, rating, scoring
from stockresearch.domain.models import (
    AreaScore,
    EventType,
    Fundamentals,
    GateResult,
    Ownership,
    PickType,
    Verification,
)
from tests.helpers import TODAY, event, good_fundamentals, ownership, tech


def areas(**overrides: float | None) -> dict[str, AreaScore]:
    base = {"quality": 4.0, "price": 4.0, "trigger": 4.0, "order_book": 4.0, "ownership": 4.0, "sentiment": 4.0}
    base.update(overrides)
    return {k: AreaScore(v) for k, v in base.items()}


class TestQuality:
    def test_strong_business_scores_high(self) -> None:
        s = scoring.score_quality(good_fundamentals())
        assert s.score is not None and s.score >= 4.5

    def test_loss_making_falling_business_scores_low(self) -> None:
        f = good_fundamentals(revenue_growth=-0.1, earnings_growth=-0.3, profit_margin=-0.05, debt_to_equity=2.0,
                              roe=0.02, revenue_cagr_3y=-0.05, profit_cagr_3y=-0.2)
        s = scoring.score_quality(f)
        assert s.score is not None and s.score <= 1.5
        assert any("making a loss" in r for r in s.reasons)

    def test_no_data_is_none_not_guessed(self) -> None:
        assert scoring.score_quality(Fundamentals()).score is None


class TestPrice:
    def test_overheated_stock_penalised(self) -> None:
        calm = scoring.score_price(tech())
        hot = scoring.score_price(tech(ret_5d=0.22, ext_20dma=0.25))
        assert hot.score is not None and calm.score is not None and hot.score < calm.score
        assert any("late-entry" in r for r in hot.reasons)

    def test_speculative_volume_penalised(self) -> None:
        s = scoring.score_price(tech(vol_ratio=2.5, delivery_5d=20.0, delivery_50d=45.0))
        assert any("same-day trading" in r for r in s.reasons)


class TestTrigger:
    def test_verified_large_order_scores_high(self) -> None:
        s = scoring.score_trigger([event(value_cr=200)], annual_revenue_cr=1000, today=TODAY)
        assert s.score is not None and s.score >= 4.5

    def test_reported_counts_half_and_unverified_not_at_all(self) -> None:
        verified = scoring.score_trigger([event(value_cr=200)], 1000, TODAY).score
        reported = scoring.score_trigger([event(value_cr=200, status=Verification.REPORTED)], 1000, TODAY).score
        unverified = scoring.score_trigger([event(value_cr=200, status=Verification.UNVERIFIED)], 1000, TODAY).score
        assert verified is not None and reported is not None
        assert verified > reported > 3.0
        assert unverified == 3.0

    def test_old_events_ignored(self) -> None:
        assert scoring.score_trigger([event(days_ago=20)], 1000, TODAY).score == 3.0

    def test_negative_event_lowers_score(self) -> None:
        s = scoring.score_trigger([event(EventType.NEGATIVE, -1, title="Auditor resigned", days_ago=15)], 1000, TODAY)
        assert s.score is not None and s.score < 3.0


class TestOwnershipAndOrderBook:
    def test_promoter_selling_penalised(self) -> None:
        good = scoring.score_ownership(ownership()).score
        bad = scoring.score_ownership(ownership(promoter_change_qoq=-3.0, promoter_net_buy_cr_90d=-50)).score
        assert good is not None and bad is not None and bad < good

    def test_no_ownership_data_is_none(self) -> None:
        assert scoring.score_ownership(Ownership()).score is None

    def test_order_book_cover(self) -> None:
        assert scoring.score_order_book(3500, [], 1000, TODAY).score == 5.0
        assert scoring.score_order_book(None, [], 1000, TODAY).score is None


class TestSentiment:
    def test_balance(self) -> None:
        pos = scoring.score_sentiment(["positive", "positive", "neutral"], [])
        neg = scoring.score_sentiment(["negative", "negative"], ["positive"])
        assert pos.score is not None and neg.score is not None and pos.score > 3 > neg.score
        assert scoring.score_sentiment([], []).score is None


class TestRating:
    def test_missing_areas_reweighted_and_completeness_reported(self) -> None:
        avg, completeness = rating.weighted(areas(order_book=None, sentiment=None))
        assert avg == 4.0
        assert completeness == 0.8

    def test_no_verified_trigger_caps_at_three(self) -> None:
        r = rating.rate(areas(quality=5, price=5, trigger=5), tech(), GateResult(True), Verification.REPORTED, False)
        assert r.overall <= 3.0 and r.stars == 3
        assert any("No verified trigger" in c for c in r.caps)

    def test_big_five_day_jump_caps(self) -> None:
        r = rating.rate(areas(), tech(ret_5d=0.18), GateResult(True), Verification.VERIFIED, False)
        assert r.overall <= 3.0

    def test_low_completeness_caps(self) -> None:
        a = areas(quality=None, trigger=None, order_book=None)
        r = rating.rate(a, tech(), GateResult(True), Verification.VERIFIED, False)
        assert r.completeness < 0.6 and r.overall <= 3.0

    def test_verified_negative_excludes(self) -> None:
        r = rating.rate(areas(), tech(), GateResult(True), Verification.VERIFIED, True)
        assert r.excluded_reason and r.overall <= 2.0

    def test_strong_verified_case_is_business_backed(self) -> None:
        r = rating.rate(areas(), tech(), GateResult(True), Verification.VERIFIED, False)
        assert r.stars == 4 and r.pick_type == PickType.FUNDAMENTALS and not r.caps

    def test_weak_business_is_hype(self) -> None:
        r = rating.rate(areas(quality=2.5), tech(), GateResult(True), Verification.VERIFIED, False)
        assert r.pick_type == PickType.HYPE

    def test_ai_can_only_make_label_more_cautious(self) -> None:
        up = rating.rate(areas(quality=2.5), tech(), GateResult(True), Verification.VERIFIED, False,
                         rating.AiSignals(ai_pick_type=PickType.FUNDAMENTALS))
        assert up.pick_type == PickType.HYPE
        down = rating.rate(areas(), tech(), GateResult(True), Verification.VERIFIED, False,
                           rating.AiSignals(ai_pick_type=PickType.HYPE))
        assert down.pick_type == PickType.HYPE

    def test_long_term_uses_business_areas_only_and_no_trigger_cap(self) -> None:
        r = rating.rate(areas(trigger=None, price=1, sentiment=None), tech(), GateResult(True),
                        Verification.UNVERIFIED, False, long_term=True)
        assert r.overall == 4.0 and not r.caps

    def test_levels(self) -> None:
        lv = rating.levels(tech(close=200, atr_pct=0.03))
        assert lv["stop"] == 188.0 and lv["plus10"] == 220.0


class TestGates:
    def run(self, **kw: object) -> GateResult:
        t = kw.pop("t", tech())
        o = kw.pop("o", ownership())
        return gates.check(t, o, kw.get("surv"), kw.get("results"), TODAY, 5.0, 25.0)  # type: ignore[arg-type]

    def test_clean_stock_passes(self) -> None:
        assert self.run().passed

    def test_each_gate(self) -> None:
        assert not self.run(t=tech(turnover_cr=1)).passed
        assert not self.run(surv="ASM Stage I").passed
        assert not self.run(t=tech(series="BE")).passed
        assert not self.run(t=tech(at_upper_circuit=True)).passed
        assert not self.run(o=ownership(pledged_pct=40)).passed

    def test_results_inside_window_is_flag_not_failure(self) -> None:
        from datetime import timedelta
        r = self.run(results=TODAY + timedelta(days=7))
        assert r.passed and r.flags
