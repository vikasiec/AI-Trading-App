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
