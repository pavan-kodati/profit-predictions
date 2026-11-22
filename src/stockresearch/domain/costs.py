"""Trading costs and tax for Indian equity delivery trades (plan E2). Rates are approximate and configurable."""

from __future__ import annotations

from dataclasses import dataclass

STT_RATE = 0.001  # securities transaction tax, each side, delivery
EXCHANGE_RATE = 0.0000297  # NSE transaction charge
SEBI_RATE = 0.000001
STAMP_RATE = 0.00015  # buy side only
GST_RATE = 0.18


@dataclass(frozen=True)
class CostModel:
    brokerage_per_order: float = 20.0
    dp_charge_per_sell: float = 15.93
    short_term_tax_rate: float = 0.20
    long_term_tax_rate: float = 0.125


def round_trip_charges(buy_value: float, sell_value: float, model: CostModel) -> float:
    brokerage = 2 * model.brokerage_per_order
    exchange = (buy_value + sell_value) * EXCHANGE_RATE
    sebi = (buy_value + sell_value) * SEBI_RATE
    gst = GST_RATE * (brokerage + exchange + sebi)
    stt = (buy_value + sell_value) * STT_RATE
    stamp = buy_value * STAMP_RATE
    return brokerage + exchange + sebi + gst + stt + stamp + model.dp_charge_per_sell


def net_return(gross_return: float, investment: float, model: CostModel, long_term: bool = False) -> float:
    """Return after charges and capital-gains tax, as a fraction of the investment."""
    buy_value = investment
    sell_value = investment * (1 + gross_return)
    profit = sell_value - buy_value - round_trip_charges(buy_value, sell_value, model)
    if profit > 0:
        profit *= 1 - (model.long_term_tax_rate if long_term else model.short_term_tax_rate)
    return profit / investment
