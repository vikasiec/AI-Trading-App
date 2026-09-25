"""Exit logic: stop-loss, target, and max-hold-time exits.

Every position this bot opens is tracked in PositionStore with its exit
rules. ExitManager.check_and_exit_all() should run every loop tick,
before any new entries are considered -- closing existing risk takes
priority over opening new risk.

Exit orders are LIMIT orders at the current LTP, consistent with the
"no MARKET orders" price-collar policy elsewhere in the risk framework
(see docs/ARCHITECTURE.md section 4). The one deliberate exception is
RiskGovernor.flatten_all(), which uses MARKET orders because the kill
switch's job is guaranteed exit, not price control.
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
        try:
            if not self.paper_trading:
                self.gateway.place_limit_order(
                    security_id=position.security_id,
                    side="SELL",
                    qty=position.qty,
                    price=live_ltp,
                    exchange=position.exchange,
                    segment=position.segment,
                    product=position.product,
                )
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

        realized_pnl = (live_ltp - position.entry_price) * position.qty
        cost = compute_round_trip_cost(
            buy_price=position.entry_price, sell_price=live_ltp, qty=position.qty,
            product=position.product, rates=self.cost_rates,
        )
        net = realized_pnl - cost.total

        if position.gtt_id is not None and self.indstocks_cfg is not None and not self.paper_trading:
            gtt_orders.cancel_gtt(self.indstocks_cfg, self.auth_headers_fn, position.gtt_id)

        self.audit.update_outcome(
            position.decision_id, fill_price=live_ltp, realized_pnl=realized_pnl,
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
                f"{live_ltp:.2f}, qty {position.qty}, gross \u20b9{realized_pnl:.2f}, "
                f"costs \u20b9{cost.total:.2f}, net \u20b9{net:.2f}"
            )
