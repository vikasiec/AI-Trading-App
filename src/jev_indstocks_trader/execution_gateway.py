"""INDstocks order placement and order-book queries.

Order payload fields (txn_type, security_id, algo_id, etc.) match
INDstocks' published /order endpoint -- see docs/ARCHITECTURE.md section 5
for the corrected request shape and the mistakes it replaces.

Fill confirmation
------------------
A 200 OK from POST /order means the broker *accepted* the order -- it says
nothing about whether the order actually *filled*, partially filled, or was
rejected a moment later. Every earlier version of this module (and of
main.py / exits.py) treated acceptance as equivalent to fill and computed
P&L / opened-or-closed positions off the *requested* price. That's wrong:
a LIMIT order can sit unfilled, and even a MARKET order can be rejected for
reasons invisible at submission time (margin, circuit limits, etc).

wait_for_fill() closes that gap by polling the order book to a terminal
state (filled or rejected) or a timeout, and callers use the *confirmed*
avg_price for anything money-related. An ambiguous outcome (TIMEOUT /
NOT_FOUND) is never treated as either a fill or a non-fill -- callers must
handle it explicitly (see main.py's entry path and exits.py's exit path).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .config import INDstocksConfig
from .risk_governor import round_to_tick

logger = logging.getLogger(__name__)

FILLED_STATUSES = {"COMPLETE", "FILLED", "EXECUTED", "TRADED", "SUCCESS"}
PARTIAL_STATUSES = {"PARTIALLY FILLED", "PARTIAL", "PF"}
REJECTED_STATUSES = {"REJECTED", "FAILED", "EXPIRED", "ABORTED", "RJ"}
CANCELLED_STATUSES = {"CANCELLED", "CANCELED", "PARTIALLY FILLED - CANCELLED", "PARTIALLY FILLED - EXPIRED", "PFC"}
ORDER_ID_KEYS = ("order_id", "id", "oms_order_id", "orderId")
FILLED_QTY_KEYS = ("filled_qty", "traded_qty", "filledQty", "tradedQty", "filled_quantity")


def extract_order_id(payload: object) -> Optional[str]:
    """Pull an order id out of a place-order response or a book row."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return None
    for key in ORDER_ID_KEYS:
        val = data.get(key)
        if val not in (None, ""):
            return str(val)
    return None


def _filled_qty(order: dict) -> Optional[int]:
    """Only real fill fields — never fall back to the requested `qty`."""
    for key in FILLED_QTY_KEYS:
        val = order.get(key)
        if val not in (None, ""):
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
    return None


def _avg_price(order: dict) -> Optional[float]:
    for key in ("avg_price", "average_price", "avgPrice", "averagePrice", "traded_price"):
        val = order.get(key)
        if val not in (None, ""):
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


@dataclass(frozen=True)
class FillResult:
    """Outcome of polling an order to a terminal state (or timing out).

    status is one of "FILLED", "PARTIAL", "REJECTED", "CANCELLED",
    "TIMEOUT", "NOT_FOUND". avg_price / filled_qty are only meaningful
    for FILLED and PARTIAL.
    """

    status: str
    filled_qty: int
    avg_price: Optional[float]
    raw: Optional[dict]


