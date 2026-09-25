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

    def _load_idempotency_state(self) -> set:
        """Rebuild from the broker's own order book on startup."""
        try:
            resp = requests.get(
                f"{self.cfg.base_url}/order-book",
                headers=self.auth_headers_fn(),
                timeout=10,
            )
            resp.raise_for_status()
            keys = set()
            for order in resp.json().get("data", []):
                keys.add(f"{order.get('name')}_{order.get('window', '')}")
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
        equity = d.get("sod_balance") or 1.0
        pnl_today = d.get("realized_pnl", 0.0) + d.get("unrealized_pnl", 0.0)
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

        window_key = f"{security_id}_{int(time.time() // 60)}"
        if window_key in self.executed_keys:
            return False, "duplicate_in_window"

        self.executed_keys.add(window_key)
        return True, "approved"

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
            orders = requests.get(f"{self.cfg.base_url}/order-book", headers=headers, timeout=10).json()
            for order in orders.get("data", []):
                if order.get("status") in ("O-PENDING", "OPEN"):
                    requests.delete(
                        f"{self.cfg.base_url}/order/{order['id']}", headers=headers, timeout=10
                    )
        except requests.RequestException:
            logger.exception("Error cancelling open orders during kill switch")

        try:
            positions = requests.get(f"{self.cfg.base_url}/positions", headers=headers, timeout=10).json()
            for pos in positions.get("data", []):
                net_qty = pos.get("net_qty", 0)
                if net_qty == 0:
                    continue
                side = "SELL" if net_qty > 0 else "BUY"
                requests.post(
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
        except requests.RequestException:
            logger.exception("Error squaring off positions during kill switch")
