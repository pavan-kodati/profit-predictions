"""The six area scores (plan section 3.2). Each returns a 1-5 score (or None = no data) and plain reasons."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from stockresearch.domain.models import (
    AreaScore,
    EventFact,
    EventType,
    Fundamentals,
    Ownership,
    Technicals,
    Verification,
)
from stockresearch.domain.technicals import is_overheated, is_speculative
from stockresearch.domain.verification import STATUS_WEIGHT

TRIGGER_WINDOW_DAYS = 10
NEGATIVE_WINDOW_DAYS = 30


def clamp(x: float) -> float:
    return round(max(1.0, min(5.0, x)), 2)


def pct(x: float | None, signed: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


# ---------------------------------------------------------------- business quality

def score_quality(f: Fundamentals) -> AreaScore:
    if not f.has_data():
        return AreaScore(None, ["Financial data not found"])
    s, why = 3.0, []

    def growth(value: float | None, label: str, strong: float = 0.25, ok: float = 0.10, weight: float = 0.6) -> None:
        nonlocal s
        if value is None:
            return
        if value > strong:
            s += weight
            why.append(f"+ {label} growing fast ({pct(value)})")
        elif value > ok:
            s += weight / 2
            why.append(f"+ {label} growing ({pct(value)})")
        elif value < 0:
            s -= weight
            why.append(f"- {label} falling ({pct(value)})")

    growth(f.revenue_growth, "Sales (latest year on year)")
    growth(f.earnings_growth, "Profit (latest year on year)")
    growth(f.revenue_cagr_3y, "Sales (3-year yearly average)", strong=0.20, ok=0.10, weight=0.4)
    growth(f.profit_cagr_3y, "Profit (3-year yearly average)", strong=0.20, ok=0.10, weight=0.4)

    q = [x for x in f.quarters if x.profit_cr is not None]
    if len(q) >= 3 and q[0].profit_cr is not None and q[1].profit_cr is not None and q[2].profit_cr is not None:
        if q[0].profit_cr < q[1].profit_cr < q[2].profit_cr:
            s -= 0.5
            why.append("- Quarterly profit has fallen two quarters in a row")
        elif q[0].profit_cr > q[1].profit_cr > q[2].profit_cr:
            s += 0.3
            why.append("+ Quarterly profit has risen two quarters in a row")

    m = f.profit_margin
    if m is not None:
        if m < 0:
            s -= 1.0
            why.append("- The company is making a loss")
        elif m < 0.03:
            s -= 0.4
            why.append(f"- Thin profit margin ({pct(m, False)})")
        elif m > 0.15:
            s += 0.3
            why.append(f"+ Healthy profit margin ({pct(m, False)})")
    d = f.debt_to_equity
    if d is not None and (f.sector or "") != "Financial Services":
        if d > 1.5:
            s -= 0.6
            why.append(f"- High debt ({d:.1f}x its own capital)")
        elif d < 0.5:
            s += 0.3
            why.append(f"+ Low debt ({d:.1f}x its own capital)")
    r = f.roe
    if r is not None:
        if r > 0.18:
            s += 0.3
            why.append(f"+ High return on shareholder money ({pct(r, False)})")
        elif r < 0.08:
            s -= 0.3
            why.append(f"- Low return on shareholder money ({pct(r, False)})")
    if f.pe is not None and f.pe > 80:
        s -= 0.3
        why.append(f"- Expensive: priced at {f.pe:.0f}x yearly profit, so much good news is already in the price")
    elif f.pe is not None and f.pe <= 0:
        why.append("Price-to-profit ratio not meaningful (loss or negative earnings)")
    return AreaScore(clamp(s), why)


# ---------------------------------------------------------------- price and volume

def score_price(t: Technicals) -> AreaScore:
    s, why = 3.0, []
    if t.close > t.dma50:
        s += 0.4
        why.append("+ Trading above its 50-day average price (uptrend)")
    else:
        s -= 0.4
        why.append("- Trading below its 50-day average price")
    if t.dma200 is not None:
        if t.close > t.dma200:
            s += 0.3
            why.append("+ Trading above its 200-day average price (long-term uptrend)")
        else:
            s -= 0.3
            why.append("- Trading below its 200-day average price (long-term downtrend)")
    vr = t.vol_ratio or 0
    if vr >= 2:
        s += 0.5
        why.append(f"+ Trading volume this week is {vr:.1f}x normal (strong interest)")
    elif vr >= 1.5:
        s += 0.25
        why.append(f"+ Trading volume this week is {vr:.1f}x normal")
    if t.delivery_5d is not None and t.delivery_50d is not None:
        if t.delivery_5d > t.delivery_50d * 1.15:
            s += 0.4
            why.append(
                f"+ More buyers are taking delivery: {t.delivery_5d:.0f}% of traded shares held this week vs "
                f"{t.delivery_50d:.0f}% usually"
            )
        if is_speculative(t):
            s -= 0.5
            why.append(
                f"- Volume is up but delivery fell to {t.delivery_5d:.0f}% (usually {t.delivery_50d:.0f}%): "
                "mostly same-day trading, not investors buying"
            )
    if (t.ret_20d or 0) > 0.10:
        s += 0.3
        why.append(f"+ Up {pct(t.ret_20d)} over the last month")
    if t.from_high >= -0.05:
        s += 0.3
        why.append("+ Within 5% of its one-year high")
    if is_overheated(t):
        s -= 0.8
        why.append(
            f"- Already up {pct(t.ret_5d)} this week and {pct(t.ext_20dma)} above its 20-day average: late-entry risk"
        )
    if (t.ret_5d or 0) < -0.05:
        s -= 0.4
        why.append(f"- Fell {pct(t.ret_5d)} this week")
    return AreaScore(clamp(s), why)


# ---------------------------------------------------------------- trigger

_IMPACT = {
    EventType.RESULTS: 1.0,
    EventType.EXPANSION: 0.6,
    EventType.APPROVAL: 0.75,
    EventType.ACQUISITION: 0.6,
    EventType.STAKE_BUY: 0.75,
    EventType.FUNDRAISE: 0.25,
    EventType.OTHER: 0.25,
}


def _order_impact(value_cr: float | None, annual_revenue_cr: float | None) -> tuple[float, str]:
    if value_cr is None or not annual_revenue_cr:
        return 0.5, "value not stated"
    share = value_cr / annual_revenue_cr
    if share >= 0.10:
        return 1.5, f"Rs {value_cr:,.0f} crore, {share * 100:.0f}% of yearly sales"
    if share >= 0.03:
        return 1.0, f"Rs {value_cr:,.0f} crore, {share * 100:.0f}% of yearly sales"
    return 0.4, f"Rs {value_cr:,.0f} crore, only {share * 100:.1f}% of yearly sales"


def recent(events: Sequence[EventFact], today: date, days: int) -> list[EventFact]:
    since = today - timedelta(days=days)
    return [e for e in events if since <= e.event_date <= today]


def score_trigger(events: Sequence[EventFact], annual_revenue_cr: float | None, today: date) -> AreaScore:
    positives = [e for e in recent(events, today, TRIGGER_WINDOW_DAYS) if e.polarity > 0]
    negatives = [e for e in recent(events, today, NEGATIVE_WINDOW_DAYS) if e.polarity < 0]
    s, why = 3.0, []
    boost = 0.0
    for e in sorted(positives, key=lambda x: x.event_date, reverse=True):
        weight = STATUS_WEIGHT[e.status]
        if e.event_type == EventType.ORDER_WIN:
            impact, detail = _order_impact(e.value_cr, annual_revenue_cr)
        else:
            impact, detail = _IMPACT.get(e.event_type, 0.25), ""
        label = {Verification.VERIFIED: "verified", Verification.REPORTED: "single source, half weight",
                 Verification.UNVERIFIED: "unverified, not counted"}[e.status]
        if weight:
            boost += impact * weight
        why.append(f"{'+' if weight else '?'} {e.event_date:%d %b}: {e.title}{f' ({detail})' if detail else ''} [{label}]")
    s += min(boost, 2.0)
    for e in negatives:
        weight = STATUS_WEIGHT[e.status]
        if weight:
            s -= 1.5 * weight
            why.append(f"- {e.event_date:%d %b}: {e.title} [{e.status.value}]")
    if not positives:
        why.append("No fresh positive trigger found in the last 10 days")
    return AreaScore(clamp(s), why)


def best_positive_status(events: Sequence[EventFact], today: date) -> Verification:
    statuses = {e.status for e in recent(events, today, TRIGGER_WINDOW_DAYS) if e.polarity > 0}
    for status in (Verification.VERIFIED, Verification.REPORTED):
        if status in statuses:
            return status
    return Verification.UNVERIFIED


def verified_negatives(events: Sequence[EventFact], today: date) -> list[EventFact]:
    """Red-flag events (plan E6): auditor exit, regulator action, insolvency, promoter selling, pledge increase.

    A weak quarter lowers the trigger score but is not a red flag, so it is not included here.
    """
    return [
        e for e in recent(events, today, NEGATIVE_WINDOW_DAYS)
        if e.event_type == EventType.NEGATIVE and e.polarity < 0 and e.status == Verification.VERIFIED
    ]


# ---------------------------------------------------------------- order book

def score_order_book(
    order_book_cr: float | None,
    events: Sequence[EventFact],
    annual_revenue_cr: float | None,
    today: date,
) -> AreaScore:
    wins = [e for e in recent(events, today, 90) if e.event_type == EventType.ORDER_WIN and e.polarity > 0
            and e.status != Verification.UNVERIFIED and e.value_cr]
    wins_cr = sum(e.value_cr or 0 for e in wins)
    if order_book_cr is None and not wins:
        return AreaScore(None, ["Order book not found in filings (not relevant for every business)"])
    if not annual_revenue_cr:
        return AreaScore(None, ["Order book found but yearly sales unknown, so it cannot be sized"])
    s, why = 3.0, []
    if order_book_cr is not None:
        cover = order_book_cr / annual_revenue_cr
        why.append(f"Pending orders of Rs {order_book_cr:,.0f} crore = {cover:.1f}x yearly sales")
        if cover >= 3:
            s = 5.0
        elif cover >= 2:
            s = 4.5
        elif cover >= 1:
            s = 4.0
        elif cover >= 0.5:
            s = 3.5
    if wins_cr:
        share = wins_cr / annual_revenue_cr
        why.append(f"+ New orders worth Rs {wins_cr:,.0f} crore in 90 days ({share * 100:.0f}% of yearly sales)")
        s += min(share * 3, 0.75)
    return AreaScore(clamp(s), why)


# ---------------------------------------------------------------- ownership

def score_ownership(o: Ownership) -> AreaScore:
    if not o.has_data():
        return AreaScore(None, ["Holding data not found"])
    s, why = 3.0, []
    as_of = f" (as of {o.as_of:%b %Y})" if o.as_of else ""
    if o.promoter_pct is not None:
        label = f"Promoters hold {o.promoter_pct:.1f}%{as_of}"
        if o.promoter_pct >= 50:
            s += 0.5
            why.append(f"+ {label}: strong control")
        elif o.promoter_pct >= 35:
            s += 0.2
            why.append(f"+ {label}")
        elif o.promoter_pct < 20:
            s -= 0.5
            why.append(f"- {label}: low")
        else:
            why.append(label)
    for change, period in ((o.promoter_change_qoq, "last quarter"), (o.promoter_change_yoy, "last year")):
        if change is None or abs(change) < 0.5:
            continue
        if change > 0:
            s += 0.4
            why.append(f"+ Promoters raised their stake by {change:.1f} points over the {period}")
        else:
            s -= 0.6
            why.append(f"- Promoters cut their stake by {abs(change):.1f} points over the {period}")
        break
    if o.pledged_pct is not None:
        if o.pledged_pct > 10:
            s -= 0.6
            why.append(f"- {o.pledged_pct:.1f}% of promoter shares are pledged")
        elif o.pledged_pct == 0:
            s += 0.2
            why.append("+ No promoter shares pledged")
    if o.institutional_pct is not None:
        if o.institutional_pct >= 15:
            s += 0.3
            why.append(f"+ Funds and institutions hold about {o.institutional_pct:.0f}%")
        elif o.institutional_pct < 2:
            s -= 0.3
            why.append(f"- Very little fund or institutional money ({o.institutional_pct:.1f}%)")
    if o.promoter_net_buy_cr_90d:
        if o.promoter_net_buy_cr_90d > 0:
            s += 0.5
            why.append(f"+ Promoters/insiders bought Rs {o.promoter_net_buy_cr_90d:,.1f} crore of shares in 90 days")
        else:
            s -= 0.5
            why.append(f"- Promoters/insiders sold Rs {abs(o.promoter_net_buy_cr_90d):,.1f} crore of shares in 90 days")
    if o.fund_bulk_buys_30d:
        s += min(0.2 * o.fund_bulk_buys_30d, 0.5)
        why.append(f"+ {o.fund_bulk_buys_30d} large purchase(s) by funds or institutions in 30 days (bulk/block deals)")
    if o.fund_bulk_sells_30d:
        s -= min(0.2 * o.fund_bulk_sells_30d, 0.5)
        why.append(f"- {o.fund_bulk_sells_30d} large sale(s) by funds or institutions in 30 days (bulk/block deals)")
    why.append("Mutual fund and foreign investor breakdown is not yet read from filings; institutional total shown")
    return AreaScore(clamp(s), why)


# ---------------------------------------------------------------- sentiment

def score_sentiment(press_stances: Sequence[str], opinion_stances: Sequence[str]) -> AreaScore:
    """Stances are 'positive' | 'neutral' | 'negative', one per allowlisted article."""
    if not press_stances and not opinion_stances:
        return AreaScore(None, ["No coverage from approved outlets in the last 14 days"])

    def balance(stances: Sequence[str]) -> float:
        if not stances:
            return 0.0
        return (stances.count("positive") - stances.count("negative")) / len(stances)

    press, opinion = balance(press_stances), balance(opinion_stances)
    s = 3.0 + 1.5 * press + 0.5 * opinion
    why = []
    if press_stances:
        why.append(
            f"Press coverage: {press_stances.count('positive')} positive, {press_stances.count('neutral')} neutral, "
            f"{press_stances.count('negative')} negative article(s)"
        )
    if opinion_stances:
        why.append(
            f"Analyst views (counted lightly): {opinion_stances.count('positive')} positive, "
            f"{opinion_stances.count('negative')} negative"
        )
    return AreaScore(clamp(s), why)
