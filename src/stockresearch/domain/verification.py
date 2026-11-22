"""Event verification and AI evidence validation (plan sections 2.2 and 2.3)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from stockresearch.domain.models import SourceRef, Tier, Verification

STATUS_WEIGHT = {Verification.VERIFIED: 1.0, Verification.REPORTED: 0.5, Verification.UNVERIFIED: 0.0}


def verify(sources: Iterable[SourceRef]) -> Verification:
    """Official record, or two independent press outlets = verified. One outlet = reported. Else unverified."""
    sources = list(sources)
    if any(s.tier == Tier.OFFICIAL for s in sources):
        return Verification.VERIFIED
    press = {s.publisher for s in sources if s.tier == Tier.PRESS}
    if len(press) >= 2:
        return Verification.VERIFIED
    if len(press) == 1:
        return Verification.REPORTED
    return Verification.UNVERIFIED


def trust_label(sources: Iterable[SourceRef]) -> str:
    sources = list(sources)
    official = sorted({s.publisher for s in sources if s.tier == Tier.OFFICIAL})
    if official:
        return f"Official {' and '.join(official)} filing"
    press = sorted({s.publisher for s in sources if s.tier == Tier.PRESS})
    if len(press) >= 2:
        return "Reported by " + " and ".join(press[:3])
    if press:
        return f"Single source: {press[0]}"
    opinion = sorted({s.publisher for s in sources if s.tier == Tier.OPINION})
    if opinion:
        return f"Opinion published by {opinion[0]}"
    return "Unverified"


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\"'“”‘’`]")


def _normalise(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub("", text)).strip().lower()


def quote_in_document(quote: str, document_text: str, min_len: int = 12) -> bool:
    """The AI's quote must appear in the stored document text (whitespace, case and quote marks ignored)."""
    q = _normalise(quote)
    if len(q) < min_len:
        return False
    return q in _normalise(document_text)


_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?")


def numbers_in(text: str) -> set[float]:
    out: set[float] = set()
    for whole, frac in _NUMBER.findall(text):
        try:
            out.add(round(float(whole.replace(",", "") + (f".{frac}" if frac else "")), 2))
        except ValueError:
            continue
    return out


def numbers_supported(claim: str, quote: str) -> bool:
    """Every number stated in the claim must appear in the quote it rests on."""
    claim_numbers = numbers_in(claim)
    if not claim_numbers:
        return True
    return claim_numbers <= numbers_in(quote)


def value_supported(value_cr: float | None, quote: str) -> bool:
    if value_cr is None:
        return True
    nums = numbers_in(quote)
    candidates = {round(value_cr, 2), round(value_cr, 1), round(value_cr, 0)}
    # Allow the quote to state the value in lakh or plain rupees as well as crore.
    candidates |= {round(value_cr * 100, 2), round(value_cr * 1e7, 0)}
    return bool(nums & candidates)
