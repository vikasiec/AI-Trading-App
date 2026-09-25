"""Central configuration, loaded from environment variables.

Nothing in this file should ever hold a real secret. Copy .env.example to
.env, fill it in, and load it with `python-dotenv` (already in requirements.txt)
before importing this module -- see main.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _optional(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class INDstocksConfig:
    client_id: str = field(default_factory=lambda: _require("INDSTOCKS_CLIENT_ID"))
    mpin: str = field(default_factory=lambda: _require("INDSTOCKS_MPIN"))
    totp_secret: str = field(default_factory=lambda: _require("INDSTOCKS_TOTP_SECRET"))
    base_url: str = field(default_factory=lambda: _optional("INDSTOCKS_BASE_URL", "https://api.indstocks.com"))
    token_cache_path: Path = field(
        default_factory=lambda: Path(_optional("INDSTOCKS_TOKEN_CACHE", str(Path.home() / ".indstocks" / "session_token.json")))
    )


@dataclass(frozen=True)
class JevConfig:
    api_key: str = field(default_factory=lambda: _require("JEV_API_KEY"))
    base_url: str = field(default_factory=lambda: _optional("JEV_BASE_URL", "https://api.typesafe.ai/v1/systemone"))
    model: str = field(default_factory=lambda: _optional("JEV_MODEL", "jev-latest"))
    conviction_threshold: float = field(default_factory=lambda: float(_optional("JEV_CONVICTION_THRESHOLD", "0.80")))
    confidence_threshold: float = field(default_factory=lambda: float(_optional("JEV_CONFIDENCE_THRESHOLD", "0.60")))
    request_timeout_s: float = field(default_factory=lambda: float(_optional("JEV_TIMEOUT_S", "2.0")))


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = field(default_factory=lambda: _require("TELEGRAM_BOT_TOKEN"))
    owner_chat_id: int = field(default_factory=lambda: int(_require("TELEGRAM_OWNER_CHAT_ID")))


@dataclass(frozen=True)
class RiskConfig:
    daily_loss_limit_pct: float = field(default_factory=lambda: float(_optional("DAILY_LOSS_LIMIT_PCT", "0.02")))
    max_position_capital_inr: float = field(default_factory=lambda: float(_optional("MAX_POSITION_CAPITAL_INR", "50000")))
    max_position_pct_equity: float = field(default_factory=lambda: float(_optional("MAX_POSITION_PCT_EQUITY", "0.02")))
    max_slippage_pct: float = field(default_factory=lambda: float(_optional("MAX_SLIPPAGE_PCT", "0.0015")))
    tick_size_inr: float = field(default_factory=lambda: float(_optional("DEFAULT_TICK_SIZE_INR", "0.05")))
    paper_trading: bool = field(default_factory=lambda: _optional("PAPER_TRADING", "true").lower() == "true")
    # Duplicated from JevConfig on purpose: the risk governor should enforce
    # its own floor even if the Jev client's threshold check is bypassed.
    min_conviction: float = field(default_factory=lambda: float(_optional("JEV_CONVICTION_THRESHOLD", "0.80")))
    min_confidence: float = field(default_factory=lambda: float(_optional("JEV_CONFIDENCE_THRESHOLD", "0.60")))
    # Exit rules -- every position opened by this bot is closed by one of these,
    # never held indefinitely.
    stop_loss_pct: float = field(default_factory=lambda: float(_optional("STOP_LOSS_PCT", "0.01")))
    target_pct: float = field(default_factory=lambda: float(_optional("TARGET_PCT", "0.02")))
    max_hold_minutes: float = field(default_factory=lambda: float(_optional("MAX_HOLD_MINUTES", "375")))  # ~one NSE trading day


@dataclass(frozen=True)
class AppConfig:
    indstocks: INDstocksConfig = field(default_factory=INDstocksConfig)
    jev: JevConfig = field(default_factory=JevConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    audit_log_path: Path = field(default_factory=lambda: Path(_optional("AUDIT_LOG_PATH", "./audit_trail.jsonl")))
    positions_store_path: Path = field(
        default_factory=lambda: Path(_optional("POSITIONS_STORE_PATH", str(Path.home() / ".indstocks" / "open_positions.json")))
    )
    watchlist_symbols: str = field(default_factory=lambda: _optional("WATCHLIST_SYMBOLS", ""))
    watchlist_file: str = field(default_factory=lambda: _optional("WATCHLIST_FILE", ""))
    news_rss_feeds: str = field(default_factory=lambda: _optional("NEWS_RSS_FEEDS", ""))
    news_cache_ttl_s: float = field(default_factory=lambda: float(_optional("NEWS_CACHE_TTL_S", "300")))
    websocket_enabled: bool = field(default_factory=lambda: _optional("WEBSOCKET_ENABLED", "false").lower() == "true")
    websocket_url: str = field(default_factory=lambda: _optional("INDSTOCKS_WEBSOCKET_URL", "wss://api.indstocks.com/market/stream"))


def load_config() -> AppConfig:
    return AppConfig()