class ExecutionGateway:
    def __init__(self, cfg: INDstocksConfig, auth_headers_fn, tick_size: float = 0.05):
        self.cfg = cfg
        self.auth_headers_fn = auth_headers_fn
        self.tick_size = tick_size

    def place_market_order(
        self,
        security_id: str,
        side: str,
        qty: int,
        exchange: str = "NSE",
        segment: str = "EQUITY",
        product: str = "INTRADAY",
        validity: str = "DAY",
    ) -> dict:
        """MARKET path for stop-loss, time exits, and flatten. New entries stay LIMIT."""
        order_data = {
            "txn_type": side,
            "exchange": exchange,
            "segment": segment,
            "security_id": security_id,
            "qty": qty,
            "order_type": "MARKET",
            "validity": validity,
            "product": product,
            "is_amo": False,
            "algo_id": "99999",
        }
        resp = requests.post(
            f"{self.cfg.base_url}/order", headers=self.auth_headers_fn(), json=order_data, timeout=10
        )
        if resp.status_code != 200:
            logger.error("Market order placement failed: %s", resp.text)
        resp.raise_for_status()
        return resp.json()

    def place_limit_order(
        self,
        security_id: str,
        side: str,               # "BUY" | "SELL"
        qty: int,
        price: float,
        exchange: str = "NSE",
        segment: str = "EQUITY",
        product: str = "INTRADAY",
        validity: str = "DAY",
    ) -> dict:
        order_data = {
            "txn_type": side,
            "exchange": exchange,
            "segment": segment,
            "security_id": security_id,
            "qty": qty,
            "order_type": "LIMIT",
            "limit_price": round_to_tick(price, self.tick_size),
            "validity": validity,
            "product": product,
            "is_amo": False,
            "algo_id": "99999",  # required by INDstocks for regular (non-partner) algo orders
        }
        resp = requests.post(
            f"{self.cfg.base_url}/order", headers=self.auth_headers_fn(), json=order_data, timeout=10
        )
        if resp.status_code != 200:
            logger.error("Order placement failed: %s", resp.text)
        resp.raise_for_status()
        return resp.json()

    def get_order_book(self) -> list[dict]:
        resp = requests.get(f"{self.cfg.base_url}/order-book", headers=self.auth_headers_fn(), timeout=10)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def get_positions(self) -> list[dict]:
        resp = requests.get(f"{self.cfg.base_url}/positions", headers=self.auth_headers_fn(), timeout=10)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def get_funds(self) -> dict:
        resp = requests.get(f"{self.cfg.base_url}/funds", headers=self.auth_headers_fn(), timeout=10)
        resp.raise_for_status()
        return resp.json()["data"]

    def equity_from_funds(self, funds: dict | None = None) -> float:
        """Prefer available/net balance over start-of-day; 0 if unknown."""
        d = funds if funds is not None else self.get_funds()
        for key in ("available_balance", "net_balance", "sod_balance", "equity"):
            val = d.get(key)
            if val not in (None, ""):
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def get_quote(self, scrip_code: str) -> dict:
        """LTP plus whatever extra fields the LTP endpoint happens to include.

        day_change_pct / volume are 0 when the broker payload doesn't
        carry them — never invent a number just to fill Jev's template.
        """
        resp = requests.get(
            f"{self.cfg.base_url}/market/quotes/ltp",
            headers=self.auth_headers_fn(),
            params={"scrip-codes": scrip_code},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()["data"][scrip_code]
        ltp = data.get("live_price", data.get("ltp"))
        change = data.get("day_change_pct", data.get("change_perc", data.get("pChange", 0.0)))
        volume = data.get("volume", data.get("vol", 0))
        try:
            change_f = float(change or 0.0)
        except (TypeError, ValueError):
            change_f = 0.0
        try:
            vol_i = int(volume or 0)
        except (TypeError, ValueError):
            vol_i = 0
        return {"ltp": float(ltp), "day_change_pct": change_f, "volume": vol_i}

    def get_ltp(self, scrip_code: str) -> float:
        """scrip_code is the exchange-prefixed code, e.g. 'NSE_2885' -- from Instruments Master."""
        return self.get_quote(scrip_code)["ltp"]

    def get_order_status(self, order_id: str) -> Optional[dict]:
        """Finds one order in the order book by id. Returns None if not found."""
        if not order_id:
            return None
        for order in self.get_order_book():
            oid = extract_order_id(order)
            if oid is not None and oid == str(order_id):
                return order
        return None

    def cancel_order(self, order_id: str, segment: str = "EQUITY") -> None:
        """POST /order/cancel — confirmed api-docs.indstocks.com/normal_orders/."""
        body_segment = "DERIVATIVE" if str(segment).upper() in ("FNO", "DERIVATIVE", "NFO") else "EQUITY"
        resp = requests.post(
            f"{self.cfg.base_url}/order/cancel",
            headers=self.auth_headers_fn(),
            json={"order_id": order_id, "segment": body_segment},
            timeout=10,
        )
        if resp.status_code >= 400:
            logger.error("Order cancellation failed for %s: %s", order_id, resp.text)
        resp.raise_for_status()

    def wait_for_fill(
        self,
        order_id: Optional[str],
        timeout_s: float = 3.0,
        poll_interval_s: float = 0.4,
        requested_qty: Optional[int] = None,
    ) -> FillResult:
        """Poll until terminal, partial-vs-full, or timeout.

        Default timeout is short on purpose: the trading loop is single-threaded
        and exits cannot run while this blocks. Never treats missing filled_qty
        as a full fill.
        """
        if not order_id:
            return FillResult(status="NOT_FOUND", filled_qty=0, avg_price=None, raw=None)

        deadline = time.monotonic() + timeout_s
        last_seen: Optional[dict] = None
        while time.monotonic() < deadline:
            order = self.get_order_status(order_id)
            if order is not None:
                last_seen = order
                status = str(order.get("status", "")).upper()
                logger.info("wait_for_fill order=%s raw_status=%s", order_id, status)
                filled = _filled_qty(order)
                avg = _avg_price(order)
                if status in FILLED_STATUSES or status in PARTIAL_STATUSES:
                    if filled is None or filled <= 0:
                        # Broker said SUCCESS/COMPLETE but gave us no fill size — do not guess.
                        return FillResult(status="TIMEOUT", filled_qty=0, avg_price=avg, raw=order)
                    if status in PARTIAL_STATUSES or (
                        requested_qty is not None and filled < requested_qty
                    ):
                        return FillResult(status="PARTIAL", filled_qty=filled, avg_price=avg, raw=order)
                    return FillResult(status="FILLED", filled_qty=filled, avg_price=avg, raw=order)
                if status in REJECTED_STATUSES:
                    return FillResult(status="REJECTED", filled_qty=0, avg_price=None, raw=order)
                if status in CANCELLED_STATUSES:
                    if filled and filled > 0:
                        return FillResult(status="PARTIAL", filled_qty=filled, avg_price=avg, raw=order)
                    return FillResult(status="CANCELLED", filled_qty=0, avg_price=None, raw=order)
            time.sleep(poll_interval_s)
        out_status = "TIMEOUT" if last_seen is not None else "NOT_FOUND"
        logger.warning(
            "wait_for_fill: order %s did not reach a terminal state within %.1fs (status=%s raw=%s)",
            order_id, timeout_s, out_status, (last_seen or {}).get("status"),
        )
        return FillResult(status=out_status, filled_qty=_filled_qty(last_seen or {}) or 0, avg_price=_avg_price(last_seen or {}), raw=last_seen)
