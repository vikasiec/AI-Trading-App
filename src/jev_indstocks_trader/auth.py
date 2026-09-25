"""INDstocks authentication.

IMPORTANT: only one TOTP-issued token is live at a time. Generating a new
one invalidates the previous one. This module is split into two roles:

- INDstocksAuth.refresh_session(): call this from exactly ONE process
  (e.g. a daily cron job). See scripts/refresh_token.py.
- get_cached_token() / auth_headers(): call these from every OTHER
  process (the trading loop, the Telegram bot, ad-hoc scripts). They
  never hit the network -- they just read the token the owner process
  cached to disk.

Running refresh_session() from more than one place will cause the
processes to invalidate each other's tokens. Don't do it.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pyotp
import requests

from .config import INDstocksConfig

logger = logging.getLogger(__name__)


class INDstocksAuth:
    """Owns TOTP-based token generation. Run from a single process only."""

    def __init__(self, cfg: INDstocksConfig):
        self.cfg = cfg
        self.cfg.token_cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def refresh_session(self) -> str:
        totp_code = pyotp.TOTP(self.cfg.totp_secret).now()
        resp = requests.post(
            f"{self.cfg.base_url}/generate/token",
            headers={"x-api-key": self.cfg.client_id, "Content-Type": "application/json"},
            json={"mpin": self.cfg.mpin, "totp": totp_code},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        token = body["data"]["token"]
        self._write_cache(token)
        logger.info("INDstocks token refreshed, cached at %s", self.cfg.token_cache_path)
        return token

    def _write_cache(self, token: str) -> None:
        tmp_path = self.cfg.token_cache_path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump({"token": token, "issued_at": time.time()}, f)
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self.cfg.token_cache_path)  # atomic on POSIX


def get_cached_token(cfg: INDstocksConfig) -> str:
    """Read-only accessor for every process that is NOT the token owner."""
    with open(cfg.token_cache_path) as f:
        data = json.load(f)
    age_hours = (time.time() - data["issued_at"]) / 3600
    if age_hours > 23.5:
        logger.warning(
            "Cached INDstocks token is %.1fh old (24h expiry) -- confirm the "
            "refresh cron is running.",
            age_hours,
        )
    return data["token"]


def auth_headers(cfg: INDstocksConfig) -> dict:
    return {"Authorization": get_cached_token(cfg), "Content-Type": "application/json"}
