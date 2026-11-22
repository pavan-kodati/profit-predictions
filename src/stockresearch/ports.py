"""Interfaces between the services and the outside world. Adapters implement these; tests use fakes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

import pandas as pd

# ---------------------------------------------------------------- records crossing the boundary


@dataclass(frozen=True)
class UniverseRow:
    symbol: str
    name: str
    industry: str | None
    isin: str | None


@dataclass(frozen=True)
class Filing:
    symbol: str
    company: str
    category: str
    summary: str
    url: str
    published_at: datetime


@dataclass(frozen=True)
class Deal:
    symbol: str
    deal_date: date
    client: str
    side: str  # BUY | SELL
    quantity: float
    price: float
    kind: str  # bulk | block


@dataclass(frozen=True)
class ShareholdingRow:
    period_end: date
    promoter_pct: float | None
    public_pct: float | None
    url: str | None


@dataclass(frozen=True)
class PledgeInfo:
    period_end: date | None
    pledged_pct: float | None


@dataclass(frozen=True)
class InsiderTrade:
    person: str
    category: str
    side: str  # buy | sell
    value_rs: float
    trade_date: date
    url: str | None


@dataclass(frozen=True)
class Article:
    url: str
    publisher_url: str
    title: str
    published_at: datetime | None
    summary: str = ""


@dataclass
class FundamentalsData:
    info: dict[str, Any] = field(default_factory=dict)
    quarters: list[tuple[date, float | None, float | None]] = field(default_factory=list)  # (period, rev cr, profit cr)
    annual: list[tuple[date, float | None, float | None]] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchDocument:
    """A stored document handed to the AI. `doc_id` is our database id."""

    doc_id: int
    publisher: str
    tier: int
    published: str
    title: str
    text: str


# ---------------------------------------------------------------- ports


class ExchangeSource(Protocol):
    def universe(self) -> list[UniverseRow]: ...
    def all_listed(self) -> list[UniverseRow]: ...
    def cap_lists(self) -> dict[str, set[str]]: ...
    def bhavcopy(self, day: date) -> pd.DataFrame | None: ...
    def announcements(self, start: date, end: date, symbol: str | None = None) -> list[Filing]: ...
    def bulk_and_block_deals(self) -> list[Deal]: ...
    def surveillance(self) -> dict[str, str]: ...
    def price_bands(self) -> dict[str, float]: ...
    def shareholding(self, symbol: str) -> list[ShareholdingRow]: ...
    def pledge(self, symbol: str) -> PledgeInfo: ...
    def insider_trades(self, symbol: str) -> list[InsiderTrade]: ...
    def next_results_date(self, symbol: str, today: date) -> date | None: ...
    def document_text(self, url: str, max_pages: int = 6) -> str: ...


class MarketDataSource(Protocol):
    def history(self, symbols: list[str], period: str) -> dict[str, pd.DataFrame]: ...
    def fundamentals(self, symbol: str) -> FundamentalsData: ...
    def today_open(self, symbols: list[str]) -> dict[str, float]: ...


class NewsSource(Protocol):
    def company_news(self, name: str, days: int) -> list[Article]: ...
    def market_feeds(self) -> list[Article]: ...


class Researcher(Protocol):
    model_name: str

    def analyse(self, symbol: str, name: str, facts: dict[str, Any], documents: list[ResearchDocument]) -> dict[str, Any]:
        """Return raw AI findings. The caller validates every quote and number before using any of it."""
        ...


class Notifier(Protocol):
    name: str

    def send(self, subject: str, html: str, text: str) -> None: ...
