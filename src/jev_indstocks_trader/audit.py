"""Append-only JSONL audit trail.

Every scoring decision -- traded or not -- is logged here, so:
  (a) SEBI Order-to-Trade Ratio review has a record, and
  (b) Jev's conviction scores can be checked against real outcomes
      once positions close (see update_outcome()).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class AuditTrail:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._skip_once: set[tuple[str, str, str]] = set()

    def log_decision(
        self,
        security_id: str,
        jev_conviction: float,
        jev_confidence: float,
        action: str,               # "BUY" | "SELL" | "SKIP"
        latency_ms: float,
        otr_check: str = "PASS",
        order_id: Optional[str] = None,
        had_news: Optional[bool] = None,
        reason: Optional[str] = None,
        symbol: Optional[str] = None,
        detail: Optional[dict] = None,
    ) -> str:
        """Returns a decision_id you can pass to update_outcome() later.

        had_news records whether any matching headlines were fed to Jev
        for this decision -- feeds the H3 news-vs-no-news comparison in
        calibration.py. None means "unknown" (e.g. no news source
        configured at all), which calibration.py treats separately from
        an explicit False.
        """
        decision_id = f"{security_id}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        record = {
            "decision_id": decision_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "security_id": security_id,
            "jev_conviction": jev_conviction,
            "jev_confidence": jev_confidence,
            "otr_check": otr_check,
            "action": action,
            "latency_ms": latency_ms,
            "order_id": order_id,
            "had_news": had_news,
            "fill_price": None,
            "realized_pnl": None,
            "reason": reason,
            "symbol": symbol,
            "detail": detail,
        }
        self._append(record)
        return decision_id

    def log_skip(self, security_id: str, reason: str, symbol: Optional[str] = None) -> None:
        """Log one SKIP row per security, reason, and UTC day.

        The same veto on every tick is one fact. Repeating it fills the
        file with copies and hides the decisions that matter.
        """
        day = datetime.now(timezone.utc).date().isoformat()
        key = (day, security_id, reason)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "security_id": security_id,
            "symbol": symbol,
            "action": "SKIP",
            "skip_reason": reason,
        }
        with self._lock:
            if key in self._skip_once:
                return
            self._skip_once.add(key)
            self._write(record)

    def update_outcome(
        self, decision_id: str, fill_price: float, realized_pnl: float,
        net_pnl: Optional[float] = None, costs: Optional[dict] = None,
        exit_reason: Optional[str] = None,
    ) -> None:
        """Append a correction record rather than mutating history in place --
        JSONL is append-only by design so the audit trail can't be silently edited.

        realized_pnl is gross (price move only); net_pnl, when provided,
        is gross minus the full statutory + brokerage cost of the round
        trip (see costs.py) -- the number that actually matters for
        judging whether a strategy has real edge.
        """
        self._append({
            "decision_id": decision_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "outcome_update",
            "fill_price": fill_price,
            "realized_pnl": realized_pnl,
            "net_pnl": net_pnl,
            "costs": costs,
            "exit_reason": exit_reason,
        })

    def _append(self, record: dict) -> None:
        with self._lock:
            self._write(record)

    def _write(self, record: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")
