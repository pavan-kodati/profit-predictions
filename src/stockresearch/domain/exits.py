"""Exit-alert rules for shares the user holds (plan E1, E6). Alerts inform; the decision stays with the user."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stockresearch.domain.models import EventFact, Technicals


@dataclass(frozen=True)
class AlertSignal:
    kind: str
    severity: str  # info | warn | exit
    message: str
    key: str  # stable part of the dedupe key


def check(
    symbol: str,
    horizon: str,
    avg_price: float,
    buy_date: date | None,
    stop_price: float | None,
    t: Technicals,
    new_negative_events: list[EventFact],
) -> list[AlertSignal]:
    out: list[AlertSignal] = []
    gain = t.close / avg_price - 1 if avg_price else 0.0
    if stop_price and t.close <= stop_price:
        out.append(AlertSignal(
            "stop_hit", "exit",
            f"{symbol} closed at Rs {t.close:,.2f}, at or below your stop level of Rs {stop_price:,.2f} "
            f"({gain * 100:+.1f}% from your buy price).",
            f"stop:{stop_price}",
        ))
    for e in new_negative_events:
        out.append(AlertSignal(
            "negative_event", "exit",
            f"{symbol}: negative {e.status.value} event on {e.event_date:%d %b}: {e.title}.",
            f"neg:{e.event_date}:{e.title[:60]}",
        ))
    if horizon == "short":
        if t.close < t.dma10:
            out.append(AlertSignal(
                "below_short_average", "warn",
                f"{symbol} closed below its 10-day average price (Rs {t.dma10:,.2f}). Short-term momentum is fading.",
                f"dma10:{t.as_of}",
            ))
        if (t.vol_ratio or 1) < 0.6:
            out.append(AlertSignal(
                "volume_dried", "warn",
                f"{symbol}: trading volume has dropped to {t.vol_ratio:.1f}x normal. Interest is fading.",
                f"vol:{t.as_of.isocalendar().week}",
            ))
        if gain >= 0.10:
            out.append(AlertSignal(
                "target_reached", "info",
                f"{symbol} is up {gain * 100:.1f}% from your buy price (Rs {avg_price:,.2f} -> Rs {t.close:,.2f}).",
                f"plus10:{int(gain * 20)}",
            ))
    elif t.dma200 is not None and t.close < t.dma200:
        out.append(AlertSignal(
            "below_long_average", "warn",
            f"{symbol} closed below its 200-day average price (Rs {t.dma200:,.2f}). The long-term trend has turned.",
            f"dma200:{t.as_of.isocalendar().week}",
        ))
    return out
