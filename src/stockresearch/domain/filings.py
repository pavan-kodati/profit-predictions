"""Map NSE filing categories to event types. Clear-cut categories are scored without AI; the rest need reading."""

from __future__ import annotations

from stockresearch.domain.models import EventType

# category text (lower case, substring match) -> (event type, polarity)
_CLEAR: list[tuple[str, EventType, int]] = [
    ("bagging/receiving of orders", EventType.ORDER_WIN, 1),
    ("awarding of order", EventType.ORDER_WIN, 1),
    ("capacity addition", EventType.EXPANSION, 1),
    ("commencement of commercial production", EventType.EXPANSION, 1),
    ("product launch", EventType.EXPANSION, 1),
    ("acquisition", EventType.ACQUISITION, 1),
    ("open offer", EventType.STAKE_BUY, 1),
    ("buy back", EventType.STAKE_BUY, 1),
    ("buyback", EventType.STAKE_BUY, 1),
    ("resignation of statutory auditor", EventType.NEGATIVE, -1),
    ("resignation of auditor", EventType.NEGATIVE, -1),
    ("corporate insolvency", EventType.NEGATIVE, -1),
    ("insolvency and bankruptcy", EventType.NEGATIVE, -1),
    ("default", EventType.NEGATIVE, -1),
    ("delayed/non-submission of financial results", EventType.NEGATIVE, -1),
    # Routine for large companies (tax orders, small regulatory fines, pending cases): a negative signal that lowers
    # the trigger score and shows as evidence against, but not a red flag that excludes the stock.
    ("pendency of litigation", EventType.OTHER, -1),
    ("action(s) taken or orders passed", EventType.OTHER, -1),
    ("fraud", EventType.NEGATIVE, -1),
    ("one time settlement", EventType.NEGATIVE, -1),
    ("qualified institutional placement", EventType.FUNDRAISE, 0),
    ("preferential issue", EventType.FUNDRAISE, 0),
    ("rights issue", EventType.FUNDRAISE, 0),
]

# Categories worth reading (by the AI) because they often carry triggers, but whose direction is not clear.
_NEEDS_READING = (
    "outcome of board meeting",
    "financial result",
    "press release",
    "investor presentation",
    "general updates",
    "updates",
    "agreements",
    "disclosure of material issue",
    "allotment",
    "credit rating",
    "change in management",
    "resignation of director",
    "resignation of managing director",
)

# Routine paperwork: stored, never scored, never read.
_ROUTINE = (
    "analysts/institutional investor meet",
    "copy of newspaper publication",
    "trading window",
    "esop",
    "record date",
    "shareholders meeting",
    "certificate under",
    "compliance certificate",
    "monitoring agency",
    "loss of share certificate",
    "duplicate share",
    "spurt in volume",
    "clarification",
)


def classify_filing(category: str) -> tuple[EventType, int] | None:
    """Return (type, polarity) for clear-cut filings, None otherwise."""
    cat = (category or "").lower()
    if any(r in cat for r in _ROUTINE):
        return None
    for needle, etype, polarity in _CLEAR:
        if needle in cat:
            return etype, polarity
    return None


def needs_reading(category: str) -> bool:
    cat = (category or "").lower()
    if any(r in cat for r in _ROUTINE):
        return False
    return classify_filing(cat) is not None or any(n in cat for n in _NEEDS_READING)


def is_material(category: str) -> bool:
    """Material = worth storing as evidence and queueing research for."""
    return needs_reading(category)


# Long-only institutions. Broker trading desks are deliberately excluded: they bulk-trade daily for themselves.
FUND_KEYWORDS = (" MUTUAL FUND", " MF ", " ASSET MANAGEMENT", " INSURANCE", " PENSION", " FUND ", " FUNDS ")


def is_institution(client_name: str) -> bool:
    name = f" {client_name.upper().replace('-', ' ')} "
    return any(k in name for k in FUND_KEYWORDS)
