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
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import requests

from .config import INDstocksConfig, RiskConfig

logger = logging.getLogger(__name__)

_CANCEL_STATUSES = frozenset({
    "O-PENDING", "OPEN", "PENDING", "TRIGGER_PENDING",
    "PARTIALLY FILLED", "PARTIAL", "PARTIALLY_FILLED", "PF",
})
# Delivery holdings are not this bot's intraday book.
_DELIVERY_PRODUCTS = frozenset({"CNC", "DELIVERY", "MTF", "NRML"})


@dataclass(frozen=True)
class FlattenResult:
    ok: bool
    still_open: tuple[str, ...]


def round_to_tick(price: float, tick_size: float) -> float:
    tick = Decimal(str(tick_size))
    d = Decimal(str(price))
    return float((d / tick).quantize(0, rounding=ROUND_HALF_UP) * tick)


class RiskGovernor:
    def __init__(self, indstocks_cfg: INDstocksConfig, risk_cfg: RiskConfig, auth_headers_fn):
        self.cfg = indstocks_cfg
        self.risk_cfg = risk_cfg
        self.auth_headers_fn = auth_headers_fn
        self._order_book_loaded = False
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
            for order in resp.json().get("data") or []:
                sid = order.get("security_id") or order.get("name")
                if not sid:
                    continue
                ts = order.get("order_epoch") or order.get("timestamp")
                if isinstance(ts, (int, float)) and ts > 0:
                    if ts > 1e12:
                        ts = ts / 1000.0
                    keys.add(self.window_key(str(sid), float(ts)))
                else:
                    # No timestamp: do not pretend the order was placed this minute.
                    continue
            logger.info("Idempotency state rebuilt: %d prior orders loaded", len(keys))
            self._order_book_loaded = True
            return keys
        except requests.RequestException:
            logger.exception("Could not rebuild idempotency state from order book — new entries blocked until loaded")
            self._order_book_loaded = False
            return set()

    # -- drawdown ----------------------------------------------------------

    def get_drawdown_pct(self) -> float | None:
        try:
            resp = requests.get(f"{self.cfg.base_url}/funds", headers=self.auth_headers_fn(), timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning("Could not fetch /funds for drawdown check — treating as unreadable")
            return None
        d = resp.json()["data"]
        # Start-of-day balance stays put when capital is deployed. Available
        # balance shrinks and would inflate the loss percentage.
        equity = (
            d.get("sod_balance")
            or d.get("net_balance")
            or d.get("available_balance")
            or 0.0
        )
        if equity <= 0:
            logger.error("Funds payload has no usable equity field; refusing to flatten on a guess")
            return None
        pnl_today = float(d.get("realized_pnl", 0.0) or 0.0) + float(d.get("unrealized_pnl", 0.0) or 0.0)
        return max(0.0, -pnl_today / float(equity))

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
        if not self._order_book_loaded:
            self.executed_keys = self._load_idempotency_state()
            if not self._order_book_loaded:
                return False, "order_book_unavailable"

        drawdown = self.get_drawdown_pct()
        if drawdown is None:
            return False, "funds_unreadable"
        if drawdown >= self.risk_cfg.daily_loss_limit_pct:
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

    def size_order(self, equity: float, price: float, lot_size: int = 1) -> int:
        capital = min(
            self.risk_cfg.max_position_capital_inr,
            self.risk_cfg.max_position_pct_equity * equity,
        )
        if price <= 0:
            return 0
        raw = int(capital // price)
        lot = max(1, int(lot_size or 1))
        return max(0, (raw // lot) * lot)

    # -- kill switch ----------------------------------------------------------

    def flatten_all(self, only_security_ids: set[str] | None = None) -> FlattenResult:
        """Cancel working orders and square broker positions for this bot.

        Delivery products (CNC and the like) are never sold. When
        `only_security_ids` is set, only those ids are touched, so a manual
        holding the bot does not track is left alone. One failed request does
        not skip the rest. `ok` is false when a targeted position is still
        open or the positions read itself failed.
        """
        headers = self.auth_headers_fn()
        logger.critical("KILL SWITCH TRIGGERED -- flattening all orders and positions")
        wanted = {str(s) for s in only_security_ids} if only_security_ids is not None else None

        def _tracked(security_id: object) -> bool:
            if wanted is None:
                return True
            return str(security_id) in wanted

        try:
            book = requests.get(f"{self.cfg.base_url}/order-book", headers=headers, timeout=10)
            book.raise_for_status()
            orders = book.json().get("data", []) or []
        except requests.RequestException:
            logger.exception("Error reading the order book during kill switch")
            orders = []

        for order in orders:
            status = str(order.get("status", "")).upper()
            if status not in _CANCEL_STATUSES:
                continue
            sid = order.get("security_id")
            if not _tracked(sid):
                continue
            oid = order.get("id") or order.get("order_id")
            if not oid:
                continue
            segment = order.get("segment", "EQUITY")
            body_segment = "DERIVATIVE" if str(segment).upper() in ("FNO", "DERIVATIVE", "NFO") else "EQUITY"
            try:
                resp = requests.post(
                    f"{self.cfg.base_url}/order/cancel",
                    headers=headers,
                    json={"order_id": oid, "segment": body_segment},
                    timeout=10,
                )
                if resp.status_code >= 400:
                    logger.error("Kill-switch cancel failed for order %s: %s", oid, resp.text)
            except requests.RequestException:
                logger.exception("Kill-switch cancel failed for order %s", oid)

        try:
            pos_resp = requests.get(f"{self.cfg.base_url}/positions", headers=headers, timeout=10)
            pos_resp.raise_for_status()
            positions = pos_resp.json().get("data", []) or []
        except requests.RequestException:
            logger.exception("Error reading positions during kill switch")
            still = tuple(sorted(wanted)) if wanted is not None else ()
            return FlattenResult(ok=False, still_open=still)

        for pos in positions:
            sid = str(pos.get("security_id", ""))
            net_qty = pos.get("net_qty", 0) or 0
            if net_qty == 0 or not sid:
                continue
            product = str(pos.get("product", "")).upper()
            if product in _DELIVERY_PRODUCTS:
                continue
            if not _tracked(sid):
                continue
            side = "SELL" if net_qty > 0 else "BUY"
            try:
                resp = requests.post(
                    f"{self.cfg.base_url}/order",
                    headers=headers,
                    json={
                        "txn_type": side,
                        "exchange": pos["exchange"],
                        "segment": pos["segment"],
                        "security_id": sid,
                        "qty": abs(int(net_qty)),
                        "order_type": "MARKET",
                        "product": pos["product"],
                        "validity": "DAY",
                        "is_amo": False,
                        "algo_id": "99999",
                    },
                    timeout=10,
                )
                if resp.status_code >= 400:
                    logger.error("Kill-switch flatten failed for %s: %s", sid, resp.text)
            except requests.RequestException:
                logger.exception("Kill-switch flatten failed for %s", sid)

        leftover: list[dict] = []
        try:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                check = requests.get(f"{self.cfg.base_url}/positions", headers=headers, timeout=10)
                check.raise_for_status()
                leftover = []
                for p in check.json().get("data", []) or []:
                    if not p.get("net_qty"):
                        continue
                    if str(p.get("product", "")).upper() in _DELIVERY_PRODUCTS:
                        continue
                    if not _tracked(p.get("security_id")):
                        continue
                    leftover.append(p)
                if not leftover:
                    break
                time.sleep(0.4)
        except requests.RequestException:
            logger.exception("Could not re-read positions after kill switch")
            still = tuple(sorted(wanted)) if wanted is not None else ()
            return FlattenResult(ok=False, still_open=still)

        still_ids = tuple(sorted({str(p.get("security_id")) for p in leftover}))
        if still_ids:
            logger.critical("KILL SWITCH incomplete — still open at broker: %s", still_ids)
            return FlattenResult(ok=False, still_open=still_ids)
        return FlattenResult(ok=True, still_open=())
