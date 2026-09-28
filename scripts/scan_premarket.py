"""Combined pre-market scanner: 200MA crossover + RS ranking.

Runs both scanners and outputs the intersection (stocks that crossed
200MA with volume AND are in the RS top quartile) plus the full RS
watchlist as fallback when no crossovers pass all filters.

Usage:
    python scripts/scan_premarket.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    from watchlist_200ma import compute_200ma_crossovers, CrossoverSignal
    from rs_ranking import compute_rs_ranking, RSSignal

    home = Path.home() / ".indstocks"
    home.mkdir(parents=True, exist_ok=True)

    crossover_signals = compute_200ma_crossovers(
        lookback_days=5, min_volume_ratio=1.5, require_above_50ma=True,
    )
    crossover_symbols = {s.symbol for s in crossover_signals}

    rs_signals = compute_rs_ranking(period_days=63, top_n=50)
    rs_symbols = {s.symbol for s in rs_signals}
    rs_lookup = {s.symbol: s for s in rs_signals}

    elite = [s for s in crossover_signals if s.symbol in rs_symbols]
    crossover_only = [s for s in crossover_signals if s.symbol not in rs_symbols]

    MIN_WATCHLIST = 10

    if elite:
        watchlist = [s.symbol for s in elite]
        logger.info("ELITE watchlist (200MA cross + RS top 50): %s", watchlist)
    elif crossover_signals:
        watchlist = [s.symbol for s in crossover_signals]
        logger.info("No elite overlap — using 200MA crossovers: %s", watchlist)
    else:
        watchlist = []
        logger.info("No crossovers today")

    if len(watchlist) < MIN_WATCHLIST:
        existing = set(watchlist)
        pad = [s.symbol for s in rs_signals if s.symbol not in existing]
        needed = MIN_WATCHLIST - len(watchlist)
        watchlist.extend(pad[:needed])
        logger.info("Padded to %d with RS top stocks: %s", len(watchlist), watchlist)

    watchlist_path = home / "watchlist_200ma.json"
    with open(watchlist_path, "w") as f:
        json.dump(watchlist, f, indent=2)
    logger.info("Watchlist written to %s — %d stocks", watchlist_path, len(watchlist))

    rs_path = home / "watchlist_rs.json"
    with open(rs_path, "w") as f:
        json.dump([s.symbol for s in rs_signals], f, indent=2)

    _send_combined_alert(elite, crossover_only, rs_signals[:10])


def _send_combined_alert(
    elite: list, crossover_only: list, rs_top10: list,
) -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        from jev_indstocks_trader.config import load_config
        from jev_indstocks_trader.telegram_alerts import TelegramAlertNotifier
        cfg = load_config()
        notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=lambda: None)
    except Exception:
        logger.debug("Telegram not configured, skipping alert")
        return

    sections = []

    sections.append("📊 Pre-Market Scan Report\n━━━━━━━━━━━━━━━━━━━━━")

    if elite:
        header = f"\n🏆 ELITE ({len(elite)}) — 200MA Cross + RS Top 50 + Volume\n"
        for s in elite:
            header += (
                f"\n▸ {s.symbol}\n"
                f"  ₹{s.price:,.2f} | 50MA ₹{s.ma50:,.2f} | 200MA ₹{s.ma200:,.2f}\n"
                f"  Above 200MA: {s.pct_above:+.1f}% | Vol: {s.volume_ratio:.1f}x"
            )
        sections.append(header)

    if crossover_only:
        co_names = ", ".join(s.symbol for s in crossover_only)
        sections.append(f"\n📈 200MA Cross only (not in RS top 50):\n{co_names}")

    if rs_top10:
        rs_block = "\n💪 RS Top 10 (3-month strength vs Nifty):\n"
        for i, s in enumerate(rs_top10, 1):
            rs_block += f"  {i}. {s.symbol} ({s.stock_return_pct:+.1f}%, RS {s.rs_ratio:.2f})\n"
        sections.append(rs_block)

    total = len(elite) + len(crossover_only)
    if not elite and not crossover_only:
        sections.append("\nNo crossovers today — watchlist filled from RS ranking.")
    else:
        sections.append(f"\n━━━━━━━━━━━━━━━━━━━━━\n{total} signal stocks + RS padding = full watchlist.")

    notifier.send_info("\n".join(sections))


if __name__ == "__main__":
    main()
