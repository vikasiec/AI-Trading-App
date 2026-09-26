"""Exit logic: stop-loss, target, and max-hold-time exits.

Every position this bot opens is tracked in PositionStore with its exit
rules. ExitManager.check_and_exit_all() should run every loop tick,
before any new entries are considered -- closing existing risk takes
priority over opening new risk.

Stop-loss and time-based exits use MARKET orders so a gap through the
stop cannot leave a resting LIMIT unfilled while the local store marks
the position closed. Target exits stay LIMIT at LTP. Kill-switch
flatten remains MARKET.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from .audit import AuditTrail
from .costs import CostRates, compute_round_trip_cost
from .execution_gateway import ExecutionGateway
from . import gtt_orders
from .positions import Position, PositionStore

logger = logging.getLogger(__name__)


def determine_exit_reason(
    position: Position,
    live_ltp: float,
    now: float,
    max_hold_seconds: float,
) -> Optional[str]:
    """Pure function -- no I/O -- so it's trivial to unit test every branch."""
    if live_ltp <= position.stop_loss_price:
        return "stop_loss"
    if live_ltp >= position.target_price:
        return "target"
    if (now - position.opened_at) >= max_hold_seconds:
        return "time_exit"
    return None


class ExitManager:
    def __init__(
        self,
        gateway: ExecutionGateway,
        store: PositionStore,
        audit: AuditTrail,
        max_hold_minutes: float,
        notifier=None,  # TelegramAlertNotifier, optional -- kept loosely typed to avoid a hard import cycle
        paper_trading: bool = True,
        cost_rates: CostRates = CostRates(),
        indstocks_cfg=None,   # INDstocksConfig, needed only if any position carries a gtt_id
        auth_headers_fn=None,
    ):
        self.gateway = gateway
        self.store = store
        self.audit = audit
        self.max_hold_seconds = max_hold_minutes * 60
        self.notifier = notifier
        self.paper_trading = paper_trading
        self.cost_rates = cost_rates
        self.indstocks_cfg = indstocks_cfg
        self.auth_headers_fn = auth_headers_fn

    def check_and_exit_all(self) -> None:
        for position in self.store.list_open():
            self._check_one(position)

    def _check_one(self, position: Position) -> None:
        try:
            live_ltp = self.gateway.get_ltp(position.scrip_code)
        except Exception:
            logger.exception("Could not fetch LTP for %s during exit check -- skipping this tick", position.security_id)
            return

        reason = determine_exit_reason(position, live_ltp, time.time(), self.max_hold_seconds)
        if reason is None:
            return

        self._execute_exit(position, live_ltp, reason)

    def _execute_exit(self, position: Position, live_ltp: float, reason: str) -> None:
        logger.info(
            "Exiting %s: reason=%s entry=%.2f ltp=%.2f qty=%d",
            position.security_id, reason, position.entry_price, live_ltp, position.qty,
        )
        order_id = None
        try:
            if not self.paper_trading:
                if reason in ("stop_loss", "time_exit"):
                    order = self.gateway.place_market_order(
                        security_id=position.security_id,
                        side="SELL",
                        qty=position.qty,
                        exchange=position.exchange,
                        segment=position.segment,
                        product=position.product,
                    )
                else:
                    order = self.gateway.place_limit_order(
                        security_id=position.security_id,
                        side="SELL",
                        qty=position.qty,
                        price=live_ltp,
                        exchange=position.exchange,
                        segment=position.segment,
                        product=position.product,
                    )
                order_id = order.get("data", {}).get("order_id")
        except Exception:
            logger.exception(
                "Exit order FAILED for %s (%s) -- position remains open, will retry next tick",
                position.security_id, reason,
            )
            if self.notifier is not None:
                self.notifier.send_critical_alert(
                    f"Exit order failed for {position.security_id} ({reason}). "
                    f"Position still open -- check manually."
                )
            return

        fill_price = live_ltp
        if not self.paper_trading:
            # Acceptance is not fill -- confirm before touching the audit trail or
            # removing the position from the store. Unlike the entry path, we do NOT
            # attempt a cancel on an ambiguous exit fill: cancelling a SELL that may
            # have already executed would just produce a confusing second error, and
            # leaving the position tracked as still-open is the safe default here --
            # reconciliation.py will catch a real mismatch against the broker.
            fill = self.gateway.wait_for_fill(order_id)
            if fill.status == "REJECTED":
                logger.error(
                    "Exit order %s for %s was REJECTED -- position remains open, will retry next tick",
                    order_id, position.security_id,
                )
                if self.notifier is not None:
                    self.notifier.send_critical_alert(
                        f"Exit order rejected for {position.security_id} ({reason}, order {order_id}). "
                        f"Position still open -- check manually."
                    )
                return
            if fill.status in ("TIMEOUT", "NOT_FOUND"):
                logger.error(
                    "Exit order %s for %s did not reach a terminal state (%s) -- "
                    "leaving position tracked as open, will re-check next tick",
                    order_id, position.security_id, fill.status,
                )
                if self.notifier is not None:
                    self.notifier.send_critical_alert(
                        f"AMBIGUOUS EXIT FILL: {position.security_id} order {order_id} "
                        f"status={fill.status} ({reason}) -- verify manually against the "
                        f"broker order book; position left tracked as open"
                    )
                return
            fill_price = fill.avg_price or live_ltp

        realized_pnl = (fill_price - position.entry_price) * position.qty
        cost = compute_round_trip_cost(
            buy_price=position.entry_price, sell_price=fill_price, qty=position.qty,
            product=position.product, rates=self.cost_rates,
        )
        net = realized_pnl - cost.total

        if position.gtt_id is not None and self.indstocks_cfg is not None and not self.paper_trading:
            gtt_orders.cancel_gtt(self.indstocks_cfg, self.auth_headers_fn, position.gtt_id)

        self.audit.update_outcome(
            position.decision_id, fill_price=fill_price, realized_pnl=realized_pnl,
            net_pnl=net, costs={
                "brokerage": cost.brokerage, "stt": cost.stt, "exchange_txn": cost.exchange_txn,
                "sebi_turnover": cost.sebi_turnover, "stamp_duty": cost.stamp_duty, "gst": cost.gst,
                "total": cost.total,
            },
        )
        self.store.remove(position.security_id)

        if self.notifier is not None:
            self.notifier.send_info(
                f"Exited {position.security_id} ({reason}): entry {position.entry_price:.2f} -> "
                f"{fill_price:.2f}, qty {position.qty}, gross \u20b9{realized_pnl:.2f}, "
                f"costs \u20b9{cost.total:.2f}, net \u20b9{net:.2f}"
            )
