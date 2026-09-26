"""WebSocket market-data feed.

CONFIRM BEFORE ENABLING: the connection URL, subscribe-message shape, and
tick-message shape below are a best-effort placeholder, modeled on the
REST quotes response shape documented at api-docs.indstocks.com --
INDstocks' actual WebSocket protocol (api-docs.indstocks.com/Websockets/)
was not confirmed while building this. Gated behind WEBSOCKET_ENABLED
(default false) for exactly that reason: main.py falls back to the
already-confirmed REST /market/quotes/ltp poll whenever this feed is
disabled or has no fresh tick for a symbol yet, so nothing depends on
this being correct to keep trading.

Runs its own reconnect loop with exponential backoff -- a dropped
connection does not crash the process, it just means LTP falls back to
REST until reconnected.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import websockets
import websockets.sync.client

logger = logging.getLogger(__name__)


class LiveTickCache:
    """Thread-safe last-tick-per-scrip cache, with staleness awareness."""

    def __init__(self, max_staleness_s: float = 5.0):
        self.max_staleness_s = max_staleness_s
        self._lock = threading.Lock()
        self._ticks: dict[str, tuple[float, float]] = {}  # scrip_code -> (ltp, received_at)

    def update(self, scrip_code: str, ltp: float) -> None:
        with self._lock:
            self._ticks[scrip_code] = (ltp, time.time())

    def get_fresh(self, scrip_code: str) -> Optional[float]:
        """Returns the cached LTP only if it's fresher than max_staleness_s, else None."""
        with self._lock:
            entry = self._ticks.get(scrip_code)
        if entry is None:
            return None
        ltp, received_at = entry
        if (time.time() - received_at) > self.max_staleness_s:
            return None
        return ltp


class INDstocksWebSocketFeed:
    def __init__(
        self,
        url: str,
        auth_headers_fn,
        scrip_codes: list[str],
        cache: LiveTickCache,
        max_backoff_s: float = 30.0,
    ):
        self.url = url
        self.auth_headers_fn = auth_headers_fn
        self.scrip_codes = scrip_codes
        self.cache = cache
        self.max_backoff_s = max_backoff_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run_forever(self) -> None:
        backoff_s = 1.0
        while not self._stop.is_set():
            try:
                self._connect_and_listen()
                backoff_s = 1.0  # reset after a clean session
            except Exception:
                logger.exception(
                    "WebSocket feed disconnected, reconnecting in %.1fs (LTP falls back to REST meanwhile)",
                    backoff_s,
                )
                self._stop.wait(backoff_s)
                backoff_s = min(backoff_s * 2, self.max_backoff_s)

    def _connect_and_listen(self) -> None:
        headers = self.auth_headers_fn()
        with websockets.sync.client.connect(
            self.url,
            additional_headers={"Authorization": headers.get("Authorization", "")},
        ) as ws:
            # TODO: confirm the real subscribe message shape against
            # api-docs.indstocks.com/Websockets/ before relying on this.
            # Confirmed api-docs.indstocks.com/Websockets/: NSE:2885 not NSE_2885
            instruments = [c.replace("_", ":", 1) if "_" in c else c for c in self.scrip_codes]
            ws.send(json.dumps({"action": "subscribe", "mode": "ltp", "instruments": instruments}))
            logger.info("WebSocket feed connected, subscribed to %d symbols", len(self.scrip_codes))

            while not self._stop.is_set():
                raw = ws.recv(timeout=10)
                self._handle_message(raw)

    def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            # TODO: confirm the real tick message shape -- assumed here to
            # mirror the REST /market/quotes/full response's field names.
            scrip_code = msg.get("scrip_code") or msg.get("instrument") or msg.get("token")
            ltp = msg.get("live_price") or msg.get("ltp") or msg.get("LTP")
            if scrip_code and ltp is not None:
                self.cache.update(str(scrip_code).replace(":", "_"), float(ltp))
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Could not parse WebSocket tick message: %r", raw[:200])
