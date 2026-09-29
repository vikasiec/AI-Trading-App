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
                return symbols
            except (json.JSONDecodeError, TypeError):
                logger.exception("Could not parse WATCHLIST_FILE=%s, falling back to default", path)
        else:
            logger.warning("WATCHLIST_FILE=%s does not exist, falling back to default", path)

    logger.info("No WATCHLIST_SYMBOLS or WATCHLIST_FILE configured -- using built-in default watchlist")
    return list(DEFAULT_WATCHLIST)


def try_reload_watchlist_file(
    path: Path,
    current_list: list[str],
    last_mtime: float,
) -> tuple[list[str], float, bool]:
    """Check the watchlist file for changes and reload if needed.

    Returns (new_list, new_mtime, changed). On any failure (empty file,
    truncated write, decode error), keeps current_list but records the
    failed mtime so subsequent ticks stay quiet until the file changes
    again. A missing file keeps last_mtime unchanged (retries each tick).
    """
    try:
        stat = path.stat()
    except OSError:
        return current_list, last_mtime, False

    if stat.st_mtime == last_mtime:
        return current_list, last_mtime, False

    try:
        raw = path.read_text().strip()
        if not raw:
            logger.warning("Watchlist file %s is empty (truncated write?), keeping previous list", path)
            return current_list, stat.st_mtime, False
        data = json.loads(raw)
        symbols = [str(s).strip().upper() for s in data if str(s).strip()]
    except (json.JSONDecodeError, TypeError, OSError):
        logger.exception("Could not parse watchlist file %s, keeping previous list", path)
        return current_list, stat.st_mtime, False

    return symbols, stat.st_mtime, symbols != current_list


def get_watchlist_file_mtime(cfg: AppConfig) -> float:
    """Return the mtime of the watchlist file, or 0.0 if not configured or missing."""
    if not cfg.watchlist_file.strip():
        return 0.0
    path = Path(cfg.watchlist_file).expanduser()
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
