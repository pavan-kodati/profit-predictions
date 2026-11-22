"""Safety gates (plan section 3.1). A stock that fails any gate is never a pick."""

from __future__ import annotations

from datetime import date, timedelta

from stockresearch.domain.models import GateResult, Ownership, Technicals

TRADE_FOR_TRADE_SERIES = {"BE", "BZ", "BT", "IT"}
RESULTS_WINDOW_DAYS = 21


def check(
    t: Technicals,
    ownership: Ownership,
    surveillance: str | None,
    next_results: date | None,
    today: date,
    min_turnover_cr: float,
    max_pledge_pct: float,
) -> GateResult:
    res = GateResult(passed=True)
    if t.turnover_cr < min_turnover_cr:
        res.failures.append(
            f"Low liquidity: average daily trading of Rs {t.turnover_cr:.1f} crore is below Rs {min_turnover_cr:.0f} crore, "
            "so it may be hard to sell quickly"
        )
    if surveillance:
        res.failures.append(
            f"On the exchange surveillance list ({surveillance}). The exchange flags these for unusual speculation; "
            "they can hit the lower limit and become impossible to sell"
        )
    if t.series and t.series.upper() in TRADE_FOR_TRADE_SERIES:
        res.failures.append(f"Trades in the restricted '{t.series}' series (trade-for-trade): no intraday exit")
    if t.at_upper_circuit:
        res.failures.append("Closed locked at the upper price limit: buy orders may not get filled")
    if ownership.pledged_pct is not None and ownership.pledged_pct > max_pledge_pct:
        res.failures.append(
            f"{ownership.pledged_pct:.1f}% of promoter shares are pledged (limit {max_pledge_pct:.0f}%): forced-selling risk"
        )
    if next_results and today <= next_results <= today + timedelta(days=RESULTS_WINDOW_DAYS):
        res.flags.append(
            f"Results due on {next_results:%d %b %Y}, inside the holding window. The reaction to results is unpredictable"
        )
    res.passed = not res.failures
    return res
