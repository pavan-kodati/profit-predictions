"""Combine area scores into the 1-5 rating, apply caps, and label hype vs substance (plan 3.2-3.4)."""

from __future__ import annotations

from dataclasses import dataclass

from stockresearch.domain.models import HOLD_GUIDE, AreaScore, GateResult, PickType, Rating, Technicals, Verification
from stockresearch.domain.technicals import is_overheated, is_speculative

WEIGHTS = {"quality": 0.25, "price": 0.20, "trigger": 0.25, "order_book": 0.10, "ownership": 0.10, "sentiment": 0.10}
AREA_LABEL = {
    "quality": "Business quality",
    "price": "Price and volume",
    "trigger": "Trigger",
    "order_book": "Order book",
    "ownership": "Ownership",
    "sentiment": "Sentiment",
}
LONG_TERM_AREAS = ("quality", "ownership", "order_book")
CAP = 3.0
MIN_COMPLETENESS = 0.60


@dataclass(frozen=True)
class AiSignals:
    future_story: bool = False  # trigger is a promise about the future rather than a delivered result
    ai_pick_type: PickType | None = None


def weighted(areas: dict[str, AreaScore], keys: tuple[str, ...] | None = None) -> tuple[float, float]:
    """Return (weighted average over areas with data, completeness = share of weight that had data)."""
    keys = keys or tuple(WEIGHTS)
    have = {k: areas[k].score for k in keys if k in areas and areas[k].score is not None}
    total = sum(WEIGHTS[k] for k in keys)
    got = sum(WEIGHTS[k] for k in have)
    if not got:
        return 3.0, 0.0
    avg = sum(WEIGHTS[k] * v for k, v in have.items() if v is not None) / got
    return round(avg, 2), round(got / total, 2)


def stars(rating: float) -> int:
    return max(1, min(5, int(rating + 0.5)))


def classify(t: Technicals, areas: dict[str, AreaScore], best_trigger: Verification, ai: AiSignals) -> PickType:
    quality = areas["quality"].score
    weak_business = quality is not None and quality < 3.0
    ran_ahead = (t.ret_60d or 0) > 0.40 and (quality is None or quality < 3.5)
    hype_signals = sum([weak_business, ran_ahead, is_speculative(t), is_overheated(t), ai.future_story])
    if hype_signals >= 2 or weak_business:
        rule = PickType.HYPE
    elif (
        best_trigger == Verification.VERIFIED
        and quality is not None
        and quality >= 3.5
        and not is_overheated(t)
        and not is_speculative(t)
        and not ai.future_story
    ):
        rule = PickType.FUNDAMENTALS
    else:
        rule = PickType.MIXED
    if ai.ai_pick_type is None:
        return rule
    # The AI can only push the label towards caution, never towards "business-backed".
    order = [PickType.FUNDAMENTALS, PickType.MIXED, PickType.HYPE]
    return max(rule, ai.ai_pick_type, key=order.index)


def rate(
    areas: dict[str, AreaScore],
    t: Technicals,
    gates: GateResult,
    best_trigger: Verification,
    has_verified_negative: bool,
    ai: AiSignals | None = None,
    long_term: bool = False,
) -> Rating:
    ai = ai or AiSignals()
    keys = LONG_TERM_AREAS if long_term else tuple(WEIGHTS)
    overall, completeness = weighted(areas, keys)
    caps: list[str] = []
    if not long_term:
        if best_trigger != Verification.VERIFIED:
            caps.append("No verified trigger: capped at 3 stars")
        if (t.ret_5d or 0) > 0.15:
            caps.append("Up more than 15% in five days (late entry): capped at 3 stars")
    if completeness < MIN_COMPLETENESS:
        caps.append(f"Only {completeness * 100:.0f}% of the data was available: capped at 3 stars")
    if caps:
        overall = min(overall, CAP)
    excluded = None
    if has_verified_negative:
        overall = min(overall, 2.0)
        excluded = "A verified negative event in the last 30 days"
    elif not gates.passed:
        excluded = "Failed a safety gate: " + "; ".join(gates.failures)
    pick_type = classify(t, areas, best_trigger, ai)
    return Rating(
        overall=round(overall, 2),
        stars=stars(overall),
        areas=areas,
        completeness=completeness,
        caps=caps,
        pick_type=pick_type,
        hold_guide=HOLD_GUIDE[pick_type],
        excluded_reason=excluded,
    )


def levels(t: Technicals) -> dict[str, float | None]:
    """Mechanical reference levels from recent volatility. Not advice: a starting point for the user's own plan."""
    risk = min(max(2 * (t.atr_pct or 0.03), 0.04), 0.12)
    return {
        "stop": round(t.close * (1 - risk), 2),
        "stop_pct": -risk,
        "plus10": round(t.close * 1.10, 2),
        "daily_move_pct": t.atr_pct,
    }
