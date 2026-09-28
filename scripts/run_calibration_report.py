#!/usr/bin/env python3
"""Run the Jev calibration report against a real audit trail.

Usage:
    python scripts/run_calibration_report.py [path/to/audit_trail.jsonl]

Defaults to AUDIT_LOG_PATH from .env if not given a path argument.
Sends the report to Telegram when configured.

Cron (Friday 16:00 IST):
    0 16 * * 5 cd /home/opc/AI-Trading-App && PYTHONPATH=src /usr/bin/python3 scripts/run_calibration_report.py >> /home/opc/logs/calibration.log 2>&1
"""
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from jev_indstocks_trader.calibration import run_calibration_report  # noqa: E402
from jev_indstocks_trader.config import load_config  # noqa: E402


def main() -> int:
    load_dotenv()
    cfg = load_config()

    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        path = cfg.audit_log_path

    report = run_calibration_report(path)
    text = report.summary_text()
    print(text)

    try:
        from jev_indstocks_trader.telegram_alerts import TelegramAlertNotifier
        notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=lambda: None)
        notifier.send_info(f"📊 Weekly Calibration Report\n━━━━━━━━━━━━━━━━━━━━━\n{text}")
        logger.info("Telegram alert sent")
    except Exception:
        logger.debug("Telegram not configured, skipping alert")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
