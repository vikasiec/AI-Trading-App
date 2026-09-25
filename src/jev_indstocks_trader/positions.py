"""Tracks positions this bot has opened, so exit logic survives a restart.

INDstocks' own /positions endpoint knows what you hold, but not *why* --
it has no concept of the stop-loss, target, or max-hold-time this bot
intended when it opened the position. This store carries that metadata,
keyed by security_id, and is the source of truth ExitManager checks
against every tick.

Persisted as JSON (atomic write, same pattern as auth.py's token cache)
so a process restart doesn't forget a position's exit rules and leave it
open indefinitely.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Position:
    security_id: str
    scrip_code: str          # e.g. "NSE_2885" -- used for LTP lookups
    exchange: str
    segment: str
    product: str
    qty: int
    entry_price: float
    stop_loss_price: float
    target_price: float
    opened_at: float          # epoch seconds
    decision_id: str          # links back to the audit trail entry that opened this
    gtt_id: Optional[str] = None  # set when GTT_ENABLED -- the exchange-side OCO order protecting this position


class PositionStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._positions: dict[str, Position] = self._load()

    def _load(self) -> dict[str, Position]:
        if not self.path.exists():
            return {}
        with open(self.path) as f:
            raw = json.load(f)
        return {k: Position(**v) for k, v in raw.items()}

    def _save(self) -> None:
        tmp_path = self.path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump({k: asdict(v) for k, v in self._positions.items()}, f, indent=2)
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self.path)  # atomic on POSIX

    def add(self, position: Position) -> None:
        with self._lock:
            self._positions[position.security_id] = position
            self._save()

    def remove(self, security_id: str) -> None:
        with self._lock:
            self._positions.pop(security_id, None)
            self._save()

    def list_open(self) -> list[Position]:
        with self._lock:
            return list(self._positions.values())

    def has_open(self, security_id: str) -> bool:
        with self._lock:
            return security_id in self._positions
