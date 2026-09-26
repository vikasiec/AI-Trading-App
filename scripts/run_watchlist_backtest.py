#!/usr/bin/env python3
"""Backtest H4/H5 on a list of stocks (one CSV per symbol).

Usage:
    python scripts/run_watchlist_backtest.py path/to/bars_dir
    python scripts/run_watchlist_backtest.py RELIANCE.csv TCS.csv INFY.csv

Each file: timestamp,open,high,low,close,volume (ISO timestamps).
Filename stem is the symbol. A hypothesis survives only if it beats
the random baseline *net of costs* on that symbol.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jev_indstocks_trader.historical_data import load_from_csv  # noqa: E402
from jev_indstocks_trader.hypothesis import evaluate_vs_baseline  # noqa: E402
from jev_indstocks_trader.strategies import mean_reversion_long, momentum_long  # noqa: E402


def _collect(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.csv")))
        elif p.is_file():
            paths.append(p)
    return paths


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run_watchlist_backtest.py <dir-or-csv> [csv...]", file=sys.stderr)
        return 2
    files = _collect(sys.argv[1:])
    if not files:
        print("no CSV files found", file=sys.stderr)
        return 2

    any_survive = False
    print(f"symbols: {len(files)}")
    for path in files:
        symbol = path.stem.upper()
        bars = load_from_csv(path)
        print(f"\n=== {symbol} ({len(bars)} bars) ===")
        if len(bars) < 30:
            print("  skip: need more bars")
            continue
        h4 = evaluate_vs_baseline(f"{symbol} H4 momentum", bars, momentum_long())
        h5 = evaluate_vs_baseline(f"{symbol} H5 mean-reversion", bars, mean_reversion_long())
        print(h4.summary_text())
        print(h5.summary_text())
        any_survive = any_survive or h4.survived or h5.survived
    return 0 if any_survive else 1


if __name__ == "__main__":
    raise SystemExit(main())
