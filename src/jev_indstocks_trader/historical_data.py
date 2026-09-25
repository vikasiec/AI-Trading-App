"""Historical OHLCV bars (D2 in docs/INTELLIGENCE_ROADMAP.md).

Two ways in, deliberately decoupled from each other:

1. `load_from_csv()` -- always works, no API dependency. Point it at a CSV
   with columns timestamp, open, high, low, close, volume (any standard
   export from NSE's own historical data, a broker's export, or
   INDstocks' historical endpoint downloaded by hand). This is what
   backtest.py is built and tested against, so the backtest engine's
   correctness never depends on an unconfirmed API contract.

2. `fetch_from_indstocks()` -- best-effort placeholder for INDstocks'
   Historical Data API. api-docs.indstocks.com/historicalData/ wasn't
   reachable while building this, so the endpoint path and response
   shape below are a guess based on the pattern of INDstocks' other
   confirmed endpoints (REST, JSON, `data` envelope). CONFIRM before
   relying on this -- same caveat as market_data.py's WebSocket feed.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

from .config import INDstocksConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


def load_from_csv(path: Path) -> list[Bar]:
    """CSV columns expected: timestamp, open, high, low, close, volume.
    timestamp parsed as ISO 8601. Rows are returned in file order -- sort
    your CSV chronologically before use.
    """
    bars = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            bars.append(Bar(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(float(row["volume"])),
            ))
    return bars


def fetch_from_indstocks(
    cfg: INDstocksConfig,
    auth_headers_fn,
    scrip_code: str,
    from_date: str,   # "YYYY-MM-DD"
    to_date: str,      # "YYYY-MM-DD"
    interval: str = "day",
) -> list[Bar]:
    """CONFIRM THE ENDPOINT before relying on this -- see module docstring.
    Placeholder shape modeled on INDstocks' other confirmed REST endpoints:
    GET with query params, {"status": "success", "data": [...]} envelope.
    """
    resp = requests.get(
        f"{cfg.base_url}/historical/candles",
        headers=auth_headers_fn(),
        params={
            "scrip-code": scrip_code,
            "from": from_date,
            "to": to_date,
            "interval": interval,
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    bars = []
    for row in body.get("data", []):
        bars.append(Bar(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
        ))
    return bars


def save_to_csv(bars: list[Bar], path: Path) -> None:
    """Round-trip helper -- cache a fetched series locally so backtests
    don't need network access to re-run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for bar in bars:
            writer.writerow([bar.timestamp.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume])
