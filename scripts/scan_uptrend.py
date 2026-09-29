"""Pre-market uptrend scanner.

Selects liquid Nifty 200 names in an established uptrend with enough
daily range to pay the bot's 1% stop / 2% target. Ranks by 63-day
relative strength vs Nifty, takes the top 10 (or fewer), no padding.

Filters (all on yesterday's close):
  1. Close > 50 DMA > 200 DMA  (established uptrend)
  2. 20-day avg daily range >= 2%  (enough volatility for intraday target)
  3. 20-day avg traded value >= liquidity_multiple * position_capital
  4. Rank by 63-day return vs Nifty 50 (relative strength)
  5. Top max_picks, no padding

Usage:
    python scripts/scan_uptrend.py [--max-picks 10] [--min-range-pct 2.0]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from watchlist_200ma import NIFTY_200  # noqa: E402


@dataclass
class UptrendSignal:
    symbol: str
    close: float
    ma50: float
    ma200: float
    avg_range_pct: float
    avg_traded_value: float
    rs_ratio: float
    stock_return_pct: float
    index_return_pct: float


def _deduplicated_pool() -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for s in NIFTY_200:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def download_pool():
    """Download 2y OHLCV for the deduplicated pool + Nifty 50."""
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        sys.exit(1)

    pool = _deduplicated_pool()
    nse_tickers = [f"{s}.NS" for s in pool] + ["^NSEI"]
    logger.info("Downloading 2-year daily data for %d stocks + Nifty 50...", len(pool))

    try:
        data = yf.download(nse_tickers, period="2y", interval="1d", progress=False, threads=True)
    except Exception:
        logger.exception("yfinance download failed")
        return None, pool
    return data, pool


def screen(
    close, high, low, volume,
    pool: list[str],
    *,
    as_of=None,
    max_picks: int = 10,
    min_range_pct: float = 2.0,
    liquidity_multiple: float = 20.0,
    position_capital: float = 50_000.0,
    rs_period_days: int = 63,
) -> tuple[list[UptrendSignal], dict[str, int]]:
    """Pure screening function on pre-downloaded data.

    as_of: if set, slice all data to rows <= this date (point-in-time replay).
    Returns (qualifying signals ranked by RS, filter funnel counts).
    """
    import pandas as pd

    if as_of is not None:
        close = close.loc[:as_of]
        if high is not None:
            high = high.loc[:as_of]
        if low is not None:
            low = low.loc[:as_of]
        if volume is not None:
            volume = volume.loc[:as_of]

    if close is None or close.empty:
        return [], {"no_data": 1}

    if "^NSEI" not in close.columns:
        return [], {"no_index": 1}

    nifty_raw = close["^NSEI"].dropna()
    if len(nifty_raw) < rs_period_days + 1:
        return [], {"insufficient_index_data": 1}

    min_traded_value = liquidity_multiple * position_capital

    funnel: dict[str, int] = {
        "pool": len(pool),
        "insufficient_data": 0,
        "no_uptrend": 0,
        "low_range": 0,
        "low_liquidity": 0,
        "passed": 0,
    }

    signals: list[UptrendSignal] = []
    for symbol in pool:
        ticker = f"{symbol}.NS"
        if ticker not in close.columns:
            funnel["insufficient_data"] += 1
            continue

        cols = {"close": close[ticker]}
        has_hlv = (
            high is not None and low is not None and volume is not None
            and ticker in high.columns and ticker in low.columns and ticker in volume.columns
        )
        if not has_hlv:
            funnel["insufficient_data"] += 1
            continue
        cols["high"] = high[ticker]
        cols["low"] = low[ticker]
        cols["volume"] = volume[ticker]
        aligned = pd.DataFrame(cols).dropna()

        if len(aligned) < 201:
            funnel["insufficient_data"] += 1
            continue

        price_series = aligned["close"]
        current_close = price_series.iloc[-1]
        ma50 = price_series.rolling(50).mean().iloc[-1]
        ma200 = price_series.rolling(200).mean().iloc[-1]

        if current_close != current_close or ma50 != ma50 or ma200 != ma200:
            funnel["insufficient_data"] += 1
            continue

        if not (current_close > ma50 > ma200):
            funnel["no_uptrend"] += 1
            continue

        # Range: (high - low) / close on the aligned last 20 sessions
        last20 = aligned.iloc[-20:]
        daily_range_pct = ((last20["high"].values - last20["low"].values) / last20["close"].values) * 100
        avg_range = float(daily_range_pct.mean())

        if avg_range < min_range_pct:
            funnel["low_range"] += 1
            continue

        # Liquidity: mean of close * volume on aligned sessions
        avg_traded_value = float((last20["close"] * last20["volume"]).mean())

        if avg_traded_value < min_traded_value:
            funnel["low_liquidity"] += 1
            continue

        # RS: use the stock's aligned index to slice Nifty to the same dates
        if len(aligned) < rs_period_days + 1:
            funnel["insufficient_data"] += 1
            continue
        stock_return = (price_series.iloc[-1] - price_series.iloc[-rs_period_days]) / price_series.iloc[-rs_period_days]
        if stock_return != stock_return:
            funnel["insufficient_data"] += 1
            continue
        nifty_aligned = nifty_raw.reindex(aligned.index).dropna()
        if len(nifty_aligned) < rs_period_days + 1:
            funnel["insufficient_data"] += 1
            continue
        index_return = (nifty_aligned.iloc[-1] - nifty_aligned.iloc[-rs_period_days]) / nifty_aligned.iloc[-rs_period_days]
        rs_ratio = (1 + stock_return) / (1 + index_return) if (1 + index_return) != 0 else 0.0

        funnel["passed"] += 1
        signals.append(UptrendSignal(
            symbol=symbol,
            close=float(current_close),
            ma50=float(ma50),
            ma200=float(ma200),
            avg_range_pct=avg_range,
            avg_traded_value=avg_traded_value,
            rs_ratio=float(rs_ratio),
            stock_return_pct=float(stock_return * 100),
            index_return_pct=float(index_return * 100),
        ))

    signals.sort(key=lambda s: s.rs_ratio, reverse=True)
    return signals[:max_picks], funnel


def compute_uptrend_watchlist(**kwargs) -> tuple[list[UptrendSignal], dict[str, int]]:
    """Download + screen in one call (CLI convenience)."""
    data, pool = download_pool()
    if data is None:
        return [], {"download_failed": 1}

    close = data.get("Close")
    high = data.get("High")
    low = data.get("Low")
    volume = data.get("Volume")
    return screen(close, high, low, volume, pool, **kwargs)


def _send_telegram_alert(
    signals: list[UptrendSignal],
    funnel: dict[str, int],
    max_picks: int,
    min_range_pct: float,
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

    header = (
        f"📊 Uptrend Scanner\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Pool: {funnel.get('pool', 0)} | "
        f"Uptrend: {funnel.get('passed', 0) + funnel.get('low_range', 0) + funnel.get('low_liquidity', 0)} | "
        f"Range ≥{min_range_pct}%: {funnel.get('passed', 0) + funnel.get('low_liquidity', 0)} | "
        f"Liquid: {funnel.get('passed', 0)}\n"
    )

    if not signals:
        notifier.send_info(header + "\nNo stocks passed all filters today.")
        return

    rows = []
    for i, s in enumerate(signals, 1):
        rows.append(
            f"\n{i}. {s.symbol}\n"
            f"   ₹{s.close:,.2f} > 50MA ₹{s.ma50:,.2f} > 200MA ₹{s.ma200:,.2f}\n"
            f"   Range: {s.avg_range_pct:.1f}% | RS: {s.rs_ratio:.2f} ({s.stock_return_pct:+.1f}%)\n"
            f"   Traded: ₹{s.avg_traded_value / 1e7:.1f}Cr/day"
        )

    footer = f"\n\n━━━━━━━━━━━━━━━━━━━━━\nTop {len(signals)} of {funnel.get('passed', 0)} qualifiers → watchlist"
    notifier.send_info(header + "".join(rows) + footer)


def main():
    parser = argparse.ArgumentParser(description="Pre-market uptrend scanner")
    parser.add_argument("--max-picks", type=int, default=10)
    parser.add_argument("--min-range-pct", type=float, default=2.0)
    parser.add_argument("--liquidity-multiple", type=float, default=20.0,
                        help="Min avg traded value = multiple * position capital (default: 20x)")
    parser.add_argument("--position-capital", type=float, default=50_000.0)
    parser.add_argument("--output", type=str,
                        default=str(Path.home() / ".indstocks" / "watchlist_uptrend.json"))
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()

    signals, funnel = compute_uptrend_watchlist(
        max_picks=args.max_picks,
        min_range_pct=args.min_range_pct,
        liquidity_multiple=args.liquidity_multiple,
        position_capital=args.position_capital,
    )

    # Data-level failures: don't overwrite existing file
    if any(funnel.get(k) for k in ("download_failed", "no_data", "no_index", "insufficient_index_data")):
        logger.error("Data failure — not writing watchlist. Funnel: %s", funnel)
        sys.exit(1)

    symbols = [s.symbol for s in signals]
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=str(output_path.parent), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(symbols, f, indent=2)
        os.replace(tmp, str(output_path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    logger.info("Uptrend watchlist written to %s — %d stocks", output_path, len(symbols))
    logger.info("Funnel: %s", funnel)
    for i, s in enumerate(signals, 1):
        logger.info(
            "  %2d. %s: ₹%.2f | 50MA ₹%.2f | 200MA ₹%.2f | range %.1f%% | RS %.2f (%+.1f%%)",
            i, s.symbol, s.close, s.ma50, s.ma200, s.avg_range_pct, s.rs_ratio, s.stock_return_pct,
        )

    if not args.no_telegram:
        _send_telegram_alert(signals, funnel, args.max_picks, args.min_range_pct)


if __name__ == "__main__":
    main()
