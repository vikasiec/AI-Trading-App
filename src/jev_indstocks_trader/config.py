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
    owner_user_id: int = field(default_factory=lambda: int(_optional("TELEGRAM_OWNER_USER_ID", _optional("TELEGRAM_OWNER_CHAT_ID", "0"))))


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
    brokerage_per_order_inr: float = field(default_factory=lambda: float(_optional("BROKERAGE_PER_ORDER_INR", "5.0")))
    # Portfolio-level risk -- caps across ALL open positions at once, not just one order.
    max_concurrent_positions: int = field(default_factory=lambda: int(_optional("MAX_CONCURRENT_POSITIONS", "3")))
    max_deployed_capital_pct: float = field(default_factory=lambda: float(_optional("MAX_DEPLOYED_CAPITAL_PCT", "0.10")))
    max_sector_capital_pct: float = field(default_factory=lambda: float(_optional("MAX_SECTOR_CAPITAL_PCT", "0.06")))
    # GTT -- exchange-side backup stop-loss/target, on top of the client-side
    # exits.py logic. Off by default: see gtt_orders.py's "confirm before
    # enabling" note.
    gtt_enabled: bool = field(default_factory=lambda: _optional("GTT_ENABLED", "false").lower() == "true")


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
    websocket_url: str = field(default_factory=lambda: _optional(
        "INDSTOCKS_WEBSOCKET_URL", "wss://ws-prices.indstocks.com/api/v1/ws/prices"
    ))
    respect_session: bool = field(default_factory=lambda: _optional("RESPECT_SESSION", "true").lower() == "true")
    log_json: bool = field(default_factory=lambda: _optional("LOG_JSON", "false").lower() == "true")
    health_host: str = field(default_factory=lambda: _optional("HEALTH_HOST", "127.0.0.1"))
    health_port: int = field(default_factory=lambda: int(_optional("HEALTH_PORT", "8080")))
    health_token: str = field(default_factory=lambda: _optional("HEALTH_TOKEN", ""))


def validate_config(cfg: AppConfig) -> list[str]:
    """Return human-readable problems. Empty list means safe to start the loop."""
    problems: list[str] = []
    r = cfg.risk
    if not (0 < r.daily_loss_limit_pct < 1):
        problems.append(f"DAILY_LOSS_LIMIT_PCT={r.daily_loss_limit_pct} must be in (0, 1)")
    if not (0 < r.max_position_pct_equity <= 1):
        problems.append(f"MAX_POSITION_PCT_EQUITY={r.max_position_pct_equity} must be in (0, 1]")
    if not (0 < r.max_deployed_capital_pct <= 1):
        problems.append(f"MAX_DEPLOYED_CAPITAL_PCT={r.max_deployed_capital_pct} must be in (0, 1]")
    if not (0 < r.max_sector_capital_pct <= 1):
        problems.append(f"MAX_SECTOR_CAPITAL_PCT={r.max_sector_capital_pct} must be in (0, 1]")
    if r.max_sector_capital_pct > r.max_deployed_capital_pct:
        problems.append("MAX_SECTOR_CAPITAL_PCT cannot exceed MAX_DEPLOYED_CAPITAL_PCT")
    if r.min_conviction < 0 or r.min_conviction > 1:
        problems.append(f"JEV_CONVICTION_THRESHOLD={r.min_conviction} must be in [0, 1]")
    if r.min_confidence < 0 or r.min_confidence > 1:
        problems.append(f"JEV_CONFIDENCE_THRESHOLD={r.min_confidence} must be in [0, 1]")
    if r.stop_loss_pct <= 0 or r.target_pct <= 0:
        problems.append("STOP_LOSS_PCT and TARGET_PCT must be > 0")
    if r.max_concurrent_positions < 1:
        problems.append("MAX_CONCURRENT_POSITIONS must be >= 1")
    if r.max_position_capital_inr <= 0:
        problems.append("MAX_POSITION_CAPITAL_INR must be > 0")
    if cfg.websocket_enabled:
        problems.append("WEBSOCKET_ENABLED=true but the WS protocol is still a placeholder")
    if r.gtt_enabled:
        problems.append("GTT_ENABLED=true but the GTT endpoint is still a placeholder")
    if not r.paper_trading:
        problems.append("PAPER_TRADING=false — live capital path; confirm the pre-flight checklist")
    return problems


def load_config(*, strict: bool = False) -> AppConfig:
    cfg = AppConfig()
    if not strict:
        return cfg
    problems = validate_config(cfg)
    if problems:
        raise RuntimeError("Invalid configuration:\n- " + "\n- ".join(problems))
    return cfg
