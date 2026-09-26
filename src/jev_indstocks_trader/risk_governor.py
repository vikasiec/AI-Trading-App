"""Deterministic risk governor.

This is the ONLY layer allowed to say yes to a trade. It owns:
  - position sizing (against live equity, not a hardcoded number)
  - daily drawdown tracking (against live /funds, not an in-memory counter)
  - price-collar enforcement (reject if live price has moved too far
    since Jev scored the signal)
  - idempotency (rebuilt from the broker's own order book on startup,
    not trusted from memory alone)
  - the kill switch (cancels open orders AND squares off open positions)

Nothing here should ever assume the in-memory state is authoritative --
always reconcile against INDstocks' own records first.
"""
from __future__ import annotations

import logging
import time
from decimal import ROUND_HALF_UP, Decimal

import requests

from .config import INDstocksConfig, RiskConfig

logger = logging.getLogger(__name__)


def round_to_tick(price: float, tick_size: float) -> float:
    tick = Decimal(str(tick_size))
    d = Decimal(str(price))
    return float((d / tick).quantize(0, rounding=ROUND_HALF_UP) * tick)


class RiskGovernor:
    def __init__(self, indstocks_cfg: INDstocksConfig, risk_cfg: RiskConfig, auth_headers_fn):
        self.cfg = indstocks_cfg
        self.risk_cfg = risk_cfg
        self.auth_headers_fn = auth_headers_fn
        self.executed_keys: set[str] = self._load_idempotency_state()

    # -- idempotency -----------------------------------------------------

    @staticmethod
    def window_key(security_id: str, ts: float | None = None) -> str:
        """Same key the live loop and the startup rebuild must both use."""
        minute = int((ts if ts is not None else time.time()) // 60)
        return f"{security_id}_{minute}"

    def _load_idempotency_state(self) -> set:
        """Rebuild from the broker's own order book on startup.

        Keys are `{security_id}_{epoch_minute}` — the same shape
        `mark_executed` writes. Older code used `{name}_{window}`, which
        never matched a live key and made the rebuild a no-op.
        """
        try:
            resp = requests.get(
                f"{self.cfg.base_url}/order-book",
                headers=self.auth_headers_fn(),
                timeout=10,
            )
            resp.raise_for_status()
            keys: set[str] = set()
            now = time.time()
            for order in resp.json().get("data", []):
                sid = order.get("security_id") or order.get("name")
                if not sid:
                    continue
                ts = order.get("order_epoch") or order.get("timestamp")
                if isinstance(ts, (int, float)) and ts > 0:
                    if ts > 1e12:
                        ts = ts / 1000.0
                    keys.add(self.window_key(str(sid), float(ts)))
                else:
                    keys.add(self.window_key(str(sid), now))
            logger.info("Idempotency state rebuilt: %d prior orders loaded", len(keys))
            return keys
        except requests.RequestException:
            logger.exception("Could not rebuild idempotency state from order book; starting empty")
            return set()

    # -- drawdown ----------------------------------------------------------

    def get_drawdown_pct(self) -> float:
        resp = requests.get(f"{self.cfg.base_url}/funds", headers=self.auth_headers_fn(), timeout=10)
        resp.raise_for_status()
        d = resp.json()["data"]
        equity = (
            d.get("available_balance")
            or d.get("net_balance")
            or d.get("sod_balance")
            or 0.0
        )
        if equity <= 0:
            logger.error("Funds payload has no usable equity field; treating drawdown as blocking")
            return 1.0
        pnl_today = float(d.get("realized_pnl", 0.0) or 0.0) + float(d.get("unrealized_pnl", 0.0) or 0.0)
        return max(0.0, -pnl_today / equity)

    # -- validation ----------------------------------------------------------

    def validate_trade(
        self,
        security_id: str,
        live_ltp: float,
        scored_at_price: float,
        conviction: float,
        confidence: float,
    ) -> tuple[bool, str]:
        """Returns (approved, reason). reason is always populated for the audit log."""
        if self.get_drawdown_pct() >= self.risk_cfg.daily_loss_limit_pct:
            self.flatten_all()
            return False, "daily_drawdown_limit_hit"

        if conviction < self.risk_cfg.min_conviction or confidence < self.risk_cfg.min_confidence:
            return False, "below_conviction_or_confidence_threshold"

        if scored_at_price <= 0:
            return False, "invalid_scored_price"

        slippage_pct = abs(live_ltp - scored_at_price) / scored_at_price
        if slippage_pct > self.risk_cfg.max_slippage_pct:
            return False, f"slippage_{slippage_pct:.4f}_exceeds_collar"

        window_key = self.window_key(security_id)
        if window_key in self.executed_keys:
            return False, "duplicate_in_window"

        # Do NOT reserve the slot here. Sizing, portfolio caps, or
        # place_order can still fail; claiming the key early would lock
        # the symbol out for the rest of the minute after a no-op.
        return True, "approved"

    def mark_executed(self, security_id: str) -> None:
        """Call only after a paper fill is booked or a live order is ACK'd."""
        self.executed_keys.add(self.window_key(security_id))

    # -- sizing ----------------------------------------------------------

    def size_order(self, equity: float, price: float) -> int:
        capital = min(
            self.risk_cfg.max_position_capital_inr,
            self.risk_cfg.max_position_pct_equity * equity,
        )
        if price <= 0:
            return 0
        return max(0, int(capital // price))

    # -- kill switch ----------------------------------------------------------

    def flatten_all(self) -> None:
        """Cancel every open order, then square off every open position.
        Both steps run even if one fails partway -- log and continue.
        """
        headers = self.auth_headers_fn()
        logger.critical("KILL SWITCH TRIGGERED -- flattening all orders and positions")

        try:
            book = requests.get(f"{self.cfg.base_url}/order-book", headers=headers, timeout=10)
            book.raise_for_status()
            for order in book.json().get("data", []):
                if order.get("status") not in ("O-PENDING", "OPEN", "PENDING", "TRIGGER_PENDING"):
                    continue
                oid = order.get("id") or order.get("order_id")
                if not oid:
                    continue
                resp = requests.delete(
                    f"{self.cfg.base_url}/order/{oid}", headers=headers, timeout=10
                )
                if resp.status_code >= 400:
                    logger.error("Kill-switch cancel failed for order %s: %s", oid, resp.text)
        except requests.RequestException:
            logger.exception("Error cancelling open orders during kill switch")

        try:
            pos_resp = requests.get(f"{self.cfg.base_url}/positions", headers=headers, timeout=10)
            pos_resp.raise_for_status()
            for pos in pos_resp.json().get("data", []):
                net_qty = pos.get("net_qty", 0)
                if net_qty == 0:
                    continue
                side = "SELL" if net_qty > 0 else "BUY"
                resp = requests.post(
                    f"{self.cfg.base_url}/order",
                    headers=headers,
                    json={
                        "txn_type": side,
                        "exchange": pos["exchange"],
                        "segment": pos["segment"],
                        "security_id": pos["security_id"],
                        "qty": abs(net_qty),
                        "order_type": "MARKET",
                        "product": pos["product"],
                        "validity": "DAY",
                        "is_amo": False,
                        "algo_id": "99999",
                    },
                    timeout=10,
                )
                if resp.status_code >= 400:
                    logger.error(
                        "Kill-switch flatten failed for %s: %s",
                        pos.get("security_id"), resp.text,
                    )
            # Acceptance is not flat — re-read positions briefly and shout leftovers.
            leftover = []
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                check = requests.get(f"{self.cfg.base_url}/positions", headers=headers, timeout=10)
                check.raise_for_status()
                leftover = [
                    p for p in check.json().get("data", []) if p.get("net_qty", 0) != 0
                ]
                if not leftover:
                    break
                time.sleep(0.4)
            if leftover:
                logger.critical(
                    "KILL SWITCH incomplete — still open at broker: %s",
                    [(p.get("security_id"), p.get("net_qty")) for p in leftover],
                )
        except requests.RequestException:
            logger.exception("Error squaring off positions during kill switch")
