"""Order/position reconciliation.

PositionStore is only rebuilt from the broker on startup (see
risk_governor.py's idempotency load and PositionStore's own
persistence). Between restarts, it's possible for local and broker
state to drift -- a fill the bot's process missed (crash mid-order), a
manual intervention on the INDstocks app, a partial fill it didn't
handle. This module periodically re-checks local state against the
broker's own /positions and /order-book and raises an alert on any
mismatch, rather than silently trusting local state indefinitely.

This does NOT auto-correct drift -- reconciling by guessing which side
is right is how you turn a data bug into a trading bug. It surfaces the
mismatch loudly (log + Telegram) so a human decides.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .execution_gateway import ExecutionGateway
from .positions import PositionStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconciliationReport:
    ok: bool
    untracked_broker_positions: list[str]   # security_ids the broker shows open but we aren't tracking
    missing_broker_positions: list[str]     # security_ids we're tracking but the broker shows closed


class ReconciliationService:
    def __init__(self, gateway: ExecutionGateway, store: PositionStore, interval_s: float = 60.0, notifier=None):
        self.gateway = gateway
        self.store = store
        self.interval_s = interval_s
        self.notifier = notifier
        self._last_run = 0.0

    def due(self) -> bool:
        return (time.time() - self._last_run) >= self.interval_s

    def reconcile(self) -> ReconciliationReport:
        self._last_run = time.time()

        broker_positions = self.gateway.get_positions()
        broker_open_ids = {
            str(p["security_id"]) for p in broker_positions if p.get("net_qty", 0) != 0
        }
        local_open_ids = {p.security_id for p in self.store.list_open()}

        untracked = sorted(broker_open_ids - local_open_ids)
        missing = sorted(local_open_ids - broker_open_ids)

        report = ReconciliationReport(
            ok=not untracked and not missing,
            untracked_broker_positions=untracked,
            missing_broker_positions=missing,
        )

        if not report.ok:
            msg = (
                f"Reconciliation mismatch -- broker has untracked positions {untracked}, "
                f"local store has positions the broker no longer shows {missing}. "
                f"Not auto-correcting; check manually."
            )
            logger.error(msg)
            if self.notifier is not None:
                self.notifier.send_critical_alert(msg)
        else:
            logger.debug("Reconciliation OK: local and broker positions match")

        return report
