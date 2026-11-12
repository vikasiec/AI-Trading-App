#!/usr/bin/env python3
"""Replay a planned intraday long before placing it (offline analysis, no orders).

Usage:
    python scripts/run_timemachine.py RELIANCE --entry 1412.50 --stop 1405 --target 1427.50 \\
        --entry-time 09:50 --qty 35 --fetch
    python scripts/run_timemachine.py RELIANCE --entry 1412.50 --stop 1405 --target 1427.50 \\
        --entry-time 09:50 --csv data/minute/RELIANCE_5m.csv --until 15:15 --json

Bars: 5-minute OHLCV for the symbol, columns timestamp,open,high,low,close,volume
(what historical_data.load_from_csv reads). Default file: data/minute/<SYMBOL>_5m.csv.
--fetch downloads the last 60 days of 5-minute bars from Yahoo Finance's
unofficial chart endpoint first and saves them there. Yahoo keeps about 60 days
of 5-minute bars, can lag, and is not an exchange feed.

Exit codes: 0 the report printed; 1 runtime error (no file, network, too few
sessions); 2 usage error (bad flags or a plan that cannot be a long).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote as urlquote

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("cp"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jev_indstocks_trader.historical_data import Bar, load_from_csv, save_to_csv  # noqa: E402
from jev_indstocks_trader.session import IST, now_ist  # noqa: E402
from jev_indstocks_trader.timemachine import (  # noqa: E402
    DEFAULT_UNTIL,
    MAX_REPLAYS,
    MAX_WINDOW,
    TimeMachineError,
    TradePlan,
    run_time_machine,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "minute"
YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/"
FETCH_TIMEOUT_S = 30


class FetchError(RuntimeError):
    """Yahoo did not give usable 5-minute bars. The message is safe to show."""


def default_csv(symbol: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / f"{symbol.upper()}_5m.csv"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def fetch_yahoo_5m(symbol: str, suffix: str = ".NS") -> list[Bar]:
    """60 days of 5-minute bars, timestamps in IST. Rows with a missing price are skipped."""
    encoded = urlquote(f"{symbol.upper()}{suffix}", safe="")
    try:
        resp = requests.get(
            f"{YAHOO_CHART}{encoded}",
            params={"range": "60d", "interval": "5m"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=FETCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        raise FetchError(f"Yahoo did not answer for {symbol}: {exc}") from exc
    except ValueError as exc:
        raise FetchError(f"Yahoo returned something that is not JSON for {symbol}") from exc

    chart = body.get("chart") if isinstance(body, dict) else None
    results = chart.get("result") if isinstance(chart, dict) else None
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise FetchError(f"Yahoo has no 5-minute data for {symbol}")
    result = results[0]
    stamps = result.get("timestamp")
    indicators = result.get("indicators")
    quotes = indicators.get("quote") if isinstance(indicators, dict) else None
    if not isinstance(stamps, list) or not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        raise FetchError(f"Yahoo's answer for {symbol} is not the shape this script knows")
    columns: dict[str, list[object]] = {}
    for key in ("open", "high", "low", "close", "volume"):
        column = quotes[0].get(key)
        columns[key] = column if isinstance(column, list) else []

    bars: list[Bar] = []
    for i, stamp in enumerate(stamps):
        ts = _number(stamp)
        row = {key: _number(col[i]) if i < len(col) else None for key, col in columns.items()}
        o, h, lo, c = row["open"], row["high"], row["low"], row["close"]
        if ts is None or o is None or h is None or lo is None or c is None:
            continue
        bars.append(Bar(
            timestamp=datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST),
            open=round(o, 2), high=round(h, 2), low=round(lo, 2), close=round(c, 2),
            volume=int(row["volume"] or 0),
        ))
    if not bars:
        raise FetchError(f"Yahoo returned no usable 5-minute bars for {symbol}")
    bars.sort(key=lambda b: b.timestamp)
    return bars


def _hhmm(text: str) -> time:
    try:
        return datetime.strptime(text.strip(), "%H:%M").time()
    except ValueError:
        raise argparse.ArgumentTypeError(f"wants an IST time like 09:50, got {text!r}")


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"wants a date like 2026-11-12, got {text!r}")


def _int_between(low: int, high: int) -> Callable[[str], int]:
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"wants a whole number, got {text!r}")
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be between {low:,} and {high:,}, got {value:,}")
        return value
    return parse


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Time Machine: replay a planned intraday long. Places nothing.")
    ap.add_argument("symbol", help="NSE symbol, e.g. RELIANCE")
    ap.add_argument("--entry", type=float, required=True, help="planned entry price")
    ap.add_argument("--stop", type=float, required=True, help="stop-loss price, below the entry")
    ap.add_argument("--target", type=float, required=True, help="target price, above the entry")
    ap.add_argument("--entry-time", type=_hhmm, required=True, help="IST close of the entry bar, e.g. 09:50")
    ap.add_argument("--date", type=_day, help="the plan's day (default: today in IST); only earlier sessions are used")
    ap.add_argument("--qty", type=int, default=0, help="shares, to see rupees net of costs (default: R only)")
    ap.add_argument("--until", type=_hhmm, default=DEFAULT_UNTIL,
                    help="end each replay at this IST time (default 15:15, the loop's flatten time)")
    ap.add_argument("--replays", type=_int_between(1, MAX_REPLAYS), default=10_000)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--window", type=_int_between(0, MAX_WINDOW), default=2,
                    help="draw from the same time of day +- this many bars")
    ap.add_argument("--csv", type=Path, help="5-minute bars CSV (default data/minute/<SYMBOL>_5m.csv)")
    ap.add_argument("--fetch", action="store_true", help="download 60 days of 5-minute bars from Yahoo first")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:          # argparse: usage errors exit 2, --help exits 0
        return exc.code if isinstance(exc.code, int) else 2

    symbol = args.symbol.strip().upper()
    try:
        plan = TradePlan(
            symbol=symbol, day=args.date or now_ist().date(), entry=args.entry, stop=args.stop,
            target=args.target, entry_time=args.entry_time, qty=args.qty,
        )
    except TimeMachineError as exc:
        print(f"run_timemachine: {exc}", file=sys.stderr)
        return 2

    path: Path = args.csv or default_csv(symbol)
    try:
        if args.fetch:
            bars = fetch_yahoo_5m(symbol)
            save_to_csv(bars, path)
            if not args.json:
                print(f"Fetched {len(bars):,} five-minute bars for {symbol} -> {path}")
        elif not path.exists():
            print(f"run_timemachine: no bars at {path}: pass --fetch to download them, or --csv PATH",
                  file=sys.stderr)
            return 1
        else:
            bars = load_from_csv(path)
        report = run_time_machine(
            bars, plan, replays=args.replays, seed=args.seed, window=args.window, until=args.until,
        )
    except (TimeMachineError, FetchError) as exc:
        print(f"run_timemachine: {exc}", file=sys.stderr)
        return 1
    except (OSError, KeyError, ValueError) as exc:
        print(f"run_timemachine: could not read {path}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary_text())
        print("Offline analysis only: nothing was placed and no risk setting was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
