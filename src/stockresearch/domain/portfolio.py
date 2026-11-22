"""Portfolio review rules (plan section 3.5). Evidence and status only: no keep, sell or average instructions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from stockresearch.domain.models import EventFact, Fundamentals, HoldingStatus, Ownership, Technicals

LONG_TERM_DAYS = 365
TAX_FLAG_DAYS = 45


@dataclass
class StatusResult:
    status: HoldingStatus
    reasons: list[str] = field(default_factory=list)


def health_status(
    rating: float,
    quality: float | None,
    f: Fundamentals,
    o: Ownership,
    t: Technicals | None,
    verified_negatives: list[EventFact],
    horizon: str,
    max_pledge_pct: float,
) -> StatusResult:
    broken, weak = [], []
    for e in verified_negatives:
        broken.append(f"Verified negative event on {e.event_date:%d %b %Y}: {e.title}")
    q = [x.profit_cr for x in f.quarters if x.profit_cr is not None]
    if len(q) >= 3 and q[0] < q[1] < q[2]:
        broken.append("Quarterly profit has fallen two quarters running")
    if o.promoter_change_qoq is not None and o.promoter_change_qoq <= -2:
        broken.append(f"Promoters cut their stake by {abs(o.promoter_change_qoq):.1f} points last quarter")
    if o.pledged_pct is not None and o.pledged_pct > max_pledge_pct:
        broken.append(f"{o.pledged_pct:.1f}% of promoter shares are pledged")
    if quality is not None and quality < 3:
        weak.append(f"Business quality score is weak ({quality:.1f}/5)")
    if rating < 3:
        weak.append(f"Overall rating is below average ({rating:.1f}/5)")
    if t is not None:
        if horizon == "long" and t.dma200 is not None and t.close < t.dma200:
            weak.append("Price is below its 200-day average (long-term downtrend)")
        if horizon == "short" and t.close < t.dma50:
            weak.append("Price is below its 50-day average (short-term trend lost)")
    if broken:
        return StatusResult(HoldingStatus.BROKEN, broken + weak)
    if weak:
        return StatusResult(HoldingStatus.WEAKENING, weak)
    return StatusResult(HoldingStatus.INTACT, ["No negative evidence found; business and trend checks hold"])


def averaging_check(
    avg_price: float,
    t: Technicals,
    stock_return_since_buy: float | None,
    index_return_since_buy: float | None,
    status: StatusResult,
    quality: float | None,
) -> dict[str, object] | None:
    """Only shown when the holding is below the buy price."""
    if t.close >= avg_price:
        return None
    loss = t.close / avg_price - 1
    lines = [f"Down {abs(loss) * 100:.1f}% from your average price of Rs {avg_price:,.2f}."]
    company_specific = None
    if stock_return_since_buy is not None and index_return_since_buy is not None:
        gap = stock_return_since_buy - index_return_since_buy
        company_specific = gap < -0.10
        lines.append(
            f"Since you bought, the stock moved {stock_return_since_buy * 100:+.1f}% and the Nifty 50 "
            f"{index_return_since_buy * 100:+.1f}%. "
            + ("The fall is specific to this company." if company_specific else "The fall is broadly in line with the market.")
        )
    business_holds = status.status == HoldingStatus.INTACT and (quality is None or quality >= 3.5)
    lines.append(
        "Business numbers still hold up and no negative evidence was found."
        if business_holds
        else "The evidence does not support the original case: " + "; ".join(status.reasons[:3]) + "."
    )
    return {"loss": loss, "company_specific": company_specific, "business_holds": business_holds, "lines": lines}


def tax_timing(buy_date: date | None, today: date, in_profit: bool) -> dict[str, object] | None:
    if buy_date is None:
        return None
    long_term_on = buy_date + timedelta(days=LONG_TERM_DAYS + 1)
    days_left = (long_term_on - today).days
    if days_left <= 0:
        return {"long_term": True, "days_left": 0, "date": long_term_on,
                "line": "Held over 12 months: gains are taxed at the lower long-term rate."}
    line = f"Becomes long-term on {long_term_on:%d %b %Y} ({days_left} days)."
    flag = in_profit and days_left <= TAX_FLAG_DAYS
    if flag:
        line += " Selling before then means the higher short-term tax rate on this gain."
    return {"long_term": False, "days_left": days_left, "date": long_term_on, "line": line, "flag": flag}


def concentration(values: dict[str, float], sectors: dict[str, str | None]) -> dict[str, object]:
    total = sum(values.values())
    if total <= 0:
        return {"total": 0, "stocks": [], "sectors": [], "flags": []}
    stocks = sorted(((s, v / total) for s, v in values.items()), key=lambda x: -x[1])
    by_sector: dict[str, float] = {}
    for s, v in values.items():
        key = sectors.get(s) or "Unknown"
        by_sector[key] = by_sector.get(key, 0) + v / total
    sector_list = sorted(by_sector.items(), key=lambda x: -x[1])
    flags = [f"{s} is {w * 100:.0f}% of your portfolio" for s, w in stocks if w > 0.25]
    flags += [f"{sec} sector is {w * 100:.0f}% of your portfolio" for sec, w in sector_list if w > 0.40 and sec != "Unknown"]
    return {"total": total, "stocks": stocks, "sectors": sector_list, "flags": flags}
