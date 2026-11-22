"""Builders for test data."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from stockresearch.domain.models import (
    EventFact,
    EventType,
    Fundamentals,
    Ownership,
    Quarter,
    SourceRef,
    Technicals,
    Tier,
    Verification,
)

TODAY = date(2026, 11, 12)


def tech(**kw: object) -> Technicals:
    base = Technicals(
        as_of=TODAY, close=100.0, ret_1d=0.01, ret_5d=0.04, ret_20d=0.08, ret_60d=0.15, ret_250d=0.30,
        vol_ratio=1.6, turnover_cr=20.0, dma10=98.0, dma20=96.0, dma50=92.0, dma200=85.0, ext_20dma=0.04,
        high_52w=103.0, from_high=-0.03, atr_pct=0.025, delivery_5d=55.0, delivery_50d=45.0,
        at_upper_circuit=False, series="EQ",
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def good_fundamentals(**kw: object) -> Fundamentals:
    base = Fundamentals(
        sector="Industrials", market_cap_cr=5000, pe=35, revenue_growth=0.28, earnings_growth=0.30,
        profit_margin=0.16, debt_to_equity=0.3, roe=0.2, revenue_cagr_3y=0.22, profit_cagr_3y=0.25,
        annual_revenue_cr=1000, institutional_pct=18,
        quarters=(Quarter(date(2026, 9, 30), 280, 45), Quarter(date(2026, 6, 30), 260, 40),
                  Quarter(date(2026, 3, 31), 250, 38)),
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def ownership(**kw: object) -> Ownership:
    base = Ownership(as_of=date(2026, 9, 30), promoter_pct=55, promoter_change_qoq=0.0, pledged_pct=0.0,
                     institutional_pct=18)
    return replace(base, **kw)  # type: ignore[arg-type]


def src(tier: Tier = Tier.OFFICIAL, publisher: str = "NSE", url: str = "https://nsearchives.nseindia.com/x.pdf") -> SourceRef:
    return SourceRef(None, url, publisher, tier, datetime(2026, 11, 10, 18, 0), "title")


def event(etype: EventType = EventType.ORDER_WIN, polarity: int = 1, status: Verification = Verification.VERIFIED,
          days_ago: int = 2, value_cr: float | None = 150.0, title: str = "Order win") -> EventFact:
    return EventFact("TEST", etype, polarity, title, TODAY - timedelta(days=days_ago), status, value_cr, (src(),))


def bars(n: int = 260, start: float = 100.0, drift: float = 0.001, seed: int = 1, end: date = TODAY,
         delivery: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.015, n)
    close = start * np.cumprod(1 + rets)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    df = pd.DataFrame({
        "open": close * (1 - 0.003), "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": rng.integers(200_000, 400_000, n).astype(float),
    }, index=idx)
    if delivery:
        df["delivery_pct"] = rng.uniform(35, 55, n)
    return df
