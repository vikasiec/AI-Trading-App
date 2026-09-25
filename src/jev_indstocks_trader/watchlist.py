"""Watchlist source.

Replaces the hardcoded `["RELIANCE"]` that used to live in main.py.
Resolution order:
  1. WATCHLIST_SYMBOLS env var -- comma-separated symbols, for quick overrides.
  2. WATCHLIST_FILE -- a JSON file with a top-level list of symbols, for a
     watchlist you maintain and version separately from your .env.
  3. A small built-in default, so the bot still runs out of the box.

Either source is re-read on every call to load_watchlist() -- cheap, and
lets you edit the watchlist file without restarting the process.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import AppConfig

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST = ["RELIANCE", "TCS", "HDFCBANK", "INFY"]


def load_watchlist(cfg: AppConfig) -> list[str]:
    if cfg.watchlist_symbols.strip():
        symbols = [s.strip().upper() for s in cfg.watchlist_symbols.split(",") if s.strip()]
        if symbols:
            return symbols

    if cfg.watchlist_file.strip():
        path = Path(cfg.watchlist_file).expanduser()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                symbols = [str(s).strip().upper() for s in data if str(s).strip()]
                if symbols:
                    return symbols
            except (json.JSONDecodeError, TypeError):
                logger.exception("Could not parse WATCHLIST_FILE=%s, falling back to default", path)
        else:
            logger.warning("WATCHLIST_FILE=%s does not exist, falling back to default", path)

    logger.info("No WATCHLIST_SYMBOLS or WATCHLIST_FILE configured -- using built-in default watchlist")
    return list(DEFAULT_WATCHLIST)
