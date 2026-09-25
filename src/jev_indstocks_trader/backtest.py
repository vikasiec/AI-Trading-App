"""Event-driven backtest engine (A6 in docs/INTELLIGENCE_ROADMAP.md).

Deliberately reuses the SAME exit-decision logic (`exits.determine_exit_reason`)
and the SAME cost engine (`costs.compute_round_trip_cost`) that the live loop
uses -- a backtest that scores trades with different rules than production
uses isn't testing anything real. What it does NOT reuse is the live
`ExitManager`/`RiskGovernor` classes themselves, since those talk to the
broker's HTTP API; this operates purely on in-memory `Bar` data instead.

Strategy-agnostic by design: `StrategyFn` is any function that looks at the
bars seen so far (no lookahead -- only `bars[:i+1]` is visible when deciding
on bar i) and returns True to enter long, or False to hold. This module knows
nothing about Jev, momentum, or mean-reversion; those become StrategyFn
implementations once their hypothesis (H1/H4/H5/...) has been tested and
survives, per the roadmap.

Fill model: a signal decided on bar i's close fills at bar i+1's open (avoids
same-bar lookahead bias). Exits use the identical stop-loss/target/time-exit
logic as live trading, checked against each subsequent bar's close.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .costs import CostRates, compute_round_trip_cost
from .exits import determine_exit_reason
from .historical_data import Bar
from .positions import Position

StrategyFn = Callable[[list[Bar]], bool]  # bars seen so far -> enter_long?


@dataclass(frozen=True)
class BacktestTrade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: int
    exit_reason: str
    gross_pnl: float
    net_pnl: float


@dataclass(frozen=True)
class BacktestResult:
    trades: list[BacktestTrade]
    total_bars: int

    @property
    def num_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> Optional[float]:
        if not self.trades:
            return None
        wins = sum(1 for t in self.trades if t.net_pnl > 0)
        return wins / len(self.trades)

    @property
    def total_net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)

    @property
    def total_gross_pnl(self) -> float:
        return sum(t.gross_pnl for t in self.trades)

    def summary_text(self) -> str:
        if not self.trades:
            return f"{self.total_bars} bars, 0 trades -- strategy never entered."
        return (
            f"{self.total_bars} bars, {self.num_trades} trades, "
            f"win_rate={self.win_rate:.1%}, "
            f"total_gross_pnl={self.total_gross_pnl:.2f}, "
            f"total_net_pnl={self.total_net_pnl:.2f} "
            f"(cost drag: {self.total_gross_pnl - self.total_net_pnl:.2f})"
        )


def run_backtest(
    bars: list[Bar],
    strategy: StrategyFn,
    qty: int,
    stop_loss_pct: float = 0.01,
    target_pct: float = 0.02,
    max_hold_bars: int = 375,
    product: str = "INTRADAY",
    cost_rates: CostRates = CostRates(),
) -> BacktestResult:
    """Single-symbol, at-most-one-open-position backtest. max_hold_bars is
    bars, not minutes -- convert from your bar interval before calling.
    """
    trades: list[BacktestTrade] = []
    open_position: Optional[Position] = None
    entry_bar_index: Optional[int] = None

    for i, bar in enumerate(bars):
        if open_position is not None:
            elapsed_bars = i - entry_bar_index
            reason = determine_exit_reason(
                open_position, live_ltp=bar.close, now=entry_bar_index + elapsed_bars,
                max_hold_seconds=max_hold_bars,
            )
            if reason is not None:
                gross = (bar.close - open_position.entry_price) * open_position.qty
                cost = compute_round_trip_cost(
                    buy_price=open_position.entry_price, sell_price=bar.close,
                    qty=open_position.qty, product=product, rates=cost_rates,
                )
                trades.append(BacktestTrade(
                    entry_time=bars[entry_bar_index].timestamp.isoformat(),
                    exit_time=bar.timestamp.isoformat(),
                    entry_price=open_position.entry_price,
                    exit_price=bar.close,
                    qty=open_position.qty,
                    exit_reason=reason,
                    gross_pnl=gross,
                    net_pnl=gross - cost.total,
                ))
                open_position = None
                entry_bar_index = None
            continue

        if i + 1 >= len(bars):
            break  # no next-bar open to fill an entry at

        if strategy(bars[: i + 1]):
            entry_price = bars[i + 1].open
            open_position = Position(
                security_id="BACKTEST", scrip_code="BACKTEST", exchange="NSE",
                segment="EQUITY", product=product, qty=qty, entry_price=entry_price,
                stop_loss_price=entry_price * (1 - stop_loss_pct),
                target_price=entry_price * (1 + target_pct),
                opened_at=i + 1, decision_id=f"backtest_{i}",
            )
            entry_bar_index = i + 1

    if open_position is not None:
        last_bar = bars[-1]
        gross = (last_bar.close - open_position.entry_price) * open_position.qty
        cost = compute_round_trip_cost(
            buy_price=open_position.entry_price, sell_price=last_bar.close,
            qty=open_position.qty, product=product, rates=cost_rates,
        )
        trades.append(BacktestTrade(
            entry_time=bars[entry_bar_index].timestamp.isoformat(),
            exit_time=last_bar.timestamp.isoformat(),
            entry_price=open_position.entry_price,
            exit_price=last_bar.close,
            qty=open_position.qty,
            exit_reason="end_of_data",
            gross_pnl=gross,
            net_pnl=gross - cost.total,
        ))

    return BacktestResult(trades=trades, total_bars=len(bars))
