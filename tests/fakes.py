"""In-memory fakes for every port, so the whole pipeline runs in tests without network access."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from stockresearch.ports import (
    Article,
    CorporateActionRow,
    Deal,
    Filing,
    FundamentalsData,
    InsiderTrade,
    PledgeInfo,
    ResearchDocument,
    ShareholdingRow,
    UniverseRow,
)

TODAY = date(2026, 11, 11)  # a Wednesday
NOW = datetime(2026, 11, 11, 20, 0)
FILING_URL = "https://nsearchives.nseindia.com/corporate/GOODCO_order.pdf"
PRESS_URL = "https://economictimes.indiatimes.com/markets/stocks/news/goodco-order/articleshow/1.cms"
OPINION_URL = "https://www.livemint.com/market/goodco-buy-target"
BLOCKED_URL = "https://twitter.com/pumper/status/123"
BLOCKED_TEXT = "GOODCO will go up 50 percent tomorrow trust me"


def make_bars(seed: int, drift: float, volume: float, n: int = 300, end: date = TODAY, hot_volume: bool = False,
              start: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = start * np.cumprod(1 + rng.normal(drift, 0.012, n))
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    vol = rng.uniform(0.8, 1.2, n) * volume
    if hot_volume:
        vol[-5:] *= 3
    df = pd.DataFrame({"open": close * 0.997, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": vol},
                      index=idx)
    deliv = rng.uniform(40, 50, n)
    if hot_volume:
        deliv[-5:] = 65
    df["delivery_pct"] = deliv
    return df


UNIVERSE = {
    "GOODCO": ("Good Company Limited", make_bars(1, 0.002, 1_500_000, hot_volume=True)),
    "WEAKCO": ("Weak Company Limited", make_bars(2, 0.002, 1_500_000, hot_volume=True)),
    "ILLQ": ("Illiquid Industries Limited", make_bars(3, 0.002, 2_000, hot_volume=True)),
    "SURV": ("Speculative Holdings Limited", make_bars(4, 0.004, 1_500_000, hot_volume=True)),
}
INDEX = make_bars(9, 0.0005, 1_000_000, start=24000)


class FakeExchange:
    def __init__(self) -> None:
        self.filings: list[Filing] = [
            Filing("GOODCO", "Good Company Limited", "Bagging/Receiving of orders/contracts",
                   "Good Company Limited has received an order worth Rs 300 crore from NTPC Limited for turbines.",
                   FILING_URL, NOW - timedelta(hours=2)),
            Filing("WEAKCO", "Weak Company Limited", "Copy of Newspaper Publication", "Newspaper ad",
                   "https://nsearchives.nseindia.com/corporate/WEAKCO_ad.pdf", NOW - timedelta(hours=3)),
        ]
        self.surveillance_list = {"SURV": "Short Term Additional Surveillance Measure (STASM) - Stage I"}
        self.fail_announcements = False
        self.actions: list[CorporateActionRow] = []

    def universe(self) -> list[UniverseRow]:
        return [UniverseRow(s, name, "Capital Goods", f"INE{i:07d}01") for i, (s, (name, _)) in enumerate(UNIVERSE.items())]

    def cap_lists(self) -> dict[str, set[str]]:
        return {"large": {"GOODCO"}, "mid": {"WEAKCO"}}

    def all_listed(self) -> list[UniverseRow]:
        return [*self.universe(), UniverseRow("OUTSIDE", "Outside Universe Limited", None, "INE9999999X1")]

    def bhavcopy(self, day: date) -> pd.DataFrame | None:
        rows = []
        for sym, (_, df) in UNIVERSE.items():
            ts = pd.Timestamp(day)
            if ts not in df.index:
                continue
            r = df.loc[ts]
            rows.append({"symbol": sym, "series": "EQ", "date": day, "open": r["open"], "high": r["high"], "low": r["low"],
                         "close": r["close"], "volume": r["volume"], "delivery_pct": r["delivery_pct"]})
        return pd.DataFrame(rows) if rows else None

    def announcements(self, start: date, end: date, symbol: str | None = None) -> list[Filing]:
        if self.fail_announcements:
            from stockresearch.adapters.http import SourceUnavailable

            raise SourceUnavailable("NSE blocked")
        return [f for f in self.filings if start <= f.published_at.date() <= end and (symbol is None or f.symbol == symbol)]

    def corporate_actions(self, start: date, end: date) -> list[CorporateActionRow]:
        return [a for a in self.actions if start <= a.ex_date <= end]

    def bulk_and_block_deals(self) -> list[Deal]:
        return [Deal("GOODCO", TODAY, "MOTILAL OSWAL MUTUAL FUND", "BUY", 500_000, 140.0, "block"),
                Deal("GOODCO", TODAY, "GRAVITON RESEARCH CAPITAL LLP", "SELL", 300_000, 141.0, "bulk")]

    def surveillance(self) -> dict[str, str]:
        return dict(self.surveillance_list)

    def price_bands(self) -> dict[str, float]:
        return {"GOODCO": 0.0, "WEAKCO": 20.0, "ILLQ": 5.0, "SURV": 5.0}

    def shareholding(self, symbol: str) -> list[ShareholdingRow]:
        pct = 55.0 if symbol != "WEAKCO" else 18.0
        return [ShareholdingRow(date(2026, 9, 30), pct, 100 - pct, f"https://nsearchives.nseindia.com/xbrl/SHP_{symbol}_q2.xml"),
                ShareholdingRow(date(2026, 6, 30), pct, 100 - pct, f"https://nsearchives.nseindia.com/xbrl/SHP_{symbol}_q1.xml")]

    def pledge(self, symbol: str) -> PledgeInfo:
        return PledgeInfo(date(2026, 9, 30), 0.0)

    def insider_trades(self, symbol: str) -> list[InsiderTrade]:
        return []

    def next_results_date(self, symbol: str, today: date) -> date | None:
        return None

    def document_text(self, url: str, max_pages: int = 6) -> str:
        return ""


class FakeMarket:
    def history(self, symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
        out = {}
        for s in symbols:
            if s == "^NSEI":
                out[s] = INDEX.drop(columns=["delivery_pct"])
            elif s in UNIVERSE:
                out[s] = UNIVERSE[s][1].drop(columns=["delivery_pct"])
        return out

    def fundamentals(self, symbol: str) -> FundamentalsData:
        if symbol == "WEAKCO":
            info = {"revenueGrowth": -0.12, "earningsGrowth": -0.4, "profitMargins": -0.03, "debtToEquity": 210,
                    "returnOnEquity": 0.02, "totalRevenue": 5e9, "heldPercentInstitutions": 0.01, "sector": "Industrials"}
            quarters = [(date(2026, 9, 30), 120.0, -5.0), (date(2026, 6, 30), 125.0, -2.0), (date(2026, 3, 31), 130.0, 1.0)]
        else:
            info = {"revenueGrowth": 0.28, "earningsGrowth": 0.30, "profitMargins": 0.16, "debtToEquity": 30,
                    "returnOnEquity": 0.20, "trailingPE": 35, "marketCap": 5e10, "totalRevenue": 1e10,
                    "heldPercentInstitutions": 0.18, "sector": "Industrials"}
            quarters = [(date(2026, 9, 30), 280.0, 45.0), (date(2026, 6, 30), 260.0, 40.0), (date(2026, 3, 31), 250.0, 38.0),
                        (date(2025, 12, 31), 240.0, 36.0), (date(2025, 9, 30), 220.0, 30.0)]
        annual = [(date(2026, 3, 31), 1000.0, 150.0), (date(2025, 3, 31), 820.0, 120.0),
                  (date(2024, 3, 31), 690.0, 95.0), (date(2023, 3, 31), 560.0, 75.0)]
        return FundamentalsData(info=info, quarters=quarters, annual=annual)

    def today_open(self, symbols: list[str]) -> dict[str, float]:
        return {s: float(UNIVERSE[s][1]["close"].iloc[-1]) * 1.05 for s in symbols if s in UNIVERSE}


class FakeNews:
    def company_news(self, name: str, days: int) -> list[Article]:
        if not name.startswith("Good Company"):
            return []
        return [
            Article(PRESS_URL, "https://economictimes.indiatimes.com", "Good Company wins Rs 300 crore order from NTPC",
                    NOW - timedelta(hours=1)),
            Article(OPINION_URL, "https://www.livemint.com", "Buy Good Company: target price Rs 180, says brokerage",
                    NOW - timedelta(hours=1)),
            Article(BLOCKED_URL, "https://twitter.com", BLOCKED_TEXT, NOW - timedelta(hours=1)),
        ]

    def market_feeds(self) -> list[Article]:
        return [Article("https://www.business-standard.com/markets/goodco-1.html", "https://www.business-standard.com",
                        "Good Company shares rise on NTPC order", NOW - timedelta(hours=1), "Shares gained 4%."),
                Article("https://randomblog.example/goodco", "https://randomblog.example", "Good Company to the moon",
                        NOW, "")]


@dataclass
class FakeResearcher:
    model_name: str = "fake-model"
    payloads: list[str] = field(default_factory=list)

    def analyse(self, symbol: str, name: str, facts: dict[str, Any], documents: list[ResearchDocument]) -> dict[str, Any]:
        self.payloads.append(json.dumps({"facts": facts, "docs": [d.__dict__ for d in documents]}, default=str))
        filing = next((d for d in documents if "has received an order" in d.text), None)
        press = next((d for d in documents if d.publisher == "Economic Times"), None)
        opinion = next((d for d in documents if d.tier == 3), None)
        if filing is None:
            return {"thesis": "Nothing notable.", "events": [], "evidence": [], "risks": ["None found"]}
        return {
            "thesis": "A Rs 300 crore order is 30% of yearly sales.",
            "events": [
                {"type": "order_win", "polarity": 1, "title": "Order worth Rs 300 crore from NTPC", "date": str(TODAY),
                 "value_cr": 300, "doc_ids": [filing.doc_id] + ([press.doc_id] if press else []),
                 "quote_doc_id": filing.doc_id, "quote": "has received an order worth Rs 300 crore from NTPC Limited"},
                {"type": "order_win", "polarity": 1, "title": "Order worth Rs 900 crore", "date": str(TODAY),
                 "value_cr": 900, "doc_ids": [filing.doc_id], "quote_doc_id": filing.doc_id,
                 "quote": "has received an order worth Rs 900 crore from a defence ministry"},
            ],
            "evidence": [
                {"area": "trigger", "stance": "for", "claim": "Received a Rs 300 crore order from NTPC",
                 "doc_id": filing.doc_id, "quote": "received an order worth Rs 300 crore from NTPC"},
                {"area": "quality", "stance": "for", "claim": "Profit rose 45% last quarter",
                 "doc_id": filing.doc_id, "quote": "received an order worth Rs 300 crore from NTPC"},
                {"area": "trigger", "stance": "for", "claim": "Brokerage sees big upside",
                 "doc_id": opinion.doc_id if opinion else 0, "quote": "target price Rs 180, says brokerage"},
            ],
            "order_book": {"value_cr": 2500, "doc_id": filing.doc_id, "quote": "an order worth Rs 300 crore"},
            "article_stances": ([{"doc_id": press.doc_id, "stance": "positive"}] if press else [])
                               + ([{"doc_id": opinion.doc_id, "stance": "positive"}] if opinion else []),
            "future_story": False, "pick_type": "fundamentals", "track_record": "Steady growth.",
            "risks": ["Execution delays", "Customer concentration"], "confidence": "medium",
        }


@dataclass
class FakeNotifier:
    name: str = "fake"
    sent: list[tuple[str, str, str]] = field(default_factory=list)

    def send(self, subject: str, html: str, text: str) -> None:
        self.sent.append((subject, html, text))
