"""H4 / H5 evaluation: strategy vs random-entry baseline, same bars, same costs.

A hypothesis *survives* only if net P&L beats the baseline. Gross-only
wins do not count — that was the whole point of wiring costs into A6.
"""
from __future__ import annotations

from dataclasses import dataclass

from .backtest import BacktestResult, StrategyFn, run_backtest
from .costs import CostRates
from .historical_data import Bar
from .strategies import random_entry


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
