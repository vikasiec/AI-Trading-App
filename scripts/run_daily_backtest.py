#!/usr/bin/env python3
"""Run a daily swing strategy through the backtest engine with train/test split.

Usage:
    python scripts/run_daily_backtest.py

Reads daily bar CSVs from data/daily/ and the Nifty index from data/index/.
Pools all trades across symbols, splits by date, and prints the out-of-sample
report.

NOTE: data comes from Yahoo Finance's unofficial chart endpoint, used offline
only. Symbols are current Nifty 50 constituents — results carry survivorship
bias (names that are in the index today may not have been in the index during
the training period).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("cp"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jev_indstocks_trader.backtest import run_backtest  # noqa: E402
from jev_indstocks_trader.costs import CostRates  # noqa: E402
from jev_indstocks_trader.historical_data import Bar, load_from_csv  # noqa: E402
from jev_indstocks_trader.hypothesis import (  # noqa: E402
    evaluate_out_of_sample,
    format_baseline_comparison,
    holdout_net,
)
from jev_indstocks_trader.strategies import daily_breakout_long, random_entry  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "daily"
INDEX_DIR = Path(__file__).resolve().parent.parent / "data" / "index"

STOP_LOSS_PCT = 0.04
TARGET_PCT = 0.08
MAX_HOLD_DAYS = 5
POSITION_CAPITAL = 50_000
TEST_CUTOFF = "2025-09-28"


def load_index_map() -> dict[date, Bar]:
    idx_path = INDEX_DIR / "NIFTY50.csv"
    if not idx_path.exists():
        print("WARNING: No Nifty index data found, running without index filter")
        return {}
    bars = load_from_csv(idx_path)
    return {b.timestamp.date(): b for b in bars}


def main() -> int:
    csv_files = sorted(DATA_DIR.glob("*.csv"))
    if not csv_files:
        print("No CSV files found in data/daily/ — run scripts/fetch_nse_daily.py first", file=sys.stderr)
        return 2

    index_map = load_index_map()
    strategy = daily_breakout_long(
        lookback=20, volume_multiple=1.0,
        index_bars=index_map if index_map else None,
        index_rising_days=5,
    )

    all_trades = []
    baseline_trades = []
    total_bars = 0
    backtest_kwargs = dict(
        stop_loss_pct=STOP_LOSS_PCT, target_pct=TARGET_PCT,
        max_hold_bars=MAX_HOLD_DAYS,
        product="CNC", cost_rates=CostRates(),
        intrabar_exits=True,
        position_capital=POSITION_CAPITAL,
    )

    print(f"=== Daily Breakout Backtest ===")
    print(f"Rule: 20-day high + Nifty rising over 5 days")
    print(f"Exit: {STOP_LOSS_PCT:.0%} stop, {TARGET_PCT:.0%} target, {MAX_HOLD_DAYS}-day max hold")
    print(f"Product: CNC (delivery), costs at live rates")
    print(f"Test cutoff: {TEST_CUTOFF}")
    print(f"Symbols: {len(csv_files)}")
    print(f"*** Survivorship bias: current Nifty names only ***")
    print()

    for csv_path in csv_files:
        symbol = csv_path.stem
        bars = load_from_csv(csv_path)
        total_bars += len(bars)

        result = run_backtest(bars, strategy, **backtest_kwargs)
        baseline = run_backtest(bars, random_entry(every_n=15), **backtest_kwargs)

        if result.num_trades > 0:
            print(f"  {symbol:15s} {result.num_trades:3d} trades, net ₹{result.total_net_pnl:>10.2f}")
        all_trades.extend(result.trades)
        baseline_trades.extend(baseline.trades)

    print(f"\n{'='*60}")
    print(f"Pooled: {len(all_trades)} trades across {len(csv_files)} symbols")
    print(f"{'='*60}\n")

    report = evaluate_out_of_sample(
        name="H6_daily_breakout_20d_nifty_rising",
        all_trades=all_trades,
        total_bars=total_bars,
        cutoff_iso=TEST_CUTOFF,
    )
    print(report.summary_text())
    print(format_baseline_comparison(
        holdout_net(all_trades, TEST_CUTOFF),
        holdout_net(baseline_trades, TEST_CUTOFF),
    ))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
