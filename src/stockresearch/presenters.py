"""Plain sentences for people new to the market, used by both the web pages and the emails (plan v2, section 3).

Kept out of the domain rules on purpose: wording can change freely without touching how ratings are computed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

import numpy as np

from stockresearch.domain.models import PICK_TYPE_LABEL, PickType
from stockresearch.domain.practice import HOLD_DAYS

LABEL_EXPLAIN = {
    PickType.FUNDAMENTALS: "The company itself reported something solid, like a big profit jump, and the price hasn't run too far yet.",
    PickType.MIXED: "There's a real reason to like it, but also a catch: the news is only partly confirmed, the business is average, "
                    "or the price has already jumped.",
    PickType.HYPE: "The price is rising mainly because people are excited, not because the business has improved.",
}

LABEL_TONE = {PickType.FUNDAMENTALS: "good", PickType.MIXED: "mid", PickType.HYPE: "bad"}


def rating_words(rating: float) -> tuple[str, str]:
    """(plain title, tone) for a 1-5 rating."""
    if rating >= 4.5:
        return "Very strong case", "good"
    if rating >= 4.0:
        return "Strong case", "good"
    if rating >= 3.5:
        return "Good case", "good"
    if rating >= 2.75:
        return "Not strong enough yet: wait and watch", "mid"
    if rating >= 2.0:
        return "Weak case", "bad"
    return "Avoid", "bad"


def _pct(text: str) -> str:
    return f"{abs(float(text)):.0f}%"


def _apply(pattern: str, fn: Callable[[re.Match[str]], str]) -> tuple[re.Pattern[str], Callable[[re.Match[str]], str]]:
    return re.compile(pattern, re.IGNORECASE), fn


# Technical reason (as written by the scoring rules) -> everyday sentence.
_RULES = [
    _apply(r"Sales \(latest year on year\) growing(?: fast)? \(([-+\d.]+)%\)", lambda g: f"Sales are up {_pct(g[1])} on a year ago."),
    _apply(r"Profit \(latest year on year\) growing(?: fast)? \(([-+\d.]+)%\)", lambda g: f"Profit is up {_pct(g[1])} on a year ago."),
    _apply(r"Sales \(3-year yearly average\) growing(?: fast)? \(([-+\d.]+)%\)", lambda g: f"Sales have grown about {_pct(g[1])} a year for 3 years."),
    _apply(r"Profit \(3-year yearly average\) growing(?: fast)? \(([-+\d.]+)%\)", lambda g: f"Profit has grown about {_pct(g[1])} a year for 3 years."),
    _apply(r"Sales \(latest year on year\) falling \(([-+\d.]+)%\)", lambda g: f"Sales are down {_pct(g[1])} on a year ago."),
    _apply(r"Profit \(latest year on year\) falling \(([-+\d.]+)%\)", lambda g: f"Profit is down {_pct(g[1])} on a year ago."),
    _apply(r"(Sales|Profit) \(3-year yearly average\) falling", lambda g: f"{g[1]} has been shrinking over 3 years."),
    _apply(r"Low debt", lambda g: "The company has little debt."),
    _apply(r"High debt", lambda g: "The company has borrowed a lot of money."),
    _apply(r"Healthy profit margin \(([\d.]+)%\)", lambda g: f"It keeps {_pct(g[1])} of every rupee of sales as profit, which is healthy."),
    _apply(r"Thin profit margin", lambda g: "It keeps very little of its sales as profit."),
    _apply(r"High return on shareholder money", lambda g: "It makes good profit on the money its owners put in."),
    _apply(r"Low return on shareholder money", lambda g: "It makes little profit on the money its owners put in."),
    _apply(r"Expensive: priced at (\d+)x", lambda g: f"The share is already expensive ({g[1]} times its yearly profit), so a lot of good news is priced in."),
    _apply(r"Already up ([-+\d.]+)% this week", lambda g: f"The price already jumped {_pct(g[1])} this week, so you may be buying late."),
    _apply(r"Volume is up but delivery fell", lambda g: "Most of the recent buying is quick same-day trading, not long-term investors."),
    _apply(r"Trading volume this week is ([\d.]+)x normal", lambda g: f"{float(g[1]):.1f} times more shares traded than usual this week: people are paying attention."),
    _apply(r"More buyers are taking delivery", lambda g: "More buyers are keeping the shares instead of selling the same day, a sign of real investors."),
    _apply(r"Within 5% of its one-year high", lambda g: "The price is close to its highest level of the past year."),
    _apply(r"Up ([-+\d.]+)% over the last month", lambda g: f"The price is up {_pct(g[1])} over the past month."),
    _apply(r"Fell ([-+\d.]+)% this week", lambda g: f"The price fell {_pct(g[1])} this week."),
    _apply(r"Trading below its 200-day", lambda g: "The price has been falling over the past year."),
    _apply(r"Trading below its 50-day", lambda g: "The price has been falling over the past two months."),
    _apply(r"Trading above its 200-day", lambda g: "The price has been rising over the past year."),
    _apply(r"Trading above its 50-day", lambda g: "The price has been rising over the past two months."),
    _apply(r"Promoters cut their stake by ([\d.]+) points", lambda g: f"The company's owners sold some of their shares ({g[1]}% of the company)."),
    _apply(r"Promoters raised their stake by ([\d.]+) points", lambda g: f"The company's owners bought more of their own shares ({g[1]}% of the company)."),
    _apply(r"Promoters hold ([\d.]+)%.*strong control", lambda g: f"The founders own {_pct(g[1])} of the company, so they have a big stake in its success."),
    _apply(r"([\d.]+)% of promoter shares are pledged", lambda g: f"The owners have borrowed money against {_pct(g[1])} of their shares; forced selling is a risk."),
    _apply(r"No promoter shares pledged", lambda g: "The owners haven't borrowed against their shares."),
    _apply(r"Funds and institutions hold about (\d+)%", lambda g: f"Mutual funds and other big investors own about {g[1]}%, a sign of confidence."),
    _apply(r"large purchase\(s\) by funds", lambda g: "A mutual fund or other big investor bought a large block of shares recently."),
    _apply(r"No verified trigger", lambda g: "We couldn't find an official reason for the price move yet."),
    _apply(r"Up more than 15% in five days", lambda g: "It already rose more than 15% in five days, so most of the move may be over."),
    _apply(r"Only (\d+)% of the data was available", lambda g: f"We only found {g[1]}% of the information we look for, so we can't be confident."),
    _apply(r"Results due on (.+?), inside the holding window", lambda g: f"The company reports results on {g[1]}. Prices can swing sharply on results day."),
    _apply(r"Low liquidity", lambda g: "Few shares trade each day, so it could be hard to sell quickly."),
    _apply(r"surveillance list", lambda g: "The exchange has put it on a watch list for unusual speculation; it can become hard to sell."),
    _apply(r"locked at the upper price limit", lambda g: "It hit its maximum daily rise, so buy orders may not go through."),
    _apply(r"verified negative event", lambda g: "Something bad was officially reported in the last month."),
]
_DATE_PREFIX = re.compile(r"^\s*\d{2} \w{3}(?: \d{4})?:\s*")
_TAG = re.compile(r"\s*\[(verified|reported|single source, half weight|unverified, not counted)\]\s*$")


def plain(reason: str) -> str:
    """One technical reason as an everyday sentence. Unknown wording passes through, tidied."""
    text = reason.lstrip("+-? ").strip()
    for pattern, fn in _RULES:
        match = pattern.search(text)
        if match:
            return fn(match)
    text = _TAG.sub("", _DATE_PREFIX.sub("", text))
    return text if text.endswith(".") else text + "."


def _business_days(start: date, n: int) -> date:
    return np.busday_offset(np.datetime64(start), n, roll="forward").astype(date)


def plain_pick(report: Any, amount: float, today: date) -> dict[str, Any]:
    """The 'In plain words' box: why it might rise, what could go wrong, and a rupee plan for the user's amount."""
    d = report.data
    pick_type = PickType(report.pick_type)
    areas = d.get("areas", {})
    evidence_for = [e for e in getattr(report, "evidence", []) if e.stance == "for" and e.area == "trigger"]

    why: list[str] = []
    if d.get("thesis"):
        why.append(d["thesis"])
    else:
        why += [plain(e.claim) for e in evidence_for[:2]]
        for key in ("quality", "price", "ownership"):
            why += [plain(r) for r in areas.get(key, {}).get("reasons", []) if r.startswith("+")][:1]
    risks: list[str] = list(d.get("risks") or [])[:3]
    if len(risks) < 3:
        negatives = [r for key in ("price", "quality", "ownership", "trigger")
                     for r in areas.get(key, {}).get("reasons", []) if r.startswith("-")]
        risks += [plain(r) for r in [*d.get("caps", []), *d.get("gates", {}).get("flags", []), *negatives]]
    if report.excluded_reason:
        risks.insert(0, plain(report.excluded_reason))
    seen: set[str] = set()
    risks = [r for r in risks if not (r in seen or seen.add(r))][:3]  # type: ignore[func-returns-value]

    close = float(d["tech"]["close"])
    stop = float(d["levels"]["stop"])
    shares = max(int(amount // close), 1) if close else 0
    hold = HOLD_DAYS.get(pick_type, 10)
    title, tone = rating_words(report.rating)
    return {
        "rating_title": title, "rating_tone": tone,
        "label": PICK_TYPE_LABEL[pick_type], "label_explain": LABEL_EXPLAIN[pick_type], "label_tone": LABEL_TONE[pick_type],
        "why": why[:3] or ["No clear reason found yet."],
        "risks": risks or ["No specific risk found, but any share can fall."],
        "plan": {
            "amount": amount, "price": close, "shares": shares, "cost": shares * close, "stop": stop,
            "loss": shares * (close - stop), "review_by": _business_days(today, hold), "hold_days": hold,
            "stop_pct": (close - stop) / close if close else 0.0,
            "over_budget": close > amount,  # one share costs more than the amount, so one share is assumed
        },
    }


TRADE_EXIT_WORDS = {
    "manual": "at the opening price, because you chose to sell",
    "window": "when its holding window ended",
    "stop": "when it fell to the safety level",
    "gap": "at the opening price, after it opened below the safety level",
}
