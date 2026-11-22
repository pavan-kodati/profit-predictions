"""Price and volume measures from daily bars. Input: a DataFrame indexed by date, oldest first."""

from __future__ import annotations

import math

import pandas as pd

from stockresearch.domain.models import Technicals

MIN_BARS = 60
CIRCUIT_BANDS = (0.02, 0.05, 0.10, 0.20)


def _f(x: float | None) -> float | None:
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else x


def at_upper_limit(close: float, high: float, ret_1d: float | None, band_pct: float | None) -> bool:
    """Closed at the day's high, exactly at the upper price limit.

    band_pct: the official daily limit in percent, 0 for stocks with no fixed band, None if unknown
    (then the common bands are tried, which can mislabel a large stock that happened to rise ~5%).
    """
    if ret_1d is None or close < high * 0.9995 or band_pct == 0:
        return False
    bands = (band_pct / 100,) if band_pct else CIRCUIT_BANDS
    return any(abs(ret_1d - b) < 0.002 for b in bands)


def compute(bars: pd.DataFrame, series: str | None = None, band_pct: float | None = None) -> Technicals | None:
    """bars columns: open, high, low, close, volume, optional delivery_pct. Returns None if too little history."""
    bars = bars.dropna(subset=["close"])
    if len(bars) < MIN_BARS:
        return None
    c, h, low, v = bars["close"], bars["high"], bars["low"], bars["volume"]
    close = float(c.iloc[-1])

    def ret(n: int) -> float | None:
        if len(c) <= n or not c.iloc[-n - 1]:
            return None
        return _f(close / float(c.iloc[-n - 1]) - 1)

    prev = c.shift(1)
    true_range = pd.concat([h - low, (h - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = _f(true_range.tail(14).mean())
    dma20 = float(c.tail(20).mean())
    vol50 = float(v.tail(50).mean())
    high_52w = float(h.tail(250).max())
    ret_1d = ret(1)
    at_circuit = at_upper_limit(close, float(h.iloc[-1]), ret_1d, band_pct)
    delivery_5d = delivery_50d = None
    if "delivery_pct" in bars.columns:
        d = bars["delivery_pct"].dropna()
        if len(d) >= 5:
            delivery_5d = _f(d.tail(5).mean())
        if len(d) >= 20:
            delivery_50d = _f(d.tail(50).mean())
    return Technicals(
        as_of=pd.Timestamp(bars.index[-1]).date(),
        close=round(close, 2),
        ret_1d=ret_1d,
        ret_5d=ret(5),
        ret_20d=ret(20),
        ret_60d=ret(60),
        ret_250d=ret(250),
        vol_ratio=_f(v.tail(5).mean() / vol50) if vol50 else None,
        turnover_cr=float((c * v).tail(20).mean() / 1e7),
        dma10=float(c.tail(10).mean()),
        dma20=dma20,
        dma50=float(c.tail(50).mean()),
        dma200=float(c.tail(200).mean()) if len(c) >= 200 else None,
        ext_20dma=close / dma20 - 1,
        high_52w=round(high_52w, 2),
        from_high=close / high_52w - 1,
        atr_pct=atr / close if atr and close else None,
        delivery_5d=delivery_5d,
        delivery_50d=delivery_50d,
        at_upper_circuit=bool(at_circuit),
        series=series,
    )


def is_overheated(t: Technicals) -> bool:
    return (t.ret_5d or 0) > 0.15 or t.ext_20dma > 0.20


def is_speculative(t: Technicals) -> bool:
    """Volume up while the share of delivered (held) shares falls: intraday churn rather than buying."""
    if t.delivery_5d is None or t.delivery_50d is None or t.vol_ratio is None:
        return False
    return t.vol_ratio >= 1.5 and t.delivery_5d < t.delivery_50d * 0.85
