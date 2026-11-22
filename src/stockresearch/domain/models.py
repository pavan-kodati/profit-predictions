"""Domain value objects. Pure data: no I/O, no database, no network."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import IntEnum, StrEnum


class Tier(IntEnum):
    """Trust tier of a source (plan section 2.1)."""

    BLOCKED = 0
    OFFICIAL = 1
    PRESS = 2
    OPINION = 3


TIER_LABEL = {
    Tier.OFFICIAL: "Official exchange record",
    Tier.PRESS: "Established financial press",
    Tier.OPINION: "Opinion (broker or analyst view)",
    Tier.BLOCKED: "Blocked source",
}


class EventType(StrEnum):
    ORDER_WIN = "order_win"
    RESULTS = "results"
    EXPANSION = "expansion"
    APPROVAL = "approval"
    ACQUISITION = "acquisition"
    STAKE_BUY = "stake_buy"
    FUNDRAISE = "fundraise"
    NEGATIVE = "negative"
    OTHER = "other"


class Verification(StrEnum):
    VERIFIED = "verified"
    REPORTED = "reported"
    UNVERIFIED = "unverified"


class PickType(StrEnum):
    HYPE = "hype"
    MIXED = "mixed"
    FUNDAMENTALS = "fundamentals"


PICK_TYPE_LABEL = {
    PickType.HYPE: "Mostly excitement",
    PickType.MIXED: "Some real news, some excitement",
    PickType.FUNDAMENTALS: "Backed by business results",
}

HOLD_GUIDE = {
    PickType.HYPE: "3 to 5 trading days only. The price is moving on excitement, so decide when you'll sell before you buy.",
    PickType.MIXED: "Up to 2 weeks. There's a real reason, but part of the rise is excitement, so don't hold too long.",
    PickType.FUNDAMENTALS: "Up to 3 weeks. The company's own results support the rise.",
}


class HoldingStatus(StrEnum):
    INTACT = "intact"
    WEAKENING = "weakening"
    BROKEN = "broken"


@dataclass(frozen=True)
class Technicals:
    as_of: date
    close: float
    ret_1d: float | None
    ret_5d: float | None
    ret_20d: float | None
    ret_60d: float | None
    ret_250d: float | None
    vol_ratio: float | None
    turnover_cr: float
    dma10: float
    dma20: float
    dma50: float
    dma200: float | None
    ext_20dma: float
    high_52w: float
    from_high: float
    atr_pct: float | None
    delivery_5d: float | None
    delivery_50d: float | None
    at_upper_circuit: bool
    series: str | None = None


@dataclass(frozen=True)
class Quarter:
    period_end: date
    revenue_cr: float | None
    profit_cr: float | None


@dataclass(frozen=True)
class Fundamentals:
    sector: str | None = None
    market_cap_cr: float | None = None
    pe: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    profit_margin: float | None = None
    debt_to_equity: float | None = None  # ratio, e.g. 0.3
    roe: float | None = None
    revenue_cagr_3y: float | None = None
    profit_cagr_3y: float | None = None
    annual_revenue_cr: float | None = None
    institutional_pct: float | None = None  # 0-100
    quarters: tuple[Quarter, ...] = ()

    def has_data(self) -> bool:
        return any(
            v is not None
            for v in (self.revenue_growth, self.earnings_growth, self.profit_margin, self.debt_to_equity, self.roe)
        ) or bool(self.quarters)


@dataclass(frozen=True)
class Ownership:
    as_of: date | None = None
    promoter_pct: float | None = None
    promoter_change_qoq: float | None = None  # percentage points
    promoter_change_yoy: float | None = None
    pledged_pct: float | None = None  # % of promoter shares pledged
    institutional_pct: float | None = None
    promoter_net_buy_cr_90d: float | None = None
    fund_bulk_buys_30d: int = 0
    fund_bulk_sells_30d: int = 0

    def has_data(self) -> bool:
        return self.promoter_pct is not None or self.institutional_pct is not None


@dataclass(frozen=True)
class SourceRef:
    document_id: int | None
    url: str
    publisher: str
    tier: Tier
    published_at: datetime | None
    title: str = ""


@dataclass(frozen=True)
class EventFact:
    symbol: str
    event_type: EventType
    polarity: int
    title: str
    event_date: date
    status: Verification
    value_cr: float | None = None
    sources: tuple[SourceRef, ...] = ()


@dataclass(frozen=True)
class EvidenceItem:
    """One checkable line of evidence (plan section 2.4)."""

    area: str
    stance: str  # "for" | "against"
    claim: str
    quote: str
    source: SourceRef
    trust_label: str


@dataclass
class AreaScore:
    score: float | None
    reasons: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.score is not None


@dataclass
class GateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass
class Rating:
    overall: float
    stars: int
    areas: dict[str, AreaScore]
    completeness: float
    caps: list[str]
    pick_type: PickType
    hold_guide: str
    excluded_reason: str | None
