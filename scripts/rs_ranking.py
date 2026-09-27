"""Relative Strength ranking scanner for Nifty 200.

Ranks stocks by their 3-month return relative to Nifty 50 index.
Outputs the top quartile (strongest momentum) as a JSON watchlist
that can be merged with the 200MA crossover watchlist.

Usage:
    python scripts/rs_ranking.py [--top-n 50] [--period-days 63]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NIFTY_200: list[str] = []


def _load_nifty_200():
    global NIFTY_200
    if not NIFTY_200:
        from watchlist_200ma import NIFTY_200 as _n200
        NIFTY_200 = _n200


@dataclass
class RSSignal:
    symbol: str
    stock_return_pct: float
    index_return_pct: float
    rs_ratio: float


def compute_rs_ranking(period_days: int = 63, top_n: int = 50) -> list[RSSignal]:
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        sys.exit(1)

    _load_nifty_200()
    nse_tickers = [f"{s}.NS" for s in NIFTY_200] + ["^NSEI"]
    logger.info("Downloading data for %d stocks + Nifty 50 index...", len(NIFTY_200))

    try:
        data = yf.download(nse_tickers, period="1y", interval="1d", progress=False, threads=True)
    except Exception:
        logger.exception("yfinance download failed")
        return []

    close = data["Close"] if "Close" in data.columns.get_level_values(0) else data.get("Close")
    if close is None or close.empty:
        logger.error("No price data returned")
        return []

    if "^NSEI" not in close.columns:
        logger.error("Nifty 50 index data not available")
        return []

    nifty = close["^NSEI"].dropna()
    if len(nifty) < period_days + 1:
        logger.error("Not enough Nifty 50 data for %d-day lookback", period_days)
        return []

    index_return = (nifty.iloc[-1] - nifty.iloc[-period_days]) / nifty.iloc[-period_days]

    signals: list[RSSignal] = []
    for symbol in NIFTY_200:
        ticker = f"{symbol}.NS"
        if ticker not in close.columns:
            continue
        series = close[ticker].dropna()
        if len(series) < period_days + 1:
            continue

        stock_return = (series.iloc[-1] - series.iloc[-period_days]) / series.iloc[-period_days]

        if stock_return != stock_return:
            continue

        rs_ratio = (1 + stock_return) / (1 + index_return) if (1 + index_return) != 0 else 0.0

        signals.append(RSSignal(
            symbol=symbol,
            stock_return_pct=stock_return * 100,
            index_return_pct=index_return * 100,
            rs_ratio=rs_ratio,
        ))

    signals.sort(key=lambda s: s.rs_ratio, reverse=True)
    return signals[:top_n]


def _send_telegram_alert(signals: list[RSSignal], top_n: int, period_days: int) -> None:
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

    if not signals:
        notifier.send_info(
            f"📈 RS Ranking — No data available\n"
            f"Could not compute relative strength rankings."
        )
        return

    header = (
        f"📈 Relative Strength Ranking\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Top {len(signals)} of Nifty 200 ({period_days}d vs Nifty 50)\n"
        f"Nifty 50 return: {signals[0].index_return_pct:+.1f}%\n\n"
    )
    top5 = signals[:5]
    rows = []
    for i, s in enumerate(top5, 1):
        rows.append(
            f"{i}. {s.symbol}\n"
            f"   Return: {s.stock_return_pct:+.1f}% | RS: {s.rs_ratio:.2f}"
        )
    body = "\n".join(rows)
    if len(signals) > 5:
        remaining = [s.symbol for s in signals[5:15]]
        body += f"\n\n6-15: {', '.join(remaining)}"
    footer = f"\n\n━━━━━━━━━━━━━━━━━━━━━\nFull list: {len(signals)} stocks in watchlist"
    notifier.send_info(header + body + footer)


def main():
    parser = argparse.ArgumentParser(description="Relative Strength ranking scanner")
    parser.add_argument("--period-days", type=int, default=63,
                        help="Lookback period in trading days (default: 63 = ~3 months)")
    parser.add_argument("--top-n", type=int, default=50,
                        help="Number of top-ranked stocks to include (default: 50)")
    parser.add_argument("--output", type=str,
                        default=str(Path.home() / ".indstocks" / "watchlist_rs.json"),
                        help="Output JSON file path")
    args = parser.parse_args()

    output_path = Path(args.output)
    signals = compute_rs_ranking(period_days=args.period_days, top_n=args.top_n)
    symbols = [s.symbol for s in signals]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(symbols, f, indent=2)

    logger.info("RS ranking written to %s — %d stocks (top %d, %d-day period)",
                output_path, len(symbols), args.top_n, args.period_days)
    for i, s in enumerate(signals[:10], 1):
        logger.info("  %2d. %s: return %+.1f%% vs Nifty %+.1f%%, RS %.2f",
                     i, s.symbol, s.stock_return_pct, s.index_return_pct, s.rs_ratio)

    _send_telegram_alert(signals, args.top_n, args.period_days)


if __name__ == "__main__":
    main()
