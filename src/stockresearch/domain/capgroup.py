"""Company size groups (plan v3, section 3). Pure functions: no database, no network.

The group comes from NSE's official index lists: Nifty 100 is "large", Nifty Midcap 150 is "mid", and every other
stock the app scans is "small". If the lists could not be downloaded, market value is used as a fallback.
"""

from __future__ import annotations

from collections.abc import Collection

LARGE, MID, SMALL = "large", "mid", "small"
ORDER = (LARGE, MID, SMALL)
LABEL = {LARGE: "Large companies", MID: "Mid-size companies", SMALL: "Small companies"}
SINGULAR = {LARGE: "Large company", MID: "Mid-size company", SMALL: "Small company"}
HINT = {
    LARGE: "the 100 biggest in India; steadier prices",
    MID: "ranked 101 to 250; faster growth, bigger swings",
    SMALL: "ranked 251 and below; riskiest",
}

# Fallback only: rough market-value boundaries (Rs crore) of the 100th and 250th biggest listed companies.
LARGE_MIN_CR = 100_000.0
MID_MIN_CR = 33_000.0


def from_lists(symbol: str, large: Collection[str], mid: Collection[str]) -> str:
    if symbol in large:
        return LARGE
    if symbol in mid:
        return MID
    return SMALL


def from_market_cap(market_cap_cr: float | None) -> str | None:
    if not market_cap_cr or market_cap_cr <= 0:
        return None
    if market_cap_cr >= LARGE_MIN_CR:
        return LARGE
    if market_cap_cr >= MID_MIN_CR:
        return MID
    return SMALL


def resolve(stored: str | None, market_cap_cr: float | None = None) -> str:
    """The group to show: the official list if known, else market value, else small (the cautious label)."""
    if stored in ORDER:
        return str(stored)
    return from_market_cap(market_cap_cr) or SMALL
