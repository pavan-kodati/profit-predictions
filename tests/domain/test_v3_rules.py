"""Plan v3 rules: size groups, shortlist per group, why a pick moved, and whether skipping a stock was right."""

from datetime import date

from stockresearch.domain import attribution as attr
from stockresearch.domain import capgroup, screening
from tests.helpers import tech

D = date(2026, 11, 13)


class TestSizeGroups:
    def test_official_lists_decide_the_group(self) -> None:
        assert capgroup.from_lists("A", {"A"}, {"B"}) == "large"
        assert capgroup.from_lists("B", {"A"}, {"B"}) == "mid"
        assert capgroup.from_lists("C", {"A"}, {"B"}) == "small"

    def test_market_value_is_the_fallback(self) -> None:
        assert capgroup.resolve(None, 250_000) == "large"
        assert capgroup.resolve(None, 50_000) == "mid"
        assert capgroup.resolve(None, 4_000) == "small"
        assert capgroup.resolve(None, None) == "small"
        assert capgroup.resolve("mid", 250_000) == "mid"  # the official list wins


class TestShortlistPerGroup:
    def test_each_group_gets_its_own_limit(self) -> None:
        techs = {f"L{i}": tech(vol_ratio=3.0 - i * 0.1) for i in range(4)} | {"S1": tech(vol_ratio=1.6)}
        groups = {f"L{i}": "large" for i in range(4)} | {"S1": "small"}
        out = screening.screen(techs, set(), 5.0, 2, groups)
        assert [c.symbol for c in out if groups[c.symbol] == "large"] == ["L0", "L1"]
        assert "S1" in {c.symbol for c in out}  # not crowded out by the stronger large companies


class TestWhyItMoved:
    def test_tiny_moves_are_not_explained(self) -> None:
        assert attr.explain(0.004, 0.03, None, [], False).key == attr.FLAT

    def test_company_news_comes_first(self) -> None:
        news = [attr.EventNote(D, "Order worth Rs 300 crore received", 1, "https://nse.example/x.pdf")]
        r = attr.explain(0.06, 0.03, 0.04, news, False)
        assert r.key == attr.BUSINESS and "Order worth" in r.text and r.links[0]["url"].endswith("x.pdf")

    def test_news_must_point_the_same_way_as_the_price(self) -> None:
        news = [attr.EventNote(D, "Order received", 1)]
        assert attr.explain(-0.05, 0.0, None, news, False).key == attr.UNKNOWN

    def test_whole_market(self) -> None:
        heads = [attr.Headline("Sensex sinks 900 points on war fears", "https://et.example/1")]
        r = attr.explain(-0.03, -0.021, None, [], False, heads)
        assert r.key == attr.MARKET and "fell 2.1%" in r.text and r.links[0]["title"].startswith("Sensex")

    def test_market_must_cover_most_of_the_move(self) -> None:
        assert attr.explain(-0.12, -0.02, None, [], False).key == attr.UNKNOWN

    def test_sector_then_faded_excitement(self) -> None:
        assert attr.explain(0.05, 0.002, 0.04, [], False).key == attr.SECTOR
        assert attr.explain(-0.05, 0.002, None, [], True).key == attr.HYPE_FADED
        assert attr.explain(0.05, 0.002, None, [], True).key == attr.UNKNOWN


class TestSkipVerdict:
    def test_verdicts(self) -> None:
        assert attr.skip_verdict(-0.07, 0.01) == attr.RIGHT
        assert attr.skip_verdict(0.01, 0.06) == attr.RIGHT  # did much worse than the Nifty
        assert attr.skip_verdict(0.02, 0.01) == attr.LITTLE
        assert attr.skip_verdict(0.09, 0.01) == attr.MISSED
        assert attr.skip_verdict(-0.01, -0.08) == attr.RIGHT  # it still fell
