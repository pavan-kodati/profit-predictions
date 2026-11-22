"""Why a pick moved, and whether skipping a stock was right (plan v3, section 4). Pure functions.

The reason is chosen by fixed rules from things that moved with the price. That is evidence of the cause, not
proof, so the wording stays modest and "no clear reason found" is a normal answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

BUSINESS, MARKET, SECTOR, HYPE_FADED, FLAT, UNKNOWN = "business", "market", "sector", "hype", "flat", "unknown"
TITLE = {
    BUSINESS: "The business",
    MARKET: "The whole market",
    SECTOR: "Its sector",
    HYPE_FADED: "Excitement faded",
    FLAT: "Hardly moved",
    UNKNOWN: "No clear reason found",
}

FLAT_BAND = 0.01  # moves smaller than this are not explained
MARKET_MIN = 0.015  # the Nifty must move at least this much to count as a market-wide move
SECTOR_MIN = 0.02
SHARE_OF_MOVE = 0.5  # the market or sector must cover at least this share of the stock's move


@dataclass(frozen=True)
class EventNote:
    day: date
    title: str
    polarity: int  # +1 good news, -1 bad news, 0 unclear
    url: str | None = None


@dataclass(frozen=True)
class Headline:
    title: str
    url: str


@dataclass(frozen=True)
class Reason:
    key: str
    text: str
    links: list[dict[str, str]] = field(default_factory=list)

    @property
    def title(self) -> str:
        return TITLE[self.key]


def _pct(x: float) -> str:
    return f"{abs(x) * 100:.1f}%"


def _short(text: str, n: int = 110) -> str:
    text = " ".join(text.split()).rstrip(".")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _same_way(a: float, b: float) -> bool:
    return (a > 0) == (b > 0)


def explain(
    stock_ret: float,
    index_ret: float | None,
    sector_ret: float | None,
    events: Sequence[EventNote],
    is_hype: bool,
    market_headlines: Sequence[Headline] = (),
) -> Reason:
    """Pick the first rule that fits: business event, whole market, sector, faded excitement, or unknown."""
    if abs(stock_ret) < FLAT_BAND:
        return Reason(FLAT, "The price has barely changed, so there is nothing to explain yet.")
    rose = stock_ret > 0

    matching = [e for e in events if e.polarity != 0 and (e.polarity > 0) == rose]
    if matching:
        e = max(matching, key=lambda x: x.day)
        text = f"{'Good' if rose else 'Bad'} company news while it was held: {_short(e.title)} ({e.day:%d %b})."
        return Reason(BUSINESS, text, [{"title": "the announcement", "url": e.url}] if e.url else [])

    if (index_ret is not None and abs(index_ret) >= MARKET_MIN and _same_way(index_ret, stock_ret)
            and abs(index_ret) >= abs(stock_ret) * SHARE_OF_MOVE):
        text = (f"The Nifty 50 {'rose' if index_ret > 0 else 'fell'} {_pct(index_ret)} over the same days. "
                f"Most shares {'rose' if index_ret > 0 else 'fell'} with it.")
        return Reason(MARKET, text, [{"title": h.title, "url": h.url} for h in market_headlines[:2]])

    if (sector_ret is not None and abs(sector_ret) >= SECTOR_MIN and _same_way(sector_ret, stock_ret)
            and abs(sector_ret) >= abs(stock_ret) * SHARE_OF_MOVE):
        return Reason(SECTOR, f"Shares in the same industry typically {'rose' if sector_ret > 0 else 'fell'} "
                              f"{_pct(sector_ret)} over the same days.")

    if is_hype and not rose:
        return Reason(HYPE_FADED, "This was a 'mostly excitement' pick. The excitement cooled and no company news "
                                  "replaced it.")

    if index_ret is None:
        market = ""
    elif abs(index_ret) < 0.005:
        market = " The Nifty 50 was flat over the same days."
    else:
        market = f" The Nifty 50 moved {index_ret * 100:+.1f}% over the same days."
    return Reason(UNKNOWN, f"No company filing, market move or sector move explains this.{market}")


# ---------------------------------------------------------------- rejected stocks

RIGHT, LITTLE, MISSED = "right", "little", "missed"
VERDICT_TITLE = {RIGHT: "Right to skip", LITTLE: "Made little difference", MISSED: "Missed a gain"}
VERDICT_BAND = 0.03
REJECTION_TRACK_DAYS = 10


def skip_verdict(ret: float, index_ret: float | None) -> str:
    """Was saying no right? Compared with simply holding the Nifty 50 over the same days."""
    diff = ret - (index_ret or 0.0)
    if ret > 0 and diff > VERDICT_BAND:
        return MISSED
    if ret < 0 or diff < -VERDICT_BAND:
        return RIGHT
    return LITTLE
