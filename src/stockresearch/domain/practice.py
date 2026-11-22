"""Practice portfolio rules (plan v2, section 2). Pure functions: no database, no network.

Rules version 2 (plan v3):
- Every pick gets the same fixed amount, bought as whole shares at the first opening price after publication.
  If one share costs more than the amount, one share is bought.
- Skip a pick only if it opens at or below its safety level.
- Sell at the safety (stop) level, or at the close of the last day of the holding window, whichever comes first.
  If a later day opens below the safety level, sell at that opening price (what would really happen).
- Every trade pays brokerage, taxes and charges; profits pay short-term capital-gains tax.
- A stock is a new pick once: it cannot be picked again while its trade is open, and afterwards only when a new
  confirmed event has happened since it was last picked.

Rules version 1 (until 13 Nov 2026) split one daily amount equally between that day's picks and skipped a pick
when one share cost more than its part. Trades made under it keep `rules_version = 1` and are never edited.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from stockresearch.domain.costs import CostModel, round_trip_charges
from stockresearch.domain.models import PickType

RULES_VERSION = 2
HOLD_DAYS = {PickType.HYPE: 5, PickType.MIXED: 10, PickType.FUNDAMENTALS: 15}
FD_RATE = 0.07  # fixed-deposit comparison, simple interest per year
MIN_TRADES_FOR_VERDICT = 20
MIN_DAYS_FOR_VERDICT = 28

EXIT_STOP = "stop"
EXIT_GAP = "gap"
EXIT_WINDOW = "window"
EXIT_MANUAL = "manual"  # only in the user's own practice portfolio: sold with the "Sell now" button


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Entry:
    quantity: int
    price: float
    skip_reason: str | None = None


@dataclass(frozen=True)
class Exit:
    day: date
    price: float
    reason: str


@dataclass(frozen=True)
class Result:
    buy_value: float
    sell_value: float
    charges: float
    tax: float
    net: float  # after charges and tax

    @property
    def net_pct(self) -> float:
        return self.net / self.buy_value if self.buy_value else 0.0


MARKET_OPEN = time(9, 15)


def earliest_entry_day(pick_date: date, published_at: datetime) -> date:
    """The first trading day whose opening price a reader of the pick could actually get.

    Picks are normally published at 08:00, before the 09:15 open, so they can be bought the same morning. A pick
    published after the open can only be bought from the next day: using that morning's price would be hindsight.
    """
    if published_at.date() < pick_date or (published_at.date() == pick_date and published_at.time() < MARKET_OPEN):
        return pick_date
    return max(pick_date, published_at.date()) + timedelta(days=1)


def plan_entry(open_price: float, stop: float, amount: float) -> Entry:
    """Shares to buy at the open, or the reason the pick is skipped."""
    if open_price <= 0:
        return Entry(0, open_price, "No opening price")
    if open_price <= stop:
        return Entry(0, open_price, "it opened at or below its safety level, so the reason to buy had already failed")
    return Entry(max(int(amount // open_price), 1), open_price)


def repeat_block(last_pick: date | None, trade_status: str | None, newest_event: date | None) -> str | None:
    """Why a stock cannot be a new pick today, or None if it can.

    `trade_status` is the status of the pretend trade made for the last pick (None if it has not been bought yet).
    `newest_event` is the date of the newest confirmed positive event for the stock.
    """
    if last_pick is None:
        return None
    if trade_status in (None, "open"):
        return f"Already picked on {last_pick:%d %b} and still running, so it is not repeated as a new pick."
    if newest_event is None or newest_event <= last_pick:
        return f"Picked before on {last_pick:%d %b}. Nothing new has been confirmed since, so it is not picked again."
    return None


def manual_exit(bars: Sequence[Bar], from_day: date) -> Exit | None:
    """Sell at the first opening price on or after `from_day` (the user's "Sell now")."""
    for bar in bars:
        if bar.day >= from_day:
            return Exit(bar.day, bar.open, EXIT_MANUAL)
    return None


def find_exit(bars: Sequence[Bar], stop: float, hold_days: int) -> Exit | None:
    """bars start with the entry day. Returns None while the trade is still open."""
    for i, bar in enumerate(bars):
        if i > 0 and bar.open <= stop:
            return Exit(bar.day, bar.open, EXIT_GAP)
        if bar.low <= stop:
            return Exit(bar.day, stop, EXIT_STOP)
        if i + 1 >= hold_days:
            return Exit(bar.day, bar.close, EXIT_WINDOW)
    return None


def settle(quantity: int, entry_price: float, exit_price: float, model: CostModel) -> Result:
    buy, sell = quantity * entry_price, quantity * exit_price
    charges = round_trip_charges(buy, sell, model)
    profit = sell - buy - charges
    tax = profit * model.short_term_tax_rate if profit > 0 else 0.0
    return Result(round(buy, 2), round(sell, 2), round(charges, 2), round(tax, 2), round(profit - tax, 2))


def fd_interest(amount: float, start: date, end: date) -> float:
    return amount * FD_RATE * max((end - start).days, 0) / 365


@dataclass(frozen=True)
class Verdict:
    key: str  # early | behind | ahead
    title: str
    detail: str


def verdict(closed_trades: int, days_running: int, portfolio_pct: float, index_pct: float) -> Verdict:
    if closed_trades < MIN_TRADES_FOR_VERDICT or days_running < MIN_DAYS_FOR_VERDICT:
        need = max(MIN_TRADES_FOR_VERDICT - closed_trades, 0)
        return Verdict("early", "Too early to tell",
                       f"{closed_trades} of {MIN_TRADES_FOR_VERDICT} finished trades so far"
                       + (f", {need} more needed" if need else "")
                       + ". With only a few trades, luck matters more than skill.")
    if portfolio_pct > index_pct:
        return Verdict("ahead", "Beating the market so far",
                       f"Ahead of simply buying the Nifty 50 by {(portfolio_pct - index_pct) * 100:.1f} points, "
                       f"over {closed_trades} finished trades.")
    return Verdict("behind", "Not beating the market",
                   f"Behind simply buying the Nifty 50 by {(index_pct - portfolio_pct) * 100:.1f} points, "
                   f"over {closed_trades} finished trades. Don't invest real money on these picks yet.")
