"""30-minute status pulse — sends a Telegram summary of bot activity.

Reports: service state, watchlist, entries/skips/Jev calls since last
pulse, open positions, skip-reason breakdown, and Jev daily usage.

Usage (cron, every 30 min during market hours):
    cd /home/opc/AI-Trading-App && PYTHONPATH=src python3 scripts/status_pulse.py
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

src = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src))

IST = timezone(timedelta(hours=5, minutes=30))
AUDIT_PATH = Path(__file__).resolve().parent.parent / "audit_trail.jsonl"
JEV_COUNTER_PATH = Path.home() / ".indstocks" / "jev_daily_counter.json"
POSITIONS_PATH = Path.home() / ".indstocks" / "open_positions.json"
WATCHLIST_PATH = Path.home() / ".indstocks" / "watchlist_200ma.json"


def service_status() -> str:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "jev-trader"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def last_tick_info() -> str:
    try:
        r = subprocess.run(
            ["journalctl", "-u", "jev-trader", "--no-pager", "-n", "1", "-o", "cat"],
            capture_output=True, text=True, timeout=5,
        )
        line = r.stdout.strip()
        if "Tick:" in line:
            return line.split("Tick:", 1)[1].strip()
        return line[-120:] if line else "no output"
    except Exception:
        return "error reading journal"


def audit_stats_since(since: datetime) -> dict:
    entries = 0
    skips = 0
    jev_calls = 0
    skip_reasons: Counter = Counter()
    symbols_scored: Counter = Counter()

    if not AUDIT_PATH.exists():
        return {"entries": 0, "skips": 0, "jev_calls": 0, "skip_reasons": {}, "symbols_scored": {}}

    for line in AUDIT_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_str = rec.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        if ts < since:
            continue
        action = rec.get("action", "")
        if str(action).startswith("BUY"):
            entries += 1
        elif action == "SKIP":
            skips += 1
            reason = rec.get("skip_reason") or rec.get("reason") or "unknown"
            skip_reasons[reason] += 1
        if rec.get("jev_conviction") is not None:
            jev_calls += 1
            sym = rec.get("symbol", "?")
            symbols_scored[sym] += 1

    return {
        "entries": entries,
        "skips": skips,
        "jev_calls": jev_calls,
        "skip_reasons": dict(skip_reasons.most_common(5)),
        "symbols_scored": dict(symbols_scored.most_common(5)),
    }


def open_positions() -> list[dict]:
    if not POSITIONS_PATH.exists():
        return []
    try:
        data = json.loads(POSITIONS_PATH.read_text())
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("positions"), list):
            return data["positions"]
        if isinstance(data, dict):
            return [v for v in data.values() if isinstance(v, dict) and v.get("security_id")]
        return []
    except Exception:
        return []


def jev_daily_usage() -> tuple[int, str]:
    if not JEV_COUNTER_PATH.exists():
        return 0, "?"
    try:
        data = json.loads(JEV_COUNTER_PATH.read_text())
        return data.get("count", 0), data.get("date", "?")
    except Exception:
        return 0, "?"


def watchlist_symbols() -> list[str]:
    if not WATCHLIST_PATH.exists():
        return []
    try:
        data = json.loads(WATCHLIST_PATH.read_text())
        if isinstance(data, list):
            return data
        return data.get("symbols", [])
    except Exception:
        return []


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    now = datetime.now(IST)
    window_start = now - timedelta(minutes=30)

    svc = service_status()
    svc_icon = "✅" if svc == "active" else "\U0001f534"
    tick = last_tick_info()
    stats = audit_stats_since(window_start)
    positions = open_positions()
    jev_count, jev_date = jev_daily_usage()
    wl = watchlist_symbols()

    lines = [
        f"\U0001f4ca STATUS PULSE — {now.strftime('%H:%M')} IST",
        "",
        f"{svc_icon} Service: {svc}",
        f"\U0001f4cb Watchlist: {len(wl)} symbols",
        f"\U0001f4c8 Last tick: {tick}",
        "",
        f"— Last 30 min —",
        f"Entries: {stats['entries']}  |  Skips: {stats['skips']}  |  Jev calls: {stats['jev_calls']}",
    ]

    if stats["skip_reasons"]:
        reasons = ", ".join(f"{r}: {c}" for r, c in stats["skip_reasons"].items())
        lines.append(f"Skip reasons: {reasons}")

    if stats["symbols_scored"]:
        scored = ", ".join(f"{s}({c})" for s, c in stats["symbols_scored"].items())
        lines.append(f"Jev scored: {scored}")

    lines.append("")
    if positions:
        lines.append(f"\U0001f4bc Open positions: {len(positions)}")
        for p in positions[:5]:
            sym = p.get("symbol", "?")
            entry_px = p.get("entry_price", "?")
            lines.append(f"  • {sym} @ {entry_px}")
    else:
        lines.append("\U0001f4bc Open positions: 0")

    lines.append(f"\U0001f916 Jev daily usage: {jev_count} calls ({jev_date})")

    body = "\n".join(lines)
    logger.info("Pulse:\n%s", body)

    try:
        from jev_indstocks_trader.config import load_config
        from jev_indstocks_trader.telegram_alerts import TelegramAlertNotifier
        cfg = load_config()
        notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=lambda: None)
        notifier.send_info(body)
        logger.info("Pulse sent to Telegram")
    except Exception:
        logger.exception("Could not send pulse to Telegram")


if __name__ == "__main__":
    main()
