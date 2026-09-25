"""GTT (Good-Till-Triggered) / OCO bracket orders.

CONFIRM BEFORE ENABLING: api-docs.indstocks.com/smart_orders/ wasn't
reachable while building this, so the endpoint path and payload shape
below are a best-effort placeholder, following the same pattern as
market_data.py's WebSocket feed -- gated off by default, with the
already-working client-side exit logic in exits.py as the fallback that
does NOT depend on this being correct.

Why this exists alongside exits.py at all: a client-side stop-loss only
protects a position while this process is running. If the process
crashes, loses network, or the host goes down, a GTT order sitting on
the exchange itself keeps protecting the position -- the exchange, not
this bot, is watching the price. exits.py remains the PRIMARY exit
mechanism (it's tested against real, confirmed order-placement code);
GTT is a redundant safety net for the gap between "the bot has crashed"
and "someone notices."

Every GTT-protected Position has its GTT id stored on it (positions.py's
`gtt_id` field). When exits.py closes a position through its normal
client-side logic, it MUST cancel the matching GTT first -- otherwise
the exchange-side leg can fire later against a position that's already
flat, since the exchange has no idea the bot exited manually.
"""
from __future__ import annotations

import logging

import requests

from .config import INDstocksConfig

logger = logging.getLogger(__name__)


def place_gtt_oco(
    cfg: INDstocksConfig,
    auth_headers_fn,
    security_id: str,
    exchange: str,
    segment: str,
    qty: int,
    stop_loss_trigger: float,
    target_trigger: float,
    product: str = "INTRADAY",
) -> str:
    """Places an exchange-side OCO GTT: SELL qty if price hits
    stop_loss_trigger OR target_trigger, whichever comes first, cancelling
    the other leg automatically. Returns the GTT order id.

    CONFIRM the endpoint and payload against INDstocks' real Smart Orders
    docs before relying on this -- see module docstring.
    """
    payload = {
        "security_id": security_id,
        "exchange": exchange,
        "segment": segment,
        "qty": qty,
        "product": product,
        "type": "GTT_OCO",
        "legs": [
            {"type": "STOP_LOSS", "txn_type": "SELL", "trigger_price": stop_loss_trigger},
            {"type": "TARGET", "txn_type": "SELL", "trigger_price": target_trigger},
        ],
    }
    resp = requests.post(f"{cfg.base_url}/gtt/place", headers=auth_headers_fn(), json=payload, timeout=10)
    if resp.status_code != 200:
        logger.error("GTT placement failed: %s", resp.text)
    resp.raise_for_status()
    body = resp.json()
    return body["data"]["gtt_id"]


def cancel_gtt(cfg: INDstocksConfig, auth_headers_fn, gtt_id: str) -> None:
    """Cancels a GTT order. Called by exits.py whenever a position closes
    through the normal client-side path, so the exchange-side leg doesn't
    fire later against a position that's already flat. Failures are
    logged but not raised -- a failed cancel here shouldn't block the
    exit that already happened; it's a cleanup step, not the exit itself.
    """
    try:
        resp = requests.delete(f"{cfg.base_url}/gtt/{gtt_id}", headers=auth_headers_fn(), timeout=10)
        resp.raise_for_status()
    except Exception:
        logger.exception(
            "Could not cancel GTT %s after a client-side exit -- it may still be live on the "
            "exchange. Check manually; this is exactly the kind of drift reconciliation.py's "
            "successor should eventually catch for GTT orders too.",
            gtt_id,
        )
