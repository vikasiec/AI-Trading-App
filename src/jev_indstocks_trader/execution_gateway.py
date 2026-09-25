"""INDstocks order placement and order-book queries.

Order payload fields (txn_type, security_id, algo_id, etc.) match
INDstocks' published /order endpoint -- see docs/ARCHITECTURE.md section 5
for the corrected request shape and the mistakes it replaces.
"""
from __future__ import annotations

import logging

import requests

from .config import INDstocksConfig
from .risk_governor import round_to_tick

logger = logging.getLogger(__name__)


class ExecutionGateway:
    def __init__(self, cfg: INDstocksConfig, auth_headers_fn, tick_size: float = 0.05):
        self.cfg = cfg
        self.auth_headers_fn = auth_headers_fn
        self.tick_size = tick_size

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

    def get_ltp(self, scrip_code: str) -> float:
        """scrip_code is the exchange-prefixed code, e.g. 'NSE_2885' -- from Instruments Master."""
        resp = requests.get(
            f"{self.cfg.base_url}/market/quotes/ltp",
            headers=self.auth_headers_fn(),
            params={"scrip-codes": scrip_code},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["data"][scrip_code]["live_price"]
