"""Telegram alert dispatcher, authenticated remote kill switch, and monitoring commands.

Owner must send `/halt CONFIRM` (or `/flatten CONFIRM`) from their user id
in the owner chat. Forwards and other users are ignored.

Monitoring commands (owner-only):
  /status    — running state, positions, P&L, Jev calls
  /watchlist — current watchlist symbols
  /add SYM   — add a stock to the watchlist
  /remove SYM — remove a stock from the watchlist
  /audit     — today's signal decisions
  /logs      — last N audit trail entries
  /health    — heartbeat, connectivity, uptime
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import telebot

from .config import TelegramConfig

logger = logging.getLogger(__name__)


class HaltState:
    """Set from the Telegram thread, read from the trading loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._halted = False

    def trip(self) -> None:
        with self._lock:
            self._halted = True

    def is_halted(self) -> bool:
        with self._lock:
            return self._halted


def _is_forwarded(message) -> bool:
    return bool(
        getattr(message, "forward_from", None)
        or getattr(message, "forward_from_chat", None)
        or getattr(message, "forward_origin", None)
        or getattr(message, "forward_date", None)
    )


def authorized_kill_command(message, owner_chat_id: int, owner_user_id: int) -> tuple[bool, str]:
    if _is_forwarded(message):
        return False, "forwarded"
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if chat_id != owner_chat_id:
        return False, "bad_chat"
    from_user = getattr(message, "from_user", None)
    user_id = getattr(from_user, "id", None)
    if user_id != owner_user_id:
        return False, "bad_user"
    text = (getattr(message, "text", None) or "").strip()
    parts = text.split()
    if len(parts) < 2 or parts[-1].upper() != "CONFIRM":
        return False, "need_confirm"
    return True, "ok"


def _authorized_read(message, owner_chat_id: int) -> bool:
    if _is_forwarded(message):
        return False
    return getattr(getattr(message, "chat", None), "id", None) == owner_chat_id


