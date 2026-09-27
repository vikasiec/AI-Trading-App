"""End-of-day prediction accuracy checker.

Runs after market close (schedule via cron at ~15:45 IST). Reads the
day's audit trail, fetches actual closing prices from yfinance, and
sends a Telegram report comparing predictions vs outcomes.

Usage:
    python scripts/eod_accuracy.py [--date 2026-09-28] [--audit-log ./audit_trail.jsonl]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _fetch_closing_prices(security_ids: list[str], target_date: str) -> dict[str, float]:
    """Fetch closing prices for the given securities on the target date.

    security_ids are INDstocks-format IDs. We map them back to NSE
    tickers for yfinance.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        return {}

    tickers = [f"{sid}.NS" for sid in security_ids]
    if not tickers:
        return {}

    logger.info("Fetching closing prices for %d symbols...", len(tickers))
    try:
        data = yf.download(
            tickers, start=target_date,
            end=(datetime.fromisoformat(target_date) + timedelta(days=3)).strftime("%Y-%m-%d"),
            interval="1d", progress=False, threads=True,
        )
    except Exception:
        logger.exception("yfinance download failed")
        return {}

    if data.empty:
        logger.warning("No price data returned from yfinance")
        return {}

    close = data["Close"] if "Close" in data.columns.get_level_values(0) else data.get("Close")
    if close is None:
        return {}

    prices: dict[str, float] = {}
    for sid in security_ids:
        ticker = f"{sid}.NS"
        if len(tickers) == 1:
            series = close
        elif ticker in close.columns:
            series = close[ticker]
        else:
            continue
        day_data = series.dropna()
        if not day_data.empty:
            prices[sid] = float(day_data.iloc[0])

    return prices


def main():
    parser = argparse.ArgumentParser(description="EOD prediction accuracy report")
    parser.add_argument(
        "--date", type=str,
        default=datetime.now(IST).strftime("%Y-%m-%d"),
        help="Date to check (YYYY-MM-DD, default: today IST)",
    )
    parser.add_argument(
        "--audit-log", type=str,
        default=str(Path.home() / ".indstocks" / "audit_trail.jsonl"),
        help="Path to audit trail JSONL",
    )
    parser.add_argument(
        "--tracking-file", type=str,
        default=str(Path.home() / ".indstocks" / "accuracy_tracking.jsonl"),
        help="Path to accuracy tracking JSONL",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    from jev_indstocks_trader.accuracy import (
        compute_accuracy,
        format_accuracy_alert,
        format_trend_summary,
        load_accuracy_history,
        load_daily_decisions,
        save_daily_report,
    )

    audit_path = Path(args.audit_log)
    if not audit_path.exists():
        env_path = Path(__file__).resolve().parent.parent / "audit_trail.jsonl"
        if env_path.exists():
            audit_path = env_path

    buys, skips = load_daily_decisions(audit_path, args.date)
    logger.info("Found %d BUY and %d high-conviction SKIP decisions for %s", len(buys), len(skips), args.date)

    if not buys and not skips:
        logger.info("No decisions found for %s — nothing to report", args.date)
        return

    all_sids = list({d["security_id"] for d in buys + skips})
    closing_prices = _fetch_closing_prices(all_sids, args.date)
    logger.info("Fetched closing prices for %d/%d symbols", len(closing_prices), len(all_sids))

    report = compute_accuracy(buys, skips, closing_prices, args.date)

    tracking_path = Path(args.tracking_file)
    save_daily_report(report, tracking_path)
    logger.info("Daily accuracy saved to %s", tracking_path)

    alert_text = format_accuracy_alert(report)
    logger.info("\n%s", alert_text)

    history = load_accuracy_history(tracking_path)
    trend = format_trend_summary(history)

    try:
        from jev_indstocks_trader.config import load_config
        from jev_indstocks_trader.telegram_alerts import TelegramAlertNotifier
        cfg = load_config()
        notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=lambda: None)

        full_message = alert_text
        if trend:
            full_message += f"\n\n{trend}"
        notifier.send_info(full_message)
        logger.info("Telegram alert sent")
    except Exception:
        logger.debug("Telegram not configured, skipping alert")


if __name__ == "__main__":
    main()
