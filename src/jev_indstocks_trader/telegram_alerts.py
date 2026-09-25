"""Telegram alert dispatcher and authenticated remote kill switch.

Only TELEGRAM_OWNER_CHAT_ID may trigger /halt or /flatten. Messages from
any other chat are silently ignored -- the bot does not confirm its own
existence to strangers who find the bot username.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import telebot

from .config import TelegramConfig

logger = logging.getLogger(__name__)


class TelegramAlertNotifier:
    def __init__(self, cfg: TelegramConfig, kill_switch_callback: Callable[[], None]):
        self.cfg = cfg
        self.bot = telebot.TeleBot(cfg.bot_token)
        self.kill_switch_callback = kill_switch_callback
        self._poll_thread: Optional[threading.Thread] = None

        @self.bot.message_handler(commands=["halt", "flatten"])
        def handle_emergency(message):
            if message.chat.id != self.cfg.owner_chat_id:
                logger.warning("Ignored /halt from unauthorized chat_id=%s", message.chat.id)
                return
            logger.critical("Authorized kill-switch command received from owner")
            self.kill_switch_callback()
            self.bot.reply_to(message, "EMERGENCY: Kill-switch triggered. All positions flattened.")

        @self.bot.message_handler(commands=["status"])
        def handle_status(message):
            if message.chat.id != self.cfg.owner_chat_id:
                return
            self.bot.reply_to(message, "Bot is alive and listening.")

    def send_critical_alert(self, text: str) -> None:
        self.bot.send_message(self.cfg.owner_chat_id, f"\u26a0\ufe0f CRITICAL: {text}")

    def send_info(self, text: str) -> None:
        self.bot.send_message(self.cfg.owner_chat_id, text)

    def start_polling(self) -> None:
        """Blocking call -- run this in its own process/thread."""
        self.bot.infinity_polling(skip_pending=True)

    def start_background_polling(self) -> threading.Thread:
        """Daemon thread so /halt and /flatten actually work in the trading process.

        Outbound send_* still works without this; inbound commands do not.
        """
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return self._poll_thread

        def _run():
            try:
                logger.info("Telegram inbound polling started")
                self.bot.infinity_polling(skip_pending=True)
            except Exception:
                logger.exception("Telegram polling thread died -- remote kill switch is offline")

        self._poll_thread = threading.Thread(target=_run, name="telegram-poll", daemon=True)
        self._poll_thread.start()
        return self._poll_thread
