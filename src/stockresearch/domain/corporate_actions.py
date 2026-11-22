"""Splits, bonus issues and consolidations (plan v5).

NSE's daily prices are stored exactly as traded, so a 1-into-5 split looks like an 80% crash. These rules turn the
exchange's corporate-action text into a factor and express a price series in the units of any chosen day.

Units: a price "as of day b" is in the share units valid at the close of day b. An action with ex-date E and factor F
(5 for a 1-into-5 split, 2 for a 1:1 bonus, 0.1 for a 10-into-1 consolidation) changes the units from the open of E.
To move a price from the units of day b to a later day, divide by F for every action with b < E <= that day. Volume
moves the other way. Delivery % is unchanged.

Pure rules: no database, no network.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

SPLIT, BONUS, CONSOLIDATION = "split", "bonus", "consolidation"
UNREADABLE = "unreadable"  # the exchange text mentions a split or bonus but the numbers could not be read
UNEXPLAINED = "unexplained"  # NSE's previous close moved with no action that explains it (demerger, missed action)
MANUAL = "manual"
CROSS_CHECK_TOLERANCE = 0.02  # NSE's previous close may differ from ours by this much before it counts as a jump
HOLD_OUT_DAYS = 14  # a stock with an unclear change in share count stays out of picks this long
PRICE_COLUMNS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class Action:
    ex_date: date
    factor: float | None  # None when unreadable: never applied
    kind: str
    text: str = ""


_NUM = r"(\d+(?:\.\d+)?)"
_FROM_TO = re.compile(rf"from\s*(?:rs\.?|re\.?|inr|₹)?\s*{_NUM}\s*/?-?.*?\bto\s*(?:rs\.?|re\.?|inr|₹)?\s*{_NUM}", re.I)
_BONUS = re.compile(rf"bonus\D*?{_NUM}\s*:\s*{_NUM}", re.I)


def parse(text: str) -> list[tuple[str, float | None]]:
    """Kinds and factors in one NSE corporate-action subject. Empty if it is not a split, bonus or consolidation.

    Examples: "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share" -> split 5;
    "Bonus 1:2" (1 new share for every 2 held) -> bonus 1.5; "Consolidation of shares from Rs 1 to Rs 10" -> 0.1.
    """
    low = text.lower()
    out: list[tuple[str, float | None]] = []
    if "bonus" in low:
        m = _BONUS.search(text)
        new, held = (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)
        out.append((BONUS, (new + held) / held) if new > 0 and held > 0 else (UNREADABLE, None))
    is_split = "split" in low or "sub-division" in low or "subdivision" in low
    is_consolidation = "consolidat" in low
    if is_split or is_consolidation:
        m = _FROM_TO.search(text)
        old, new_fv = (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)
        if old > 0 and new_fv > 0 and old != new_fv:
            factor = old / new_fv
            out.append((SPLIT if factor > 1 else CONSOLIDATION, factor))
        else:
            out.append((UNREADABLE, None))
    return out


def factor_between(actions: Iterable[Action], after: date, upto: date | None = None) -> float:
    """Combined factor of the actions with `after` < ex-date <= `upto` (no upper limit when `upto` is None)."""
    f = 1.0
    for a in actions:
        if a.factor and a.ex_date > after and (upto is None or a.ex_date <= upto):
            f *= a.factor
    return f


def convert(price: float, actions: Iterable[Action], from_day: date, to_day: date | None = None) -> float:
    """A price in the units of `from_day`, expressed in the units of `to_day` (latest when None)."""
    acts = list(actions)
    if to_day is None or to_day >= from_day:
        return price / factor_between(acts, from_day, to_day)
    return price * factor_between(acts, to_day, from_day)


def to_latest(df: pd.DataFrame, basis: pd.Series, actions: list[Action]) -> pd.DataFrame:
    """Express every row in the latest units. `basis` holds each row's own basis day (its date for NSE rows, the
    download day for Yahoo rows, which Yahoo has already adjusted up to that day)."""
    applied = [a for a in actions if a.factor]
    if not applied or df.empty:
        return df
    factors = pd.Series([factor_between(applied, b) for b in basis], index=df.index, dtype=float)
    if (factors == 1.0).all():
        return df
    out = df.copy()
    for col in PRICE_COLUMNS:
        if col in out:
            out[col] = out[col] / factors
    if "volume" in out:
        out["volume"] = out["volume"] * factors
    return out


def rebase(df: pd.DataFrame | None, actions: list[Action], day: date) -> pd.DataFrame | None:
    """A series in the latest units, expressed in the units of `day` (to compare with a price stored on `day`)."""
    if df is None or df.empty:
        return df
    f = factor_between(actions, day)
    if f == 1.0:
        return df
    out = df.copy()
    for col in PRICE_COLUMNS:
        if col in out:
            out[col] = out[col] * f
    if "volume" in out:
        out["volume"] = out["volume"] / f
    return out


def unexplained_ratio(our_prev_close: float, nse_prev_close: float) -> float | None:
    """NSE adjusts its own previous close on an ex-date. If ours (already adjusted for known actions) differs by
    more than the tolerance, the share count changed in a way we do not know about; the ratio is the factor."""
    if our_prev_close <= 0 or nse_prev_close <= 0:
        return None
    ratio = our_prev_close / nse_prev_close
    return ratio if abs(ratio - 1) > CROSS_CHECK_TOLERANCE else None


def price_warning(actions: Iterable[Action], today: date) -> str | None:
    """A reason to keep the stock out of picks: its share count changed recently in a way the app could not read."""
    for a in sorted(actions, key=lambda x: x.ex_date, reverse=True):
        if a.kind not in (UNREADABLE, UNEXPLAINED) or not today - timedelta(days=HOLD_OUT_DAYS) <= a.ex_date <= today:
            continue
        if a.kind == UNREADABLE:
            return (f"Price history unreliable: a change in share count on {a.ex_date:%d %b %Y} could not be read "
                    f"from the exchange notice ({a.text[:80]})")
        return (f"Price history unreliable: NSE's own previous close changed on {a.ex_date:%d %b %Y} with no split or "
                "bonus on record (possibly a demerger), so recent price moves may not be real")
    return None


def describe(actions: Iterable[Action], since: date) -> list[str]:
    """Plain lines for a report or holding page: which adjustments were applied to the prices shown."""
    out = []
    for a in sorted(actions, key=lambda x: x.ex_date):
        if not a.factor or a.ex_date <= since:
            continue
        if a.kind == BONUS:
            what = f"a bonus issue ({_ratio(a.factor - 1)} new share(s) per share held)"
        elif a.kind == SPLIT:
            what = f"a share split (1 share became {_ratio(a.factor)})"
        elif a.kind == CONSOLIDATION:
            what = f"a share consolidation ({_ratio(1 / a.factor)} shares became 1)"
        else:
            what = f"a change in share count (factor {a.factor:.4g})"
        out.append(f"Prices adjusted for {what} on {a.ex_date:%d %b %Y}")
    return out


def _ratio(x: float) -> str:
    return f"{x:.0f}" if abs(x - round(x)) < 1e-9 else f"{x:.4g}"
