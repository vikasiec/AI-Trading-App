"""Persistent daily Jev API call counter.

Survives process restarts so the daily cap is enforced across the
calendar day, not just within a single process lifetime.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path.home() / ".indstocks" / "jev_daily_counter.json"


class JevDailyCounter:
    def __init__(self, path: Path = _DEFAULT_PATH):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._date: str = ""
        self._count: int = 0
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path) as f:
                data = json.load(f)
            self._date = data.get("date", "")
            self._count = int(data.get("count", 0))
        except (json.JSONDecodeError, ValueError, OSError):
            logger.warning("Could not load Jev counter from %s, starting fresh", self._path)

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"date": self._date, "count": self._count}, f)
        tmp.replace(self._path)

    def get(self, today: str) -> int:
        if self._date != today:
            return 0
        return self._count

    def increment(self, today: str) -> int:
        if self._date != today:
            self._date = today
            self._count = 0
        self._count += 1
        self._save()
        return self._count

    def reset_if_new_day(self, today: str) -> bool:
        if self._date != today:
            old = self._count
            self._date = today
            self._count = 0
            self._save()
            logger.info("Jev daily count reset (yesterday: %d calls)", old)
            return True
        return False
