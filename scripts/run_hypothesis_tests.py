#!/usr/bin/env python3
"""Run H4 (momentum) and H5 (mean reversion) against a CSV of OHLCV bars.

Usage:
    python scripts/run_hypothesis_tests.py path/to/bars.csv

Drop any NSE/broker daily or intraday export with columns
timestamp,open,high,low,close,volume (ISO timestamps). The verdict is
KILLED unless the strategy beats a cadence baseline net of costs.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jev_indstocks_trader.historical_data import load_from_csv  # noqa: E402
from jev_indstocks_trader.hypothesis import evaluate_vs_baseline  # noqa: E402
from jev_indstocks_trader.strategies import mean_reversion_long, momentum_long  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run_hypothesis_tests.py path/to/bars.csv", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    bars = load_from_csv(path)
    print(f"loaded {len(bars)} bars from {path}")
    h4 = evaluate_vs_baseline("H4 momentum", bars, momentum_long())
    h5 = evaluate_vs_baseline("H5 mean-reversion", bars, mean_reversion_long())
    print(h4.summary_text())
    print(h5.summary_text())
    return 0 if (h4.survived or h5.survived) else 1


if __name__ == "__main__":
    raise SystemExit(main())
