"""Process health endpoint (P2). Stdlib only — no extra dependency.

GET /healthz returns JSON: process up, paper flag, open position count,
kill-switch not a substitute for this. Bind 127.0.0.1 by default.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def build_payload(
    *,
    paper_trading: bool,
    open_positions: int,
    watchlist_n: int,
    started_at: float,
) -> dict:
    return {
        "ok": True,
        "paper_trading": paper_trading,
        "open_positions": open_positions,
        "watchlist": watchlist_n,
        "uptime_s": round(time.time() - started_at, 1),
    }


class _Handler(BaseHTTPRequestHandler):
    payload_fn: Callable[[], dict] = staticmethod(lambda: {"ok": True})
    required_token: str = ""
    bind_host: str = "127.0.0.1"

    def log_message(self, fmt, *args):
        logger.debug("health %s", fmt % args)

    def _authorized(self) -> bool:
        loopback = self.bind_host in ("127.0.0.1", "localhost", "::1")
        if loopback and not self.required_token:
            return True
        token = self.headers.get("X-Health-Token", "")
        return bool(self.required_token) and token == self.required_token

    def do_GET(self):
        if self.path.split("?")[0] not in ("/healthz", "/health", "/"):
            self.send_response(404)
            self.end_headers()
            return
        if not self._authorized():
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps(self.payload_fn()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_health_server(
    payload_fn: Callable[[], dict],
    host: str = "127.0.0.1",
    port: int = 8080,
    token: str = "",
) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1") and not token:
        raise RuntimeError("HEALTH_TOKEN is required when HEALTH_HOST is not loopback")
    handler = type(
        "HealthHandler",
        (_Handler,),
        {"payload_fn": staticmethod(payload_fn), "required_token": token, "bind_host": host},
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    t = threading.Thread(target=httpd.serve_forever, name="healthz", daemon=True)
    t.start()
    logger.info("healthz listening on http://%s:%d/healthz", host, port)
    return httpd
