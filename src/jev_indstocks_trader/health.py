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

    def log_message(self, fmt, *args):
        logger.debug("health %s", fmt % args)

    def do_GET(self):
        if self.path.split("?")[0] not in ("/healthz", "/health", "/"):
            self.send_response(404)
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
) -> ThreadingHTTPServer:
    handler = type("HealthHandler", (_Handler,), {"payload_fn": staticmethod(payload_fn)})
    httpd = ThreadingHTTPServer((host, port), handler)
    t = threading.Thread(target=httpd.serve_forever, name="healthz", daemon=True)
    t.start()
    logger.info("healthz listening on http://%s:%d/healthz", host, port)
    return httpd
