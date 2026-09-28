#!/usr/bin/env python3
"""Fetch 5 years of daily OHLCV bars for liquid Nifty 50 names from Yahoo Finance.

Saves one CSV per symbol in data/daily/ in the format load_from_csv() expects:
    timestamp,open,high,low,close,volume

Usage:
    python scripts/fetch_nse_daily.py              # all default symbols
    python scripts/fetch_nse_daily.py RELIANCE TCS  # specific symbols
"""
from __future__ import annotations

import csv
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote as urlquote

import requests

MIN_BARS = 600

LIQUID_NIFTY_50 = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "AXISBANK", "BAJFINANCE", "MARUTI", "TITAN",
    "SUNPHARMA", "TATAMOTORS", "NTPC", "POWERGRID", "M&M",
    "ULTRACEMCO", "WIPRO", "HCLTECH", "TATASTEEL", "ADANIENT",
]

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "daily"


def fetch_yahoo(symbol: str, years: int = 5, suffix: str = ".NS") -> list[dict]:
    encoded = urlquote(f"{symbol}{suffix}", safe="")
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}"
    params = {"range": f"{years}y", "interval": "1d", "events": "history"}
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    today = date.today()
    bars = []
    for i, ts in enumerate(timestamps):
        o = quote["open"][i]
        h = quote["high"][i]
        lo = quote["low"][i]
        c = quote["close"][i]
        v = quote["volume"][i]
        if any(x is None for x in (o, h, lo, c, v)):
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if dt.date() >= today:
            continue
        bars.append({
            "timestamp": dt.strftime("%Y-%m-%dT09:15:00"),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(lo, 2),
            "close": round(c, 2),
            "volume": int(v),
        })
    bars.sort(key=lambda b: b["timestamp"])
    return bars


def save_csv(symbol: str, bars: list[dict], out_dir: Path = DATA_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{symbol}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(bars)
    return path


INDEX_DIR = Path(__file__).resolve().parent.parent / "data" / "index"

INDICES = {"NIFTY50": "^NSEI"}


def main(symbols: list[str] | None = None) -> int:
    if symbols is None:
        symbols = sys.argv[1:] if len(sys.argv) > 1 else list(LIQUID_NIFTY_50)
    print(f"Fetching {len(symbols)} symbols, 5 years daily bars each")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, 0
    for sym in symbols:
        try:
            bars = fetch_yahoo(sym)
            if len(bars) < MIN_BARS:
                print(f"  {sym:15s} SKIP: only {len(bars)} bars (need {MIN_BARS})", file=sys.stderr)
                fail += 1
                continue
            path = save_csv(sym, bars)
            first = bars[0]["timestamp"][:10]
            last = bars[-1]["timestamp"][:10]
            print(f"  {sym:15s} {len(bars):5d} bars  {first} -> {last}  -> {path.name}")
            ok += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"  {sym:15s} FAILED: {e}", file=sys.stderr)
            fail += 1

    for name, ticker in INDICES.items():
        try:
            bars = fetch_yahoo(ticker, suffix="")
            if len(bars) < MIN_BARS:
                print(f"  {name:15s} SKIP: only {len(bars)} bars (need {MIN_BARS})", file=sys.stderr)
                fail += 1
                continue
            save_csv(name, bars, INDEX_DIR)
            first = bars[0]["timestamp"][:10]
            last = bars[-1]["timestamp"][:10]
            print(f"  {name:15s} {len(bars):5d} bars  {first} -> {last}  (index)")
            ok += 1
        except Exception as e:
            print(f"  {name:15s} FAILED: {e}", file=sys.stderr)
            fail += 1

    print(f"\nDone: {ok} ok, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
