"""Yahoo Finance adapter (via yfinance): price history backfill, fallback prices, and company financials.

Yahoo data is unofficial and derived from exchange filings. It is used for prices when NSE files are missing,
and for financial statements, which NSE publishes only as complex XBRL.
"""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

import pandas as pd
import yfinance as yf

from stockresearch.adapters.http import SourceUnavailable
from stockresearch.ports import FundamentalsData

log = logging.getLogger(__name__)
INDEX_SYMBOL = "^NSEI"
INFO_KEYS = (
    "sector", "industry", "marketCap", "trailingPE", "returnOnEquity", "debtToEquity", "revenueGrowth",
    "earningsGrowth", "profitMargins", "heldPercentInstitutions", "heldPercentInsiders", "totalRevenue",
    "longBusinessSummary",
)


def ticker(symbol: str) -> str:
    return symbol if symbol.startswith("^") else f"{symbol}.NS"


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _rows(stmt: pd.DataFrame, limit: int) -> list[tuple[date, float | None, float | None]]:
    out: list[tuple[date, float | None, float | None]] = []
    if stmt is None or stmt.empty:
        return out
    for col in list(stmt.columns)[:limit]:
        rev = _num(stmt.at["Total Revenue", col]) if "Total Revenue" in stmt.index else None
        net = _num(stmt.at["Net Income", col]) if "Net Income" in stmt.index else None
        if rev is None and net is None:
            continue
        out.append((pd.Timestamp(col).date(),
                    round(rev / 1e7, 2) if rev is not None else None,
                    round(net / 1e7, 2) if net is not None else None))
    return out


class YahooMarketData:
    def history(self, symbols: list[str], period: str = "1y", chunk: int = 80) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), chunk):
            part = symbols[i:i + chunk]
            try:
                raw = yf.download([ticker(s) for s in part], period=period, group_by="ticker", auto_adjust=False,
                                  actions=False, progress=False, threads=True)
            except Exception as e:
                log.warning("Yahoo history failed for a batch of %d: %s", len(part), e)
                continue
            for s in part:
                try:
                    df = raw[ticker(s)] if len(part) > 1 or isinstance(raw.columns, pd.MultiIndex) else raw
                except KeyError:
                    continue
                df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
                if not df.empty:
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    out[s] = df
        return out

    def fundamentals(self, symbol: str) -> FundamentalsData:
        try:
            t = yf.Ticker(ticker(symbol))
            info = t.info or {}
        except Exception as e:
            raise SourceUnavailable(f"Yahoo fundamentals failed for {symbol}: {e}") from e
        data = FundamentalsData(info={k: info.get(k) for k in INFO_KEYS})
        try:
            data.quarters = _rows(t.quarterly_income_stmt, 6)
        except Exception as e:
            log.info("quarterly statements unavailable for %s: %s", symbol, e)
        try:
            data.annual = _rows(t.income_stmt, 5)
        except Exception as e:
            log.info("annual statements unavailable for %s: %s", symbol, e)
        return data

    def today_open(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in symbols:
            try:
                df = yf.Ticker(ticker(s)).history(period="1d", interval="1m")
                if not df.empty:
                    out[s] = float(df["Open"].iloc[0])
            except Exception as e:
                log.info("opening price unavailable for %s: %s", s, e)
        return out
