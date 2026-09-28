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
from dataclasses import dataclass, field

from .execution_gateway import ExecutionGateway
from .positions import PositionStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QtyMismatch:
    security_id: str
    local_qty: int
    broker_qty: int


@dataclass(frozen=True)
class ReconciliationReport:
    ok: bool
    untracked_broker_positions: list[str]   # security_ids the broker shows open but we aren't tracking
    missing_broker_positions: list[str]     # security_ids we're tracking but the broker shows closed
    qty_mismatches: list[QtyMismatch] = field(default_factory=list)


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
        broker_qty_map: dict[str, int] = {}
        for p in broker_positions:
            net = p.get("net_qty", 0)
            if net != 0:
                broker_qty_map[str(p["security_id"])] = int(net)

        broker_open_ids = set(broker_qty_map.keys())
        local_positions = {p.security_id: p for p in self.store.list_open()}
        local_open_ids = set(local_positions.keys())

        untracked = sorted(broker_open_ids - local_open_ids)
        missing = sorted(local_open_ids - broker_open_ids)

        qty_mismatches: list[QtyMismatch] = []
        for sid in sorted(broker_open_ids & local_open_ids):
            local_qty = local_positions[sid].qty
            broker_qty = broker_qty_map[sid]
            if local_qty != broker_qty:
                qty_mismatches.append(QtyMismatch(sid, local_qty, broker_qty))

        report = ReconciliationReport(
            ok=not untracked and not missing and not qty_mismatches,
            untracked_broker_positions=untracked,
            missing_broker_positions=missing,
            qty_mismatches=qty_mismatches,
        )

        if not report.ok:
            parts = []
            if untracked:
                parts.append(f"broker has untracked positions {untracked}")
            if missing:
                parts.append(f"local store has positions the broker no longer shows {missing}")
            if qty_mismatches:
                qty_details = ", ".join(
                    f"{m.security_id} local={m.local_qty} broker={m.broker_qty}"
                    for m in qty_mismatches
                )
                parts.append(f"qty mismatch: {qty_details}")
            msg = f"Reconciliation mismatch -- {'; '.join(parts)}. Not auto-correcting; check manually."
            logger.error(msg)
            if self.notifier is not None:
                self.notifier.send_critical_alert(msg)
        else:
            logger.debug("Reconciliation OK: local and broker positions match")

        return report