def _read_today_audit(audit_path: str) -> list[dict]:
    """Read today's audit entries from the JSONL file."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    entries: list[dict] = []
    path = Path(audit_path)
    if not path.exists():
        return entries
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("ts", "")
                if ts.startswith(today):
                    entries.append(row)
    except OSError:
        pass
    return entries


def _read_last_n_audit(audit_path: str, n: int = 10) -> list[dict]:
    """Read the last N entries from the audit JSONL file."""
    path = Path(audit_path)
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries[-n:]


def _atomic_write_watchlist(path: Path, symbols: list[str]) -> None:
    """Write watchlist JSON atomically (tmp + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(symbols, f, indent=2)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class TelegramAlertNotifier:
    def __init__(self, cfg: TelegramConfig, kill_switch_callback: Callable[[], None]):
        self.cfg = cfg
        self.bot = telebot.TeleBot(cfg.bot_token)
        self.kill_switch_callback = kill_switch_callback
        self.halt_state = HaltState()
        self._poll_thread: Optional[threading.Thread] = None
        self.poll_alive = False
        self._state_fn: Optional[Callable[[], dict]] = None

        @self.bot.message_handler(commands=["halt", "flatten"])
        def handle_emergency(message):
            ok, why = authorized_kill_command(
                message, self.cfg.owner_chat_id, self.cfg.owner_user_id
            )
            if not ok:
                logger.warning(
                    "Ignored /halt (%s) chat=%s",
                    why, getattr(getattr(message, "chat", None), "id", None),
                )
                if why == "need_confirm" and getattr(getattr(message, "chat", None), "id", None) == self.cfg.owner_chat_id:
                    try:
                        self.bot.reply_to(message, "Send /halt CONFIRM to flatten.")
                    except Exception:
                        pass
                return
            logger.critical("Authorized kill-switch command received from owner")
            self.halt_state.trip()
            try:
                result = self.kill_switch_callback()
                detail = result if isinstance(result, str) and result else "New entries are blocked."
            except Exception:
                logger.exception("Kill switch callback failed")
                detail = "Halt is on, but flatten raised. Check positions before doing anything else."
            try:
                self.bot.reply_to(message, f"EMERGENCY: {detail}")
            except Exception:
                logger.exception("Could not reply to /halt")

        @self.bot.message_handler(commands=["status"])
        def handle_status(message):
            if not _authorized_read(message, self.cfg.owner_chat_id):
                return
            try:
                self.bot.reply_to(message, self._format_status())
            except Exception:
                logger.exception("Could not reply to /status")

        @self.bot.message_handler(commands=["watchlist"])
        def handle_watchlist(message):
            if not _authorized_read(message, self.cfg.owner_chat_id):
                return
            try:
                self.bot.reply_to(message, self._format_watchlist())
            except Exception:
                logger.exception("Could not reply to /watchlist")

        @self.bot.message_handler(commands=["add"])
        def handle_add(message):
            if not _authorized_read(message, self.cfg.owner_chat_id):
                return
            try:
                self.bot.reply_to(message, self._handle_add_symbol(message))
            except Exception:
                logger.exception("Could not reply to /add")

        @self.bot.message_handler(commands=["remove"])
        def handle_remove(message):
            if not _authorized_read(message, self.cfg.owner_chat_id):
                return
            try:
                self.bot.reply_to(message, self._handle_remove_symbol(message))
            except Exception:
                logger.exception("Could not reply to /remove")

        @self.bot.message_handler(commands=["audit"])
        def handle_audit(message):
            if not _authorized_read(message, self.cfg.owner_chat_id):
                return
            try:
                self.bot.reply_to(message, self._format_audit())
            except Exception:
                logger.exception("Could not reply to /audit")

        @self.bot.message_handler(commands=["logs"])
        def handle_logs(message):
            if not _authorized_read(message, self.cfg.owner_chat_id):
                return
            try:
                self.bot.reply_to(message, self._format_logs())
            except Exception:
                logger.exception("Could not reply to /logs")

        @self.bot.message_handler(commands=["health"])
        def handle_health(message):
            if not _authorized_read(message, self.cfg.owner_chat_id):
                return
            try:
                self.bot.reply_to(message, self._format_health())
            except Exception:
                logger.exception("Could not reply to /health")

        @self.bot.message_handler(commands=["help"])
        def handle_help(message):
            if not _authorized_read(message, self.cfg.owner_chat_id):
                return
            text = (
                "Commands:\n"
                "/status — positions, P&L, Jev calls\n"
                "/watchlist — current symbols\n"
                "/add SYMBOL — add stock to watchlist\n"
                "/remove SYMBOL — remove from watchlist\n"
                "/audit — today's signals\n"
                "/logs — last 10 audit entries\n"
                "/health — heartbeat & uptime\n"
                "/halt CONFIRM — emergency flatten\n"
            )
            try:
                self.bot.reply_to(message, text)
            except Exception:
                logger.exception("Could not reply to /help")

    def set_state_provider(self, fn: Callable[[], dict]) -> None:
        self._state_fn = fn

    def _get_state(self) -> dict:
        if self._state_fn is None:
            return {}
        try:
            return self._state_fn()
        except Exception:
            logger.exception("State provider failed")
            return {}

    def _format_status(self) -> str:
        state = self._get_state()
        if not state:
            return "Bot is alive. State provider not wired yet."

        mode = "PAPER" if state.get("paper_trading") else "LIVE"
        halted = state.get("halted", False)
        positions = state.get("positions", [])
        jev_today = state.get("jev_calls_today", 0)
        jev_cap = state.get("jev_cap", 500)
        started = state.get("started_at", 0)
        uptime_s = int(time.time() - started) if started else 0
        uptime_h = uptime_s // 3600
        uptime_m = (uptime_s % 3600) // 60

        lines = [
            f"{'HALTED' if halted else 'RUNNING'} ({mode})",
            f"Uptime: {uptime_h}h {uptime_m}m",
            f"Jev calls: {jev_today}/{jev_cap}",
            f"Open positions: {len(positions)}",
        ]

        total_pnl = 0.0
        for pos in positions:
            sym = getattr(pos, "symbol", "") or getattr(pos, "security_id", "?")
            entry = getattr(pos, "entry_price", 0)
            sl = getattr(pos, "stop_loss_price", 0)
            tgt = getattr(pos, "target_price", 0)
            qty = getattr(pos, "qty", 0)
            pnl = getattr(pos, "cumulative_pnl", 0.0)
            total_pnl += pnl
            lines.append(
                f"  {sym}: {qty}@{entry:.2f} SL:{sl:.2f} TGT:{tgt:.2f}"
            )

        if positions:
            lines.append(f"Cumulative P&L: {total_pnl:+.2f}")

        return "\n".join(lines)

    def _format_watchlist(self) -> str:
        state = self._get_state()
        wl = state.get("watchlist", [])
        if not wl:
            return "Watchlist: (empty or not available)"
        return f"Watchlist ({len(wl)}):\n" + ", ".join(wl)

    def _format_audit(self) -> str:
        state = self._get_state()
        audit_path = state.get("audit_path", "")
        if not audit_path:
            return "Audit trail path not configured."

        entries = _read_today_audit(audit_path)
        if not entries:
            return "No audit entries today."

        buys = [e for e in entries if e.get("action") == "BUY"]
        skips = [e for e in entries if e.get("action") == "SKIP"]
        outcomes = [e for e in entries if e.get("type") == "outcome_update"]

        lines = [f"Today: {len(buys)} BUY, {len(skips)} SKIP, {len(outcomes)} exits"]

        for e in buys[-5:]:
            sym = e.get("symbol") or e.get("security_id", "?")
            conv = e.get("jev_conviction", 0)
            conf = e.get("jev_confidence", 0)
            ts = e.get("ts", "")
            t = ts[11:16] if len(ts) > 16 else ts
            lines.append(f"  BUY {sym} conv={conv:.2f} conf={conf:.2f} @{t}")

        for e in outcomes[-5:]:
            did = e.get("decision_id", "?")
            sym = did.split("_")[0] if "_" in did else did
            pnl = e.get("net_pnl")
            reason = e.get("exit_reason", "")
            pnl_str = f"{pnl:+.2f}" if pnl is not None else "?"
            lines.append(f"  EXIT {sym} pnl={pnl_str} ({reason})")

        skip_reasons: dict[str, int] = {}
        for e in skips:
            r = e.get("skip_reason") or e.get("reason") or "unknown"
            skip_reasons[r] = skip_reasons.get(r, 0) + 1
        if skip_reasons:
            top = sorted(skip_reasons.items(), key=lambda x: x[1], reverse=True)[:5]
            lines.append("Skip reasons: " + ", ".join(f"{r}({n})" for r, n in top))

        return "\n".join(lines)

    def _format_logs(self) -> str:
        state = self._get_state()
        audit_path = state.get("audit_path", "")
        if not audit_path:
            return "Audit trail path not configured."

        entries = _read_last_n_audit(audit_path, n=10)
        if not entries:
            return "No audit entries found."

        lines = [f"Last {len(entries)} audit entries:"]
        for e in entries:
            ts = e.get("ts", "")
            t = ts[11:16] if len(ts) > 16 else ts
            action = e.get("action") or e.get("type", "?")
            sym = e.get("symbol") or e.get("security_id") or ""
            if e.get("type") == "outcome_update":
                did = e.get("decision_id", "")
                sym = did.split("_")[0] if "_" in did else did
                pnl = e.get("net_pnl")
                reason = e.get("exit_reason", "")
                pnl_str = f"pnl={pnl:+.2f}" if pnl is not None else ""
                lines.append(f"  {t} EXIT {sym} {pnl_str} ({reason})")
            elif action == "SKIP":
                reason = e.get("skip_reason") or e.get("reason") or ""
                lines.append(f"  {t} SKIP {sym} ({reason})")
            elif action == "BUY":
                conv = e.get("jev_conviction", 0)
                lines.append(f"  {t} BUY {sym} conv={conv:.2f}")
            else:
                lines.append(f"  {t} {action} {sym}")

        return "\n".join(lines)

    def _format_health(self) -> str:
        state = self._get_state()
        if not state:
            return "Bot is alive. State provider not wired yet."

        mode = "PAPER" if state.get("paper_trading") else "LIVE"
        halted = state.get("halted", False)
        hb_ok = state.get("heartbeat_ok", None)
        hb_fails = state.get("heartbeat_consecutive_failures", 0)
        tg_alive = state.get("telegram_alive", False)
        positions = state.get("positions", [])
        started = state.get("started_at", 0)
        uptime_s = int(time.time() - started) if started else 0
        uptime_h = uptime_s // 3600
        uptime_m = (uptime_s % 3600) // 60

        lines = [
            f"Mode: {mode}",
            f"Halt flag: {'YES' if halted else 'no'}",
            f"Uptime: {uptime_h}h {uptime_m}m",
            f"Heartbeat: {'OK' if hb_ok else 'FAIL'} (consecutive fails: {hb_fails})",
            f"Telegram poll: {'alive' if tg_alive else 'DEAD'}",
            f"Open positions: {len(positions)}",
        ]

        return "\n".join(lines)

    def _handle_add_symbol(self, message) -> str:
        text = (getattr(message, "text", None) or "").strip()
        parts = text.split()
        if len(parts) < 2:
            return "Usage: /add SYMBOL\nExample: /add RELIANCE"
        symbol = parts[1].strip().upper()
        if not symbol.isalpha() and "-" not in symbol:
            return f"Invalid symbol: {symbol}"

        state = self._get_state()
        wl_path = state.get("watchlist_file_path")
        if not wl_path:
            return "No watchlist file configured (WATCHLIST_FILE not set)."

        current = list(state.get("watchlist", []))
        if symbol in current:
            return f"{symbol} is already in the watchlist."

        current.append(symbol)
        try:
            _atomic_write_watchlist(Path(wl_path), current)
        except Exception:
            logger.exception("Failed to write watchlist file for /add")
            return f"Failed to write watchlist file."

        return f"Added {symbol}. Watchlist ({len(current)}): {', '.join(current)}"

    def _handle_remove_symbol(self, message) -> str:
        text = (getattr(message, "text", None) or "").strip()
        parts = text.split()
        if len(parts) < 2:
            return "Usage: /remove SYMBOL\nExample: /remove RELIANCE"
        symbol = parts[1].strip().upper()

        state = self._get_state()
        wl_path = state.get("watchlist_file_path")
        if not wl_path:
            return "No watchlist file configured (WATCHLIST_FILE not set)."

        current = list(state.get("watchlist", []))
        if symbol not in current:
            return f"{symbol} is not in the watchlist."

        current.remove(symbol)
        try:
            _atomic_write_watchlist(Path(wl_path), current)
        except Exception:
            logger.exception("Failed to write watchlist file for /remove")
            return f"Failed to write watchlist file."

        return f"Removed {symbol}. Watchlist ({len(current)}): {', '.join(current)}"

    def send_critical_alert(self, text: str) -> None:
        try:
            self.bot.send_message(self.cfg.owner_chat_id, f"⚠️ CRITICAL: {text}")
        except Exception:
            logger.exception("Telegram critical alert failed")

    def send_info(self, text: str) -> None:
        try:
            self.bot.send_message(self.cfg.owner_chat_id, text)
        except Exception:
            logger.exception("Telegram info alert failed")

    def start_polling(self) -> None:
        self.bot.infinity_polling(skip_pending=True)

    def start_background_polling(self) -> threading.Thread:
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return self._poll_thread

        def _run():
            try:
                logger.info("Telegram inbound polling started")
                self.poll_alive = True
                self.bot.infinity_polling(skip_pending=True)
            except Exception:
                logger.exception("Telegram polling thread died -- remote kill switch is offline")
            finally:
                self.poll_alive = False

        self._poll_thread = threading.Thread(target=_run, name="telegram-poll", daemon=True)
        self._poll_thread.start()
        return self._poll_thread
