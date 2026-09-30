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
from .execution_gateway import ExecutionGateway, extract_order_id
from . import gtt_orders
from .positions import Position, PositionStore

logger = logging.getLogger(__name__)

_COMPLETED_STATUSES = frozenset({"COMPLETE", "FILLED", "EXECUTED", "TRADED", "SUCCESS"})
_DEAD_STATUSES = frozenset({"REJECTED", "CANCELLED", "CANCELED", "EXPIRED", "FAILED", "ABORTED", "RJ"})
_PARTIAL_STATUSES = frozenset({"PARTIALLY FILLED", "PARTIAL", "PF"})
_MARKET_EXIT_REASONS = frozenset({"stop_loss", "time_exit", "session_close", "kill_switch"})


def _costs_payload(slice_cost, total_cost: float) -> dict:
    """Last slice's line items, with total equal to every slice booked so far."""
    return {
        "brokerage": slice_cost.brokerage,
        "stt": slice_cost.stt,
        "exchange_txn": slice_cost.exchange_txn,
        "sebi_turnover": slice_cost.sebi_turnover,
        "stamp_duty": slice_cost.stamp_duty,
        "gst": slice_cost.gst,
        "total": total_cost,
    }


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
        self.stopped_out: set[str] = set()

    def check_and_exit_all(self) -> None:
        for position in self.store.list_open():
            self._check_one(position)

    def force_exit_all(self, reason: str = "session_close") -> None:
        """Square every local position (pre-close flatten)."""
        for position in list(self.store.list_open()):
            try:
                live_ltp = self.gateway.get_ltp(position.scrip_code)
            except Exception:
                logger.exception("No LTP for forced exit %s — leaving it open for the next tick", position.security_id)
                continue
            if not live_ltp or live_ltp <= 0:
                logger.error("No LTP for forced exit %s — leaving it open for the next tick", position.security_id)
                continue
            self._execute_exit(position, live_ltp, reason)

    def _clear_pending_exit(self, position: Position) -> None:
        position.pending_exit_order_id = None
        position.pending_exit_booked_qty = 0

    def _book_pending_fill(
        self, position: Position, filled_qty: int, fill_price: float, exit_reason: str,
    ) -> None:
        """Book only the shares of this order not already in cumulative_pnl.

        filled_qty is the broker's total on the order. A timeout may already
        have booked the first slice and left the order id set.
        """
        delta = filled_qty - position.pending_exit_booked_qty
        if delta <= 0:
            logger.info(
                "Pending exit %s for %s already booked; clearing id, %d shares still open",
                position.pending_exit_order_id, position.security_id, position.qty,
            )
            self._clear_pending_exit(position)
            self.store.add(position)
            return
        self._cancel_gtt(position)
        book_qty = delta if delta <= position.qty else position.qty
        if delta > position.qty:
            logger.error(
                "Pending exit fill %d for %s exceeds local qty %d; booking %d",
                filled_qty, position.security_id, position.qty, book_qty,
            )
        remainder = position.qty - book_qty
        slice_pnl = (fill_price - position.entry_price) * book_qty
        slice_cost = compute_round_trip_cost(
            buy_price=position.entry_price, sell_price=fill_price, qty=book_qty,
            product=position.product, rates=self.cost_rates,
            brokerage_orders=2 if position.cumulative_cost == 0 else 1,
        )
        position.cumulative_pnl += slice_pnl
        position.cumulative_cost += slice_cost.total
        if remainder > 0:
            position.qty = remainder
            self._clear_pending_exit(position)
            self.store.add(position)
        else:
            self._remember_stop(position, exit_reason)
            self.audit.update_outcome(
                position.decision_id, fill_price=fill_price,
                realized_pnl=position.cumulative_pnl,
                net_pnl=position.cumulative_pnl - position.cumulative_cost,
                costs=_costs_payload(slice_cost, position.cumulative_cost),
                exit_reason=exit_reason,
            )
            self.store.remove(position.security_id)
        logger.info(
            "Booked pending fill for %s: %d new shares @ %.2f, %d remaining",
            position.security_id, book_qty, fill_price, remainder,
        )

    def _cancel_gtt(self, position: Position) -> None:
        if position.gtt_id is None or self.indstocks_cfg is None or self.paper_trading:
            return
        try:
            gtt_orders.cancel_gtt(self.indstocks_cfg, self.auth_headers_fn, position.gtt_id)
        except Exception:
            logger.exception("Could not cancel GTT %s for %s", position.gtt_id, position.security_id)
        position.gtt_id = None

    def _remember_stop(self, position: Position, reason: str) -> None:
        if reason == "stop_loss":
            self.stopped_out.add(position.security_id)

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

    def _broker_qty(self, position: Position) -> int | None:
        """Live sell size from the broker book. None means do not sell."""
        if self.paper_trading:
            return position.qty
        net = self.gateway.net_qty(position.security_id)
        if net is None:
            logger.error("Broker position unread for %s — not sending a sell", position.security_id)
            if self.notifier is not None:
                self.notifier.send_critical_alert(
                    f"Exit for {position.security_id} skipped: broker position unread"
                )
            return None
        if net <= 0:
            logger.error(
                "Broker is flat for %s but the local store is open — not sending a sell",
                position.security_id,
            )
            self._cancel_gtt(position)
            self.audit.update_outcome(
                position.decision_id, fill_price=position.entry_price,
                realized_pnl=position.cumulative_pnl,
                net_pnl=position.cumulative_pnl - position.cumulative_cost,
                costs=_costs_payload(
                    compute_round_trip_cost(
                        position.entry_price, position.entry_price, 0,
                        product=position.product, rates=self.cost_rates,
                    ),
                    position.cumulative_cost,
                ),
                exit_reason="broker_flat",
            )
            self.store.remove(position.security_id)
            if self.notifier is not None:
                self.notifier.send_critical_alert(
                    f"{position.security_id} is already flat at the broker. Local position cleared, no sell sent."
                )
            return None
        if net < position.qty:
            logger.warning("Broker net %d is below local qty %d for %s", net, position.qty, position.security_id)
            position.qty = net
            self.store.add(position)
        return position.qty

    def _execute_exit(self, position: Position, live_ltp: float, reason: str) -> None:
        if not self.paper_trading and position.pending_exit_order_id:
            try:
                prior = self.gateway.get_order_status(position.pending_exit_order_id)
            except Exception:
                logger.warning(
                    "Could not check pending exit order %s for %s — will retry next tick",
                    position.pending_exit_order_id, position.security_id,
                )
                return
            if prior is not None:
                status = str(prior.get("status", "")).upper()
                if status in _COMPLETED_STATUSES:
                    filled = prior.get("filled_qty") or prior.get("tradedqty")
                    avg = prior.get("avg_price") or prior.get("average_price")
                    if filled and int(filled) > 0:
                        return self._book_pending_fill(
                            position, int(filled), float(avg) if avg else live_ltp, reason,
                        )
                    logger.error(
                        "Pending exit order %s for %s shows %s but no filled qty — "
                        "alerting; will not send a second sell",
                        position.pending_exit_order_id, position.security_id, status,
                    )
                    if self.notifier is not None:
                        self.notifier.send_critical_alert(
                            f"EXIT ORDER {position.pending_exit_order_id} for {position.security_id} "
                            f"shows {status} with no fill qty — verify manually"
                        )
                    return
                if status not in _DEAD_STATUSES:
                    market_now = reason in _MARKET_EXIT_REASONS or status in _PARTIAL_STATUSES
                    if not market_now:
                        logger.debug(
                            "Exit order %s for %s still in-flight (status=%s), skipping new exit",
                            position.pending_exit_order_id, position.security_id, status,
                        )
                        return
                    logger.warning(
                        "Cancelling resting exit %s for %s so %s can go out as a market order",
                        position.pending_exit_order_id, position.security_id, reason,
                    )
                    try:
                        self.gateway.cancel_order(position.pending_exit_order_id)
                    except Exception:
                        logger.exception(
                            "Could not cancel resting exit %s", position.pending_exit_order_id,
                        )
                        return
                    filled = prior.get("filled_qty") or prior.get("tradedqty")
                    avg = prior.get("avg_price") or prior.get("average_price")
                    if filled and int(filled) > 0:
                        self._book_pending_fill(
                            position, int(filled), float(avg) if avg else live_ltp, reason,
                        )
                        if not self.store.has_open(position.security_id):
                            self._remember_stop(position, reason)
                            return
                        position = self.store.get(position.security_id) or position
            self._clear_pending_exit(position)
            self.store.add(position)

        if self._broker_qty(position) is None:
            return
        logger.info(
            "Exiting %s: reason=%s entry=%.2f ltp=%.2f qty=%d",
            position.security_id, reason, position.entry_price, live_ltp, position.qty,
        )
        order_id = None
        try:
            if not self.paper_trading:
                if reason in ("stop_loss", "time_exit", "session_close", "kill_switch"):
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
                order_id = extract_order_id(order)
                if not order_id:
                    raise RuntimeError(f"exit place-order returned no id: {order}")
                position.pending_exit_order_id = order_id
                position.pending_exit_booked_qty = 0
                self.store.add(position)
        except Exception:
            logger.exception(
                "Exit order FAILED for %s (%s) -- checking the order book before another sell",
                position.security_id, reason,
            )
            adopted = None
            try:
                adopted = self.gateway.find_recent_order_id(position.security_id, "SELL")
            except Exception:
                logger.exception("Order book lookup failed after exit error for %s", position.security_id)
            if isinstance(adopted, str) and adopted:
                position.pending_exit_order_id = adopted
                position.pending_exit_booked_qty = 0
                self.store.add(position)
                logger.error("Adopted existing sell %s for %s — will not send a second sell", adopted, position.security_id)
            if self.notifier is not None:
                self.notifier.send_critical_alert(
                    f"Exit order failed for {position.security_id} ({reason}). "
                    + (
                        f"Tracking existing order {adopted}."
                        if isinstance(adopted, str) and adopted
                        else "Position still open -- check manually."
                    )
                )
            return

        fill_price = live_ltp
        if not self.paper_trading:
            fill = self.gateway.wait_for_fill(order_id, timeout_s=3.0, requested_qty=position.qty)
            if fill.status in ("REJECTED", "CANCELLED"):
                logger.error(
                    "Exit order %s for %s was REJECTED -- position remains open, will retry next tick",
                    order_id, position.security_id,
                )
                self._clear_pending_exit(position)
                self.store.add(position)
                if self.notifier is not None:
                    self.notifier.send_critical_alert(
                        f"Exit order rejected for {position.security_id} ({reason}, order {order_id}). "
                        f"Position still open -- check manually."
                    )
                return
            if fill.status in ("TIMEOUT", "NOT_FOUND"):
                if fill.filled_qty and fill.filled_qty > 0:
                    closed_qty = fill.filled_qty
                    fill_price = fill.avg_price or live_ltp
                    remainder = position.qty - closed_qty
                    slice_pnl = (fill_price - position.entry_price) * closed_qty
                    self._cancel_gtt(position)
                    slice_cost = compute_round_trip_cost(
                        buy_price=position.entry_price, sell_price=fill_price, qty=closed_qty,
                        product=position.product, rates=self.cost_rates,
                        brokerage_orders=2 if position.cumulative_cost == 0 else 1,
                    )
                    position.cumulative_pnl += slice_pnl
                    position.cumulative_cost += slice_cost.total
                    if remainder > 0:
                        position.qty = remainder
                        position.pending_exit_booked_qty = closed_qty
                        self.store.add(position)
                    else:
                        self._remember_stop(position, reason)
                        self.audit.update_outcome(
                            position.decision_id, fill_price=fill_price,
                            realized_pnl=position.cumulative_pnl,
                            net_pnl=position.cumulative_pnl - position.cumulative_cost,
                            costs=_costs_payload(slice_cost, position.cumulative_cost),
                            exit_reason=reason,
                        )
                        self.store.remove(position.security_id)
                    if self.notifier is not None:
                        self.notifier.send_critical_alert(
                            f"TIMEOUT PARTIAL EXIT {position.security_id}: {closed_qty} filled, "
                            f"{remainder} remaining — order {order_id}"
                        )
                    return
                logger.error(
                    "Exit order %s for %s did not reach a terminal state (%s) -- "
                    "pending exit tracked, will re-check next tick",
                    order_id, position.security_id, fill.status,
                )
                if self.notifier is not None:
                    self.notifier.send_critical_alert(
                        f"AMBIGUOUS EXIT FILL: {position.security_id} order {order_id} "
                        f"status={fill.status} ({reason}) -- verify manually against the "
                        f"broker order book; pending exit order tracked"
                    )
                return
            if fill.status == "PARTIAL":
                closed_qty = fill.filled_qty
                fill_price = fill.avg_price or live_ltp
                remainder = position.qty - closed_qty
                logger.error(
                    "Partial exit fill for %s: closed %d, remainder %d left open",
                    position.security_id, closed_qty, remainder,
                )
                try:
                    gateway_cancel_ok = True
                    self.gateway.cancel_order(order_id)
                except Exception:
                    gateway_cancel_ok = False
                    logger.exception("Could not cancel residual exit order %s", order_id)
                slice_pnl = (fill_price - position.entry_price) * closed_qty
                self._cancel_gtt(position)
                slice_cost = compute_round_trip_cost(
                    buy_price=position.entry_price, sell_price=fill_price, qty=closed_qty,
                    product=position.product, rates=self.cost_rates,
                    brokerage_orders=2 if position.cumulative_cost == 0 else 1,
                )
                position.cumulative_pnl += slice_pnl
                position.cumulative_cost += slice_cost.total
                position.qty = remainder
                if gateway_cancel_ok:
                    self._clear_pending_exit(position)
                else:
                    position.pending_exit_order_id = order_id
                    position.pending_exit_booked_qty = closed_qty
                self.store.add(position)
                if self.notifier is not None:
                    self.notifier.send_critical_alert(
                        f"PARTIAL EXIT {position.security_id}: sold {closed_qty}, "
                        f"{remainder} still open — will retry next tick"
                    )
                return
            fill_price = fill.avg_price or live_ltp
            self._clear_pending_exit(position)

        final_slice_pnl = (fill_price - position.entry_price) * position.qty
        cost = compute_round_trip_cost(
            buy_price=position.entry_price, sell_price=fill_price, qty=position.qty,
            product=position.product, rates=self.cost_rates,
            brokerage_orders=2 if position.cumulative_cost == 0 else 1,
        )
        realized_pnl = position.cumulative_pnl + final_slice_pnl
        total_cost = position.cumulative_cost + cost.total
        net = realized_pnl - total_cost

        if position.gtt_id is not None and self.indstocks_cfg is not None and not self.paper_trading:
            gtt_orders.cancel_gtt(self.indstocks_cfg, self.auth_headers_fn, position.gtt_id)

        self._remember_stop(position, reason)
        self.audit.update_outcome(
            position.decision_id, fill_price=fill_price, realized_pnl=realized_pnl,
            net_pnl=net, costs=_costs_payload(cost, total_cost),
            exit_reason=reason,
        )
        self.store.remove(position.security_id)

        if self.notifier is not None:
            self.notifier.send_info(
                f"Exited {position.security_id} ({reason}): entry {position.entry_price:.2f} -> "
                f"{fill_price:.2f}, qty {position.qty}, gross \u20b9{realized_pnl:.2f}, "
                f"costs \u20b9{total_cost:.2f}, net \u20b9{net:.2f}"
            )
