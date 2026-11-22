from stockresearch.domain import sources, verification
from stockresearch.domain.models import Tier, Verification
from tests.helpers import src


class TestClassify:
    def test_official_exchange_domains(self) -> None:
        assert sources.classify("https://nsearchives.nseindia.com/corporate/a.pdf") == (Tier.OFFICIAL, "NSE")
        assert sources.classify("https://www.bseindia.com/xml-data/corpfiling/x.pdf") == (Tier.OFFICIAL, "BSE")

    def test_press_domain_with_factual_headline(self) -> None:
        tier, name = sources.classify("https://economictimes.indiatimes.com/a.cms", "Company wins Rs 500 crore order")
        assert (tier, name) == (Tier.PRESS, "Economic Times")

    def test_recommendation_headline_is_opinion_even_from_press(self) -> None:
        for title in ("Buy Azad Engineering: target of Rs 3100", "Brokerages see 20% upside", "Stocks to buy this week"):
            assert sources.classify("https://www.livemint.com/a", title)[0] == Tier.OPINION

    def test_unknown_and_social_sources_are_blocked(self) -> None:
        for url in ("https://twitter.com/x/status/1", "https://t.me/stockcalls", "https://randomblog.in/post",
                    "https://www.youtube.com/watch?v=1", "not a url"):
            assert sources.classify(url)[0] == Tier.BLOCKED

    def test_forum_on_allowed_publisher_is_blocked(self) -> None:
        assert sources.classify("https://mmb.moneycontrol.com/forum-topics/x")[0] == Tier.BLOCKED
        assert sources.classify("https://www.moneycontrol.com/forum/abc")[0] == Tier.BLOCKED

    def test_lookalike_domain_is_blocked(self) -> None:
        assert sources.classify("https://economictimes.indiatimes.com.evil.example/a")[0] == Tier.BLOCKED
        assert sources.classify("https://fakelivemint.com/a")[0] == Tier.BLOCKED


class TestVerify:
    def test_official_filing_verifies(self) -> None:
        assert verification.verify([src(Tier.OFFICIAL)]) == Verification.VERIFIED

    def test_two_independent_outlets_verify(self) -> None:
        s = [src(Tier.PRESS, "Mint"), src(Tier.PRESS, "Economic Times")]
        assert verification.verify(s) == Verification.VERIFIED

    def test_same_outlet_twice_is_only_reported(self) -> None:
        assert verification.verify([src(Tier.PRESS, "Mint"), src(Tier.PRESS, "Mint")]) == Verification.REPORTED

    def test_opinion_alone_is_unverified(self) -> None:
        assert verification.verify([src(Tier.OPINION, "Mint")]) == Verification.UNVERIFIED
        assert verification.verify([]) == Verification.UNVERIFIED

    def test_trust_labels(self) -> None:
        assert verification.trust_label([src()]) == "Official NSE filing"
        assert verification.trust_label([src(Tier.PRESS, "Mint"), src(Tier.PRESS, "Reuters")]) == "Reported by Mint and Reuters"
        assert verification.trust_label([src(Tier.PRESS, "Mint")]) == "Single source: Mint"


class TestEvidenceValidation:
    DOC = "The Company has received a Letter of Award worth Rs. 1,250 crore from NTPC Limited for supply of turbines."

    def test_quote_must_be_in_document(self) -> None:
        assert verification.quote_in_document("received a Letter of Award worth Rs. 1,250 crore", self.DOC)
        assert verification.quote_in_document("RECEIVED a letter   of award worth rs. 1,250 CRORE", self.DOC)
        assert not verification.quote_in_document("received an order worth Rs 2,000 crore", self.DOC)

    def test_too_short_quote_rejected(self) -> None:
        assert not verification.quote_in_document("NTPC", self.DOC)

    def test_numbers_in_claim_must_be_in_quote(self) -> None:
        quote = "worth Rs. 1,250 crore from NTPC"
        assert verification.numbers_supported("Won a Rs 1,250 crore order from NTPC", quote)
        assert not verification.numbers_supported("Won a Rs 1,500 crore order", quote)
        assert verification.numbers_supported("Won an order from NTPC", quote)

    def test_value_supported(self) -> None:
        assert verification.value_supported(1250, "worth Rs. 1,250 crore")
        assert verification.value_supported(12.5, "order of Rs 1250 lakh")
        assert not verification.value_supported(1500, "worth Rs. 1,250 crore")
        assert verification.value_supported(None, "anything")
