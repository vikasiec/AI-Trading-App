"""H4 / H5 / H6 evaluation: strategy vs random-entry baseline, same bars, same costs.

Two evaluation modes:

1. evaluate_vs_baseline() — original. Runs strategy and baseline on the
   full bar series. A hypothesis *survives* only if net P&L beats the
   baseline. Used for intraday rules.

2. evaluate_out_of_sample() — new. Runs strategy on the full bar series,
   then splits trades by date into train and test sets. The test set is
   the only thing the live gate reads. Used for daily swing rules.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .backtest import BacktestResult, BacktestTrade, StrategyFn, run_backtest
from .costs import CostRates
from .historical_data import Bar
from .strategies import random_entry

MIN_TEST_TRADES = 30
MIN_AVG_NET_PER_TRADE = 0.0


@dataclass(frozen=True)
class HypothesisVerdict:
    name: str
    strategy_result: BacktestResult
    baseline_result: BacktestResult
    survived: bool
    reason: str

    def summary_text(self) -> str:
        flag = "SURVIVED" if self.survived else "KILLED"
        return (
            f"[{flag}] {self.name}: {self.reason}\n"
            f"  strategy: {self.strategy_result.summary_text()}\n"
            f"  baseline: {self.baseline_result.summary_text()}"
        )


@dataclass(frozen=True)
class SplitReport:
    """Train/test split evaluation of a daily strategy."""
    name: str
    train: BacktestResult
    test: BacktestResult
    passed: bool
    reason: str

    def summary_text(self) -> str:
        flag = "PASS" if self.passed else "FAIL"
        lines = [f"[{flag}] {self.name}: {self.reason}"]
        for label, r in [("  train", self.train), ("  test ", self.test)]:
            if r.num_trades == 0:
                lines.append(f"{label}: 0 trades")
                continue
            wr = r.win_rate
            ci_lo, ci_hi = _binomial_ci(r.num_trades, wr)
            avg_w = f"₹{r.avg_winner:.2f}" if r.avg_winner is not None else "n/a"
            avg_l = f"₹{r.avg_loser:.2f}" if r.avg_loser is not None else "n/a"
            avg_cost = (r.total_gross_pnl - r.total_net_pnl) / r.num_trades
            lines.append(
                f"{label}: {r.num_trades} trades, "
                f"win_rate={wr:.0%} [{ci_lo:.0%}-{ci_hi:.0%}], "
                f"avg_winner={avg_w}, "
                f"avg_loser={avg_l}, "
                f"avg_net/trade=₹{r.avg_net_per_trade:.2f}, "
                f"avg_cost/trade=₹{avg_cost:.2f}, "
                f"total_net=₹{r.total_net_pnl:.2f}"
            )
        return "\n".join(lines)


def _binomial_ci(n: int, p: float, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for win rate."""
    if n == 0:
        return 0.0, 1.0
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return max(0.0, centre - spread), min(1.0, centre + spread)


def evaluate_vs_baseline(
    name: str,
    bars: list[Bar],
    strategy: StrategyFn,
    qty: int = 10,
    stop_loss_pct: float = 0.01,
    target_pct: float = 0.02,
    max_hold_bars: int = 20,
    cost_rates: CostRates = CostRates(),
    baseline_every_n: int = 15,
) -> HypothesisVerdict:
    strat = run_backtest(
        bars, strategy, qty=qty, stop_loss_pct=stop_loss_pct,
        target_pct=target_pct, max_hold_bars=max_hold_bars, cost_rates=cost_rates,
    )
    base = run_backtest(
        bars, random_entry(every_n=baseline_every_n), qty=qty,
        stop_loss_pct=stop_loss_pct, target_pct=target_pct,
        max_hold_bars=max_hold_bars, cost_rates=cost_rates,
    )
    if strat.num_trades == 0:
        return HypothesisVerdict(
            name, strat, base, False,
            "strategy never entered — no evidence either way, treated as fail-closed",
        )
    survived = strat.total_net_pnl > base.total_net_pnl
    delta = strat.total_net_pnl - base.total_net_pnl
    reason = (
        f"net {strat.total_net_pnl:.2f} vs baseline {base.total_net_pnl:.2f} "
        f"(delta {delta:+.2f})"
    )
    return HypothesisVerdict(name, strat, base, survived, reason)


def _split_trades(
    trades: list[BacktestTrade], cutoff_iso: str,
) -> tuple[list[BacktestTrade], list[BacktestTrade]]:
    """Split trades by entry_time into train (before cutoff) and test (on or after)."""
    train, test = [], []
    for t in trades:
        if t.exit_reason == "end_of_data":
            continue
        if t.entry_time < cutoff_iso:
            train.append(t)
        else:
            test.append(t)
    return train, test


def evaluate_out_of_sample(
    name: str,
    all_trades: list[BacktestTrade],
    total_bars: int,
    cutoff_iso: str = "",
    min_test_trades: int = MIN_TEST_TRADES,
) -> SplitReport:
    """Evaluate a pool of backtest trades with a train/test date split.

    all_trades should come from running the strategy on the full bar
    series across multiple symbols, then pooling. cutoff_iso is the
    ISO date string for the start of the test period.

    Pass criteria (test set only):
      1. At least min_test_trades trades
      2. Average net P&L per trade > 0 (positive expectancy after costs)
    """
    train_trades, test_trades = _split_trades(all_trades, cutoff_iso)

    train_result = BacktestResult(trades=train_trades, total_bars=total_bars)
    test_result = BacktestResult(trades=test_trades, total_bars=total_bars)

    if test_result.num_trades < min_test_trades:
        return SplitReport(
            name=name, train=train_result, test=test_result,
            passed=False,
            reason=(
                f"not enough evidence: {test_result.num_trades} test trades "
                f"(need {min_test_trades})"
            ),
        )

    avg_net = test_result.avg_net_per_trade
    passed = avg_net is not None and avg_net > MIN_AVG_NET_PER_TRADE

    if passed:
        reason = (
            f"test avg ₹{avg_net:.2f}/trade on {test_result.num_trades} trades "
            f"(win rate {test_result.win_rate:.0%})"
        )
    else:
        reason = (
            f"negative expectancy: test avg ₹{avg_net:.2f}/trade "
            f"on {test_result.num_trades} trades"
        )

    return SplitReport(
        name=name, train=train_result, test=test_result,
        passed=passed, reason=reason,
    )


def holdout_net(trades: list[BacktestTrade], cutoff_iso: str) -> float:
    """Net P&L of trades that entered on or after cutoff. end_of_data rows are excluded."""
    _, test = _split_trades(trades, cutoff_iso)
    return sum(t.net_pnl for t in test)


def format_baseline_comparison(strategy_test_net: float, baseline_test_net: float) -> str:
    """Printed comparison only. Does not decide pass or fail."""
    delta = strategy_test_net - baseline_test_net
    return (
        f"  random-entry test net: ₹{baseline_test_net:.2f}\n"
        f"  strategy minus random-entry: ₹{delta:+.2f}\n"
        "  comparison only — beating random does not pass a negative average"
    )
