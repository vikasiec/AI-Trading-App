"""Statutory cost engine for NSE equity trades.

Every rupee this bot makes has to clear STT, exchange transaction
charges, SEBI turnover fees, stamp duty, brokerage, and GST on top of
that -- before it's real edge. This module computes the actual cost of
a round trip (one entry + one exit) so `exits.py` can log net P&L
alongside gross, and so a future backtester can reject strategies whose
edge doesn't clear costs.

Rates below are current published NSE/SEBI/government rates for
CASH EQUITY as of Sept 2026, corroborated across several brokers'
published charge sheets (motilaloswal, rupeezy, tiqs, indiainfoline).
They're not INDstocks-specific except brokerage, which is INDstocks'
confirmed flat ₹5/order. Rates do change (union budgets, SEBI circulars)
-- treat these as defaults, not law, and override via env/config if
they drift.

All rates are configurable via RiskConfig / env vars so a change in
government rates doesn't require a code change.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostRates:
    """All rates as fractions (0.00025 = 0.025%), except brokerage_per_order (flat rupees)."""
    brokerage_per_order: float = 5.0          # INDstocks flat rate, confirmed
    stt_intraday_sell_pct: float = 0.00025      # 0.025% on sell side only, intraday equity
    stt_delivery_pct: float = 0.001              # 0.1% on both buy and sell, delivery equity
    exchange_txn_pct: float = 0.0000297           # NSE, both sides
    sebi_turnover_pct: float = 0.000001            # ₹10/crore, both sides
    stamp_duty_intraday_buy_pct: float = 0.00003    # 0.003% on buy side only, intraday
    stamp_duty_delivery_buy_pct: float = 0.00015     # 0.015% on buy side only, delivery
    gst_pct: float = 0.18                              # on (brokerage + exchange txn + SEBI charges)


@dataclass(frozen=True)
class RoundTripCost:
    """Full cost breakdown for one entry + one exit."""
    brokerage: float
    stt: float
    exchange_txn: float
    sebi_turnover: float
    stamp_duty: float
    gst: float
    total: float


def compute_round_trip_cost(
    buy_price: float,
    sell_price: float,
    qty: int,
    product: str = "INTRADAY",
    rates: CostRates = CostRates(),
) -> RoundTripCost:
    """Cost of one BUY + one SELL of `qty` shares. `product` is "INTRADAY"
    or "CNC" (delivery) -- STT and stamp duty differ between the two.
    """
    buy_value = buy_price * qty
    sell_value = sell_price * qty
    turnover = buy_value + sell_value

    brokerage = rates.brokerage_per_order * 2  # one order to enter, one to exit

    if product == "CNC":
        stt = (buy_value + sell_value) * rates.stt_delivery_pct
        stamp_duty = buy_value * rates.stamp_duty_delivery_buy_pct
    else:
        stt = sell_value * rates.stt_intraday_sell_pct
        stamp_duty = buy_value * rates.stamp_duty_intraday_buy_pct

    exchange_txn = turnover * rates.exchange_txn_pct
    sebi_turnover = turnover * rates.sebi_turnover_pct
    gst = (brokerage + exchange_txn + sebi_turnover) * rates.gst_pct

    total = brokerage + stt + exchange_txn + sebi_turnover + stamp_duty + gst

    return RoundTripCost(
        brokerage=brokerage, stt=stt, exchange_txn=exchange_txn,
        sebi_turnover=sebi_turnover, stamp_duty=stamp_duty, gst=gst, total=total,
    )


def net_pnl(buy_price: float, sell_price: float, qty: int, product: str = "INTRADAY",
            rates: CostRates = CostRates()) -> float:
    """Gross P&L minus the full round-trip cost."""
    gross = (sell_price - buy_price) * qty
    cost = compute_round_trip_cost(buy_price, sell_price, qty, product, rates)
    return gross - cost.total
