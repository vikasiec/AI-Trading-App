"""Event-driven backtest engine (A6 in docs/INTELLIGENCE_ROADMAP.md).

Deliberately reuses the SAME cost engine (`costs.compute_round_trip_cost`)
that the live loop uses -- a backtest that scores trades with different
rules than production uses isn't testing anything real.

Strategy-agnostic by design: `StrategyFn` is any function that looks at the
bars seen so far (no lookahead -- only `bars[:i+1]` is visible when deciding
on bar i) and returns True to enter long, or False to hold.

Fill model: a signal decided on bar i's close fills at bar i+1's open (avoids
same-bar lookahead bias).

Exit model (two modes):
  - close-only (default, for minute bars): checks stop/target against the
    bar's close price, same as the live loop's per-tick LTP check.
  - intrabar (for daily bars): checks the bar's low against the stop and
    the bar's high against the target. On a day where both are hit, the
    stop is assumed to have hit first. This avoids the false survival bias
    where a daily bar's close recovers but the intraday low would have
    stopped the live loop out.
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

    @property
    def avg_winner(self) -> Optional[float]:
        winners = [t.net_pnl for t in self.trades if t.net_pnl > 0]
        return sum(winners) / len(winners) if winners else None

    @property
    def avg_loser(self) -> Optional[float]:
        losers = [t.net_pnl for t in self.trades if t.net_pnl <= 0]
        return sum(losers) / len(losers) if losers else None

    @property
    def avg_net_per_trade(self) -> Optional[float]:
        if not self.trades:
            return None
        return self.total_net_pnl / len(self.trades)

    def summary_text(self) -> str:
        if not self.trades:
            return f"{self.total_bars} bars, 0 trades -- strategy never entered."
        avg_w = self.avg_winner
        avg_l = self.avg_loser
        w_str = f"{avg_w:.2f}" if avg_w is not None else "n/a"
        l_str = f"{avg_l:.2f}" if avg_l is not None else "n/a"
        return (
            f"{self.total_bars} bars, {self.num_trades} trades, "
            f"win_rate={self.win_rate:.1%}, "
            f"avg_winner={w_str}, avg_loser={l_str}, "
            f"avg_net/trade={self.avg_net_per_trade:.2f}, "
            f"total_net_pnl={self.total_net_pnl:.2f} "
            f"(cost drag: {self.total_gross_pnl - self.total_net_pnl:.2f})"
        )


def _check_intrabar_exit(
    position: Position, bar: Bar, max_hold_bars: int, elapsed_bars: int,
) -> tuple[Optional[str], float]:
    """Check stop/target against the bar's high and low, not just close.
    Returns (reason, fill_price) or (None, 0.0).
    """
    stop_hit = bar.low <= position.stop_loss_price
    target_hit = bar.high >= position.target_price
    time_up = elapsed_bars >= max_hold_bars

    if stop_hit and target_hit:
        return "stop_loss", min(position.stop_loss_price, bar.open)
    if stop_hit:
        return "stop_loss", min(position.stop_loss_price, bar.open)
    if target_hit:
        return "target", max(position.target_price, bar.open)
    if time_up:
        return "time_exit", bar.close
    return None, 0.0


def run_backtest(
    bars: list[Bar],
    strategy: StrategyFn,
    qty: int = 0,
    stop_loss_pct: float = 0.01,
    target_pct: float = 0.02,
    max_hold_bars: int = 375,
    product: str = "INTRADAY",
    cost_rates: CostRates = CostRates(),
    intrabar_exits: bool = False,
    position_capital: float = 0.0,
) -> BacktestResult:
    """Single-symbol, at-most-one-open-position backtest.

    Sizing: if position_capital > 0, each trade is sized as
    floor(position_capital / entry_price) — so the rupee exposure is
    constant across trades regardless of the stock's price history.
    Otherwise the fixed `qty` is used for every trade.

    Set intrabar_exits=True for daily bars so stops/targets check the
    bar's high/low instead of just close. max_hold_bars is bars, not
    minutes -- convert from your bar interval before calling.
    """
    trades: list[BacktestTrade] = []
    open_position: Optional[Position] = None
    entry_bar_index: Optional[int] = None

    for i, bar in enumerate(bars):
        if open_position is not None:
            elapsed_bars = i - entry_bar_index

            if intrabar_exits:
                reason, fill_price = _check_intrabar_exit(
                    open_position, bar, max_hold_bars, elapsed_bars,
                )
            else:
                reason = determine_exit_reason(
                    open_position, live_ltp=bar.close,
                    now=entry_bar_index + elapsed_bars,
                    max_hold_seconds=max_hold_bars,
                )
                fill_price = bar.close

            if reason is not None:
                gross = (fill_price - open_position.entry_price) * open_position.qty
                cost = compute_round_trip_cost(
                    buy_price=open_position.entry_price, sell_price=fill_price,
                    qty=open_position.qty, product=product, rates=cost_rates,
                )
                trades.append(BacktestTrade(
                    entry_time=bars[entry_bar_index].timestamp.isoformat(),
                    exit_time=bar.timestamp.isoformat(),
                    entry_price=open_position.entry_price,
                    exit_price=fill_price,
                    qty=open_position.qty,
                    exit_reason=reason,
                    gross_pnl=gross,
                    net_pnl=gross - cost.total,
                ))
                open_position = None
                entry_bar_index = None
            continue

        if i + 1 >= len(bars):
            break

        if strategy(bars[: i + 1]):
            entry_price = bars[i + 1].open
            trade_qty = int(position_capital // entry_price) if position_capital > 0 else qty
            if trade_qty < 1:
                continue
            open_position = Position(
                security_id="BACKTEST", scrip_code="BACKTEST", exchange="NSE",
                segment="EQUITY", product=product, qty=trade_qty, entry_price=entry_price,
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
