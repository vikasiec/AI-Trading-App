"""Telegram alert dispatcher and authenticated remote kill switch.

Owner must send `/halt CONFIRM` (or `/flatten CONFIRM`) from their user id
in the owner chat. Forwards and other users are ignored.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import telebot

from .config import TelegramConfig

logger = logging.getLogger(__name__)


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


class TelegramAlertNotifier:
    def __init__(self, cfg: TelegramConfig, kill_switch_callback: Callable[[], None]):
        self.cfg = cfg
        self.bot = telebot.TeleBot(cfg.bot_token)
        self.kill_switch_callback = kill_switch_callback
        self._poll_thread: Optional[threading.Thread] = None
        self.poll_alive = False

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
            self.kill_switch_callback()
            self.bot.reply_to(message, "EMERGENCY: Kill-switch triggered. All positions flattened.")

        @self.bot.message_handler(commands=["status"])
        def handle_status(message):
            if getattr(getattr(message, "chat", None), "id", None) != self.cfg.owner_chat_id:
                return
            if _is_forwarded(message):
                return
            self.bot.reply_to(message, "Bot is alive and listening.")

    def send_critical_alert(self, text: str) -> None:
        self.bot.send_message(self.cfg.owner_chat_id, f"\u26a0\ufe0f CRITICAL: {text}")

    def send_info(self, text: str) -> None:
        self.bot.send_message(self.cfg.owner_chat_id, text)

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
