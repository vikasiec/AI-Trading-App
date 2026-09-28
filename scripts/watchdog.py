"""Bot watchdog — alerts on Telegram if jev-trader service is not running.

Meant to run via cron every 30 min during market hours. Only alerts
when the service SHOULD be running but isn't.

Usage (cron, every 30 min 09:15-15:30 IST = 03:45-10:00 UTC):
    */30 3-9 * * 1-5 cd /home/opc/AI-Trading-App && PYTHONPATH=src python3 scripts/watchdog.py
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

src = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src))


def is_service_active() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "jev-trader"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def is_market_hours() -> bool:
    from jev_indstocks_trader.session import session_phase
    return session_phase() in ("open", "flatten")


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    if not is_market_hours():
        logger.info("Outside market hours, skipping watchdog check")
        return

    if is_service_active():
        logger.info("jev-trader is active — all good")
        return

    logger.error("jev-trader is NOT active during market hours!")

    try:
        from jev_indstocks_trader.config import load_config
        from jev_indstocks_trader.telegram_alerts import TelegramAlertNotifier
        cfg = load_config()
        notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=lambda: None)
        notifier.send_critical_alert(
            "\U0001f6a8 WATCHDOG: jev-trader service is DOWN\n"
            "Bot is not running during market hours.\n"
            "Run: sudo systemctl start jev-trader"
        )
    except Exception:
        logger.exception("Could not send watchdog alert")

    sys.exit(1)


if __name__ == "__main__":
    main()
