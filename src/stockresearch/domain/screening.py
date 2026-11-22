"""Daily screen: narrow ~750 stocks to a research shortlist (plan section 6, market-close job)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from stockresearch.domain.models import Technicals


@dataclass(frozen=True)
class Candidate:
    symbol: str
    pre_score: float
    reason: str


def screen(
    techs: dict[str, Technicals],
    event_symbols: set[str],
    min_turnover_cr: float,
    top_n: int,
    groups: Mapping[str, str] | None = None,
) -> list[Candidate]:
    """Liquid stocks in an uptrend that show fresh activity, plus liquid stocks with a fresh positive filing.

    `event_symbols` are stocks with a positive official filing in the last few days; they get a ranking boost
    so the research budget goes to stocks that have a concrete reason to move.

    With `groups` (symbol -> size group), `top_n` is the limit for each group, so large, mid-size and small
    companies each get a fair share of the shortlist.
    """
    rows = []
    for sym, t in techs.items():
        if t.turnover_cr < min_turnover_cr:
            continue
        has_event = sym in event_symbols
        if t.close < t.dma50 and not has_event:
            continue
        active = (t.vol_ratio or 0) >= 1.5 or (t.ret_5d or 0) >= 0.05 or t.from_high >= -0.05
        if not (active or has_event):
            continue
        delivery_up = (
            t.delivery_5d is not None and t.delivery_50d is not None and t.delivery_5d > t.delivery_50d * 1.1
        )
        rows.append(
            {
                "symbol": sym,
                "vol_ratio": t.vol_ratio or 0.0,
                "ret_5d": t.ret_5d or 0.0,
                "ret_20d": t.ret_20d or 0.0,
                "from_high": t.from_high,
                "event": 1.0 if has_event else 0.0,
                "delivery_up": 1.0 if delivery_up else 0.0,
            }
        )
    if not rows:
        return []
    df = pd.DataFrame(rows).set_index("symbol")
    rank = df[["vol_ratio", "ret_5d", "ret_20d", "from_high"]].rank(pct=True)
    df["pre"] = (
        0.30 * rank["vol_ratio"]
        + 0.20 * rank["ret_5d"]
        + 0.15 * rank["ret_20d"]
        + 0.15 * rank["from_high"]
        + 0.15 * df["event"]
        + 0.05 * df["delivery_up"]
    )
    out = []
    taken: dict[str, int] = {}
    for idx, row in df.sort_values("pre", ascending=False).iterrows():
        group = groups.get(str(idx), "small") if groups is not None else "all"
        if taken.get(group, 0) >= top_n:
            continue
        taken[group] = taken.get(group, 0) + 1
        bits = []
        if row["event"]:
            bits.append("fresh positive filing")
        if row["vol_ratio"] >= 1.5:
            bits.append(f"volume {row['vol_ratio']:.1f}x normal")
        if row["ret_5d"] >= 0.05:
            bits.append(f"up {row['ret_5d'] * 100:.0f}% in 5 days")
        if row["from_high"] >= -0.05:
            bits.append("near 52-week high")
        out.append(Candidate(str(idx), round(float(row["pre"]), 4), ", ".join(bits) or "screen"))
    return out
