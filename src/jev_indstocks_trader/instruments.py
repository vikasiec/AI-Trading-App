"""Instruments Master lookup.

Confirmed against api-docs.indstocks.com/instruments/ (Sep 2026):

  GET /market/instruments?source=equity
  CSV columns: EXCH,SEGMENT,SECURITY_ID,INSTRUMENT_NAME,EXPIRY_CODE,
  TRADING_SYMBOL,LOT_UNITS,CUSTOM_SYMBOL,EXPIRY_DATE,STRIKE_PRICE,
  OPTION_TYPE,TICK_SIZE,EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SERIES,SYMBOL_NAME

Quotes use SEGMENT_TOKEN scrip codes (NSE_2885). Orders use SECURITY_ID (2885).
"""
from __future__ import annotations

import csv
import io
import logging
import time
from pathlib import Path

import requests

from .config import INDstocksConfig

logger = logging.getLogger(__name__)


def _ci(row: dict, *keys: str) -> str | None:
    lower = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        val = lower.get(key.lower())
        if val not in (None, ""):
            return str(val).strip()
    return None


def normalize_instrument_row(row: dict) -> dict | None:
    sid = _ci(row, "SECURITY_ID", "security_id")
    symbol = _ci(row, "SYMBOL_NAME", "TRADING_SYMBOL", "CUSTOM_SYMBOL", "symbol", "trading_symbol")
    if not sid or not symbol:
        return None
    exch = (_ci(row, "EXCH", "exchange") or "NSE").upper()
    segment_raw = (_ci(row, "SEGMENT", "segment") or "EQUITY").upper()
    segment = "EQUITY" if segment_raw in ("EQUITY", "NSE_EQ", "BSE_EQ", "E") else segment_raw
    series = (_ci(row, "SERIES") or "EQ").upper()
    tick_raw = _ci(row, "TICK_SIZE", "tick_size") or "0.05"
    try:
        tick = float(tick_raw)
    except ValueError:
        tick = 0.05
    if tick >= 1:
        tick = tick / 100.0
    lot_raw = _ci(row, "LOT_UNITS", "lot_size", "lot_units") or "1"
    try:
        lot = max(1, int(float(lot_raw)))
    except ValueError:
        lot = 1
    name = _ci(row, "INSTRUMENT_NAME", "CUSTOM_SYMBOL", "name") or symbol
    return {
        "security_id": sid,
        "scrip_code": f"{exch}_{sid}",
        "symbol": symbol.upper().split("-")[0],
        "trading_symbol": (_ci(row, "TRADING_SYMBOL") or symbol).upper(),
        "name": name,
        "exchange": exch,
        "segment": segment,
        "series": series,
        "tick_size": tick,
        "lot_size": lot,
        "raw": row,
    }


class InstrumentsMaster:
    def __init__(self, cfg: INDstocksConfig, auth_headers_fn, cache_path: Path | None = None, ttl_hours: float = 24.0):
        self.cfg = cfg
        self.auth_headers_fn = auth_headers_fn
        self.cache_path = cache_path or (self.cfg.token_cache_path.parent / "instruments_master.csv")
        self.ttl_hours = ttl_hours
        self._by_symbol: dict[str, dict] = {}

    def _needs_refresh(self) -> bool:
        if not self.cache_path.exists():
            return True
        age_hours = (time.time() - self.cache_path.stat().st_mtime) / 3600
        return age_hours > self.ttl_hours

    def refresh(self, force: bool = False) -> None:
        if not force and not self._needs_refresh():
            self._load_from_disk()
            return
        resp = requests.get(
            f"{self.cfg.base_url}/market/instruments",
            headers=self.auth_headers_fn(),
            params={"source": "equity"},
            timeout=30,
        )
        resp.raise_for_status()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_bytes(resp.content)
        logger.info("Instruments master refreshed -> %s", self.cache_path)
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        self._by_symbol.clear()
        text = self.cache_path.read_text(errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            norm = normalize_instrument_row(row)
            if norm is None:
                continue
            if norm["series"] not in ("EQ", "BE", "BZ", ""):
                continue
            key = norm["symbol"]
            existing = self._by_symbol.get(key)
            if existing and existing.get("series") == "EQ" and norm["series"] != "EQ":
                continue
            self._by_symbol[key] = norm
            ts = norm["trading_symbol"].split("-")[0]
            self._by_symbol.setdefault(ts, norm)
        logger.info("Instruments master loaded: %d symbols", len(self._by_symbol))

    def resolve(self, symbol: str) -> dict:
        """Returns a normalized instrument row for a trading symbol (e.g. 'RELIANCE')."""
        if not self._by_symbol:
            self.refresh()
        row = self._by_symbol.get(symbol.upper())
        if row is None:
            raise KeyError(f"Symbol {symbol!r} not found in instruments master")
        return row
