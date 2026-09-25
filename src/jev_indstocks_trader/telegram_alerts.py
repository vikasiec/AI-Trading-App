"""Telegram alert dispatcher and authenticated remote kill switch.

Only TELEGRAM_OWNER_CHAT_ID may trigger /halt or /flatten. Messages from
any other chat are silently ignored -- the bot does not confirm its own
existence to strangers who find the bot username.
"""
from __future__ import annotations

import logging
from typing import Callable

import telebot

from .config import TelegramConfig

logger = logging.getLogger(__name__)


class TelegramAlertNotifier:
    def __init__(self, cfg: TelegramConfig, kill_switch_callback: Callable[[], None]):
        self.cfg = cfg
        self.bot = telebot.TeleBot(cfg.bot_token)
        self.kill_switch_callback = kill_switch_callback

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
        self.bot.infinity_polling()
