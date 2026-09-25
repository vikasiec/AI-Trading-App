"""Instruments Master lookup.

INDstocks' order API needs a `security_id`, not a plain ticker symbol.
This module downloads and caches the instruments master so the rest of
the codebase can resolve a human-readable symbol (e.g. "RELIANCE") to
the `security_id` and `scrip_code` the API actually expects.

TODO before going live: confirm the exact CSV columns and download
endpoint against the current Instruments Master doc
(https://api-docs.indstocks.com/instruments/) -- the shape below is a
reasonable placeholder based on the fields referenced elsewhere in
INDstocks' docs (security_id, exchange, segment), not a verified schema.
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
            f"{self.cfg.base_url}/market/instruments", headers=self.auth_headers_fn(), timeout=30
        )
        resp.raise_for_status()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_bytes(resp.content)
        logger.info("Instruments master refreshed -> %s", self.cache_path)
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        self._by_symbol.clear()
        with open(self.cache_path, newline="") as f:
            for row in csv.DictReader(f):
                symbol = row.get("symbol") or row.get("trading_symbol")
                if symbol:
                    self._by_symbol[symbol.upper()] = row

    def resolve(self, symbol: str) -> dict:
        """Returns the full instrument row for a trading symbol (e.g. 'RELIANCE')."""
        if not self._by_symbol:
            self.refresh()
        row = self._by_symbol.get(symbol.upper())
        if row is None:
            raise KeyError(f"Symbol {symbol!r} not found in instruments master")
        return row
