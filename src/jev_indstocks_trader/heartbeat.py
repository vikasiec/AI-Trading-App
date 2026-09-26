"""Broker connectivity heartbeat.

GET /user/profile on an interval. Consecutive failures alert Telegram.
After HEARTBEAT_FAILS_BEFORE_FLATTEN failures in live mode the kill
switch runs — leaving positions unmonitored is worse than flattening.
Paper mode alerts only.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import requests

from .config import INDstocksConfig

logger = logging.getLogger(__name__)


class BrokerHeartbeat:
    def __init__(
        self,
        cfg: INDstocksConfig,
        auth_headers_fn,
        *,
        interval_s: float = 30.0,
        fails_before_flatten: int = 5,
        flatten_on_outage: bool = False,
        flatten_fn: Optional[Callable[[], None]] = None,
        notifier=None,
    ):
        self.cfg = cfg
        self.auth_headers_fn = auth_headers_fn
        self.interval_s = interval_s
        self.fails_before_flatten = max(1, fails_before_flatten)
        self.flatten_on_outage = flatten_on_outage
        self.flatten_fn = flatten_fn
        self.notifier = notifier
        self._last_run = 0.0
        self.consecutive_failures = 0
        self.last_ok = False
        self._flattened_this_outage = False

    def due(self) -> bool:
        return (time.time() - self._last_run) >= self.interval_s

    def ping(self) -> bool:
        self._last_run = time.time()
        try:
            resp = requests.get(
                f"{self.cfg.base_url}/user/profile",
                headers=self.auth_headers_fn(),
                timeout=8,
            )
            resp.raise_for_status()
        except Exception:
            logger.exception("Heartbeat: /user/profile failed")
            self.consecutive_failures += 1
            self.last_ok = False
            if self.notifier is not None:
                self.notifier.send_critical_alert(
                    f"Broker heartbeat failed ({self.consecutive_failures}/"
                    f"{self.fails_before_flatten})"
                )
            if (
                self.flatten_on_outage
                and self.flatten_fn is not None
                and self.consecutive_failures >= self.fails_before_flatten
                and not self._flattened_this_outage
            ):
                logger.critical("Heartbeat grace exceeded — flattening")
                try:
                    self.flatten_fn()
                except Exception:
                    logger.exception("Heartbeat flatten failed")
                self._flattened_this_outage = True
            return False

        if self.consecutive_failures:
            logger.info("Heartbeat recovered after %d failures", self.consecutive_failures)
        self.consecutive_failures = 0
        self.last_ok = True
        self._flattened_this_outage = False
        return True
