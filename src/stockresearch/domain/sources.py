"""Source allowlist and trust tiers (plan section 2.1). Anything not listed here is blocked."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from stockresearch.domain.models import Tier

OFFICIAL_DOMAINS: dict[str, str] = {
    "nseindia.com": "NSE",
    "bseindia.com": "BSE",
}

PRESS_DOMAINS: dict[str, str] = {
    "economictimes.indiatimes.com": "Economic Times",
    "livemint.com": "Mint",
    "business-standard.com": "Business Standard",
    "moneycontrol.com": "Moneycontrol",
    "cnbctv18.com": "CNBC-TV18",
    "thehindubusinessline.com": "Hindu BusinessLine",
    "financialexpress.com": "Financial Express",
    "reuters.com": "Reuters",
    "ptinews.com": "PTI",
    "ndtvprofit.com": "NDTV Profit",
}

# Sub-sites of allowed publishers that carry user-generated or unverifiable content.
BLOCKED_HOSTS = {"mmb.moneycontrol.com", "forum.moneycontrol.com", "blogs.economictimes.indiatimes.com"}
BLOCKED_PATH_PARTS = ("/forum", "/messageboard", "/blogs/", "/opinion/readers-blog", "/sponsored", "/brand-")

# Headlines that carry a recommendation are opinion, whoever publishes them.
_OPINION_PATTERNS = re.compile(
    r"\b(target(?: price)?|buy call|sell call|stocks? to buy|stocks? to sell|top picks?|recommend\w*|"
    r"accumulate|outperform|underperform|overweight|underweight|brokerages?|price target|"
    r"rating|upgrade[sd]?|downgrade[sd]?|initiates? coverage|technical picks?|multibagger)\b",
    re.IGNORECASE,
)
_OPINION_PREFIX = re.compile(r"^\s*(buy|sell|hold|add|reduce)\b", re.IGNORECASE)


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _match(host: str, table: dict[str, str]) -> str | None:
    for domain, name in table.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


def publisher_for(url: str) -> str | None:
    host = _host(url)
    return _match(host, OFFICIAL_DOMAINS) or _match(host, PRESS_DOMAINS)


def is_opinion_headline(title: str) -> bool:
    return bool(_OPINION_PATTERNS.search(title) or _OPINION_PREFIX.search(title))


def classify(url: str, title: str = "") -> tuple[Tier, str]:
    """Return (tier, publisher name) for a document. Unknown sources are BLOCKED."""
    host = _host(url)
    if not host or host in BLOCKED_HOSTS:
        return Tier.BLOCKED, host or "unknown"
    path = (urlparse(url).path or "").lower()
    if any(part in path for part in BLOCKED_PATH_PARTS):
        return Tier.BLOCKED, host
    official = _match(host, OFFICIAL_DOMAINS)
    if official:
        return Tier.OFFICIAL, official
    press = _match(host, PRESS_DOMAINS)
    if press:
        return (Tier.OPINION if is_opinion_headline(title) else Tier.PRESS), press
    return Tier.BLOCKED, host


def allowlisted_press_sites() -> list[str]:
    return list(PRESS_DOMAINS)
