"""Money formatting shared by the rules and the presentation layer."""

from __future__ import annotations


def inr(amount: float | None, decimals: int = 0, signed: bool = False) -> str:
    """Indian rupee format with lakh grouping: 174903 -> '₹1,74,903'."""
    if amount is None:
        return "n/a"
    value = float(amount)
    sign = "-" if value < 0 else ("+" if signed else "")
    whole, _, frac = f"{abs(value):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}₹{whole}" + (f".{frac}" if decimals else "")
