"""Back-test of the price and volume screen (plan E4).

Replays the screen on stored history: each day, take the top picks by the price rules, buy at the next day's open,
and measure the return after 10 trading days. Compares against buying every liquid stock (the baseline).
The news and filings part cannot be back-tested this way; that is what paper trading is for.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain.costs import net_return
from stockresearch.services import repo
from stockresearch.services.runs import JobContext
from stockresearch.services.tracker import cost_model

HOLD = 10
TOP_N = 5


def _panel(bars: dict[str, pd.DataFrame], field: str) -> pd.DataFrame:
    return pd.DataFrame({s: df[field] for s, df in bars.items()}).sort_index()


def run(c: Container, years: int = 3, top_n: int = TOP_N) -> dict[str, Any]:
    with session_scope(c.sessions) as s:
        symbols = repo.universe_symbols(s)
        bars = repo.load_bars(s, symbols, days=365 * years + 60)
    bars = {k: v for k, v in bars.items() if len(v) >= 230}
    if not bars:
        return {"error": "Not enough stored price history. Run: stockresearch bootstrap --years 3"}
    close, open_, high, vol = (_panel(bars, f) for f in ("close", "open", "high", "volume"))
    turnover = (close * vol).rolling(20).mean() / 1e7
    dma50 = close.rolling(50).mean()
    vol_ratio = vol.rolling(5).mean() / vol.rolling(50).mean()
    ret5 = close / close.shift(5) - 1
    ret20 = close / close.shift(20) - 1
    from_high = close / high.rolling(250, min_periods=200).max() - 1
    ext20 = close / close.rolling(20).mean() - 1
    entry = open_.shift(-1)
    fwd = close.shift(-HOLD - 1) / entry - 1

    eligible = (turnover >= c.settings.min_turnover_cr) & (close > dma50)
    active = (vol_ratio >= 1.5) | (ret5 >= 0.05) | (from_high >= -0.05)
    not_hot = (ret5 <= 0.15) & (ext20 <= 0.20)
    mask = eligible & active & not_hot
    rank = lambda df: df.where(mask).rank(axis=1, pct=True)  # noqa: E731
    score = 0.35 * rank(vol_ratio) + 0.25 * rank(ret5) + 0.20 * rank(ret20) + 0.20 * rank(from_high)

    dates = score.index[210:-HOLD - 2]  # warm-up for the 200-day measures
    picks_ret: list[float] = []
    base_ret: list[float] = []
    per_day = []
    for d in dates[::HOLD]:  # non-overlapping holding periods
        row = score.loc[d].dropna()
        if row.empty:
            continue
        chosen = row.nlargest(top_n).index
        r = fwd.loc[d, chosen].dropna()
        b = fwd.loc[d][eligible.loc[d]].dropna()
        if r.empty or b.empty:
            continue
        picks_ret.extend(r.tolist())
        base_ret.extend(b.tolist())
        per_day.append({"date": str(pd.Timestamp(d).date()), "picks": list(chosen), "avg": float(r.mean()),
                        "baseline": float(b.mean())})
    if not picks_ret:
        return {"error": "The screen found no candidates in the stored history"}
    model = cost_model(c)
    p, b = np.array(picks_ret), np.array(base_ret)
    net = np.array([net_return(x, c.settings.reference_investment, model) for x in p])
    return {
        "years": years, "hold_days": HOLD, "top_n": top_n, "periods": len(per_day), "trades": len(p),
        "from": per_day[0]["date"], "to": per_day[-1]["date"],
        "picks": {"avg": float(p.mean()), "median": float(np.median(p)), "hit_rate": float((p > 0).mean()),
                  "avg_net": float(net.mean()), "worst": float(p.min()), "best": float(p.max()),
                  "share_10pct": float((p >= 0.10).mean())},
        "baseline": {"avg": float(b.mean()), "median": float(np.median(b)), "hit_rate": float((b > 0).mean())},
        "excess_avg": float(p.mean() - b.mean()),
        "recent_periods": per_day[-12:],
    }


def backtest_job(ctx: JobContext, years: int) -> str:
    with ctx.stage("Back-test the price screen") as detail:
        result = run(ctx.c, years)
        with session_scope(ctx.c.sessions) as s:
            s.add(m.BacktestResult(created_at=repo.now_ist(), data=result))
        if "error" in result:
            raise RuntimeError(result["error"])
        detail.append(f"{result['trades']} trades, average {result['picks']['avg'] * 100:+.2f}% vs baseline "
                      f"{result['baseline']['avg'] * 100:+.2f}%")
    return "Back-test saved"
