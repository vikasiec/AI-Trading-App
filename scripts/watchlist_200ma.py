"""Daily pre-market scanner: find Nifty 200 stocks crossing above 200 DMA.

Writes matching symbols to a JSON watchlist file that the trading bot
reads via WATCHLIST_FILE. Run via cron at ~8:45 IST before market open.

Usage:
    python scripts/watchlist_200ma.py [--lookback-days 5] [--output /path/to/watchlist.json]
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

NIFTY_200 = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC",
    "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK", "ASIANPAINT",
    "MARUTI", "HCLTECH", "SUNPHARMA", "TITAN", "BAJFINANCE", "WIPRO",
    "ULTRACEMCO", "NESTLEIND", "ONGC", "NTPC", "POWERGRID", "M&M",
    "JSWSTEEL", "TATASTEEL", "ADANIENT", "ADANIPORTS", "TECHM",
    "INDUSINDBK", "BAJAJFINSV", "HINDALCO", "DRREDDY", "CIPLA",
    "EICHERMOT", "DIVISLAB", "TATACONSUM", "APOLLOHOSP", "COALINDIA",
    "BPCL", "GRASIM", "BAJAJ-AUTO", "SBILIFE", "HDFCLIFE", "TATAMOTORS",
    "BRITANNIA", "LTIM", "HEROMOTOCO", "SHRIRAMFIN",
    # Nifty Next 50
    "ADANIGREEN", "ADANIPOWER", "AMBUJACEM", "BANKBARODA", "BEL",
    "BOSCHLTD", "CANBK", "CHOLAFIN", "COLPAL", "DLF", "GAIL",
    "GODREJCP", "HAL", "HAVELLS", "ICICIPRULI", "IIFL", "INDIGO",
    "IOC", "IRCTC", "JIOFIN", "JINDALSTEL", "JSWENERGY", "LTF",
    "LUPIN", "MARICO", "MAXHEALTH", "MOTHERSON", "MUTHOOTFIN",
    "NAUKRI", "NHPC", "OBEROIRLTY", "OFSS", "PAGEIND", "PERSISTENT",
    "PIDILITIND", "PNB", "POLYCAB", "RECLTD", "SBICARD", "SIEMENS",
    "SRF", "SUPREMEIND", "TATAELXSI", "TATAPOWER", "TORNTPHARM",
    "TRENT", "TVS", "UNIONBANK", "VEDL", "ZOMATO", "ZYDUSLIFE",
    # Nifty 100-200
    "ABB", "ABCAPITAL", "ABFRL", "ACC", "ALKEM", "APLAPOLLO",
    "ASTRAL", "ATGL", "ATUL", "AUBANK", "AUROPHARMA", "BALKRISIND",
    "BATAINDIA", "BHEL", "BIOCON", "BSE", "CANFINHOME", "CGPOWER",
    "CHAMBLFERT", "COFORGE", "CONCOR", "CUMMINSIND", "DALBHARAT",
    "DEEPAKNTR", "DEVYANI", "DIXON", "ESCORTS", "EXIDEIND",
    "FEDERALBNK", "FORTIS", "GLAND", "GLAXO", "GMRINFRA", "GNFC",
    "GODREJPROP", "GSPL", "GUJGASLTD", "HDFCAMC", "HINDPETRO",
    "HONAUT", "IDFCFIRSTB", "IEX", "INDIANB", "INDUSTOWER",
    "IRFC", "JKCEMENT", "JUBLFOOD", "KANSAINER", "KEI",
    "LALPATHLAB", "LICHSGFIN", "LTTS", "M&MFIN", "MANAPPURAM",
    "MFSL", "MGL", "MPHASIS", "MRF", "NATCOPHARM", "NIACL",
    "NMDC", "OBEROIRLTY", "OIL", "PATANJALI", "PETRONET",
    "PFC", "PIIND", "PRESTIGE", "PVRINOX", "RAMCOCEM",
    "RBLBANK", "RVNL", "SAIL", "SOLARINDS", "SONACOMS",
    "STARHEALTH", "SUNDARMFIN", "SUNDRMFAST", "SUNTV",
    "SUZLON", "SYNGENE", "TATACHEM", "TATACOMM", "TIINDIA",
    "TORNTPOWER", "TVSMOTOR", "UBL", "UNITDSPR", "UPL",
    "VOLTAS", "WHIRLPOOL", "YESBANK",
]


@dataclass
class CrossoverSignal:
    symbol: str
    price: float
    ma200: float
    pct_above: float
    volume_ratio: float


def compute_200ma_crossovers(
    lookback_days: int = 5,
    min_volume_ratio: float = 1.0,
) -> list[CrossoverSignal]:
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        sys.exit(1)

    nse_tickers = [f"{s}.NS" for s in NIFTY_200]
    logger.info("Downloading 1-year daily data for %d stocks...", len(nse_tickers))

    try:
        data = yf.download(nse_tickers, period="1y", interval="1d", progress=False, threads=True)
    except Exception:
        logger.exception("yfinance download failed")
        return []

    close = data["Close"] if "Close" in data.columns.get_level_values(0) else data.get("Close")
    volume = data["Volume"] if "Volume" in data.columns.get_level_values(0) else data.get("Volume")
    if close is None or close.empty:
        logger.error("No price data returned")
        return []

    crossovers: list[CrossoverSignal] = []
    for symbol in NIFTY_200:
        ticker = f"{symbol}.NS"
        if ticker not in close.columns:
            continue
        price_series = close[ticker].dropna()
        if len(price_series) < 201:
            continue

        ma200 = price_series.rolling(200).mean()
        before_price = price_series.iloc[-(lookback_days + 1)]
        before_ma = ma200.iloc[-(lookback_days + 1)]

        current_price = price_series.iloc[-1]
        current_ma = ma200.iloc[-1]
        if current_price != current_price or current_ma != current_ma:
            continue
        if current_price <= current_ma:
            continue
        if before_price >= before_ma:
            continue

        vol_ratio = 0.0
        if volume is not None and ticker in volume.columns:
            vol_series = volume[ticker].dropna()
            if len(vol_series) >= 21:
                avg_vol_20 = vol_series.iloc[-21:-1].mean()
                current_vol = vol_series.iloc[-1]
                if avg_vol_20 > 0:
                    vol_ratio = current_vol / avg_vol_20

        if vol_ratio < min_volume_ratio:
            logger.debug(
                "FILTERED: %s — crossed 200MA but volume ratio %.2fx < %.2fx minimum",
                symbol, vol_ratio, min_volume_ratio,
            )
            continue

        pct_above = ((current_price - current_ma) / current_ma) * 100
        signal = CrossoverSignal(
            symbol=symbol, price=current_price, ma200=current_ma,
            pct_above=pct_above, volume_ratio=vol_ratio,
        )
        logger.info(
            "CROSSOVER: %s — price %.2f > 200MA %.2f (%.1f%% above, vol %.1fx)",
            symbol, current_price, current_ma, pct_above, vol_ratio,
        )
        crossovers.append(signal)

    crossovers.sort(key=lambda s: s.volume_ratio, reverse=True)
    return crossovers


def main():
    parser = argparse.ArgumentParser(description="200 DMA crossover watchlist scanner")
    parser.add_argument("--lookback-days", type=int, default=5,
                        help="How many recent days to check for crossover (default: 5)")
    parser.add_argument("--min-volume-ratio", type=float, default=1.0,
                        help="Minimum volume/20d-avg ratio to include (default: 1.0)")
    parser.add_argument("--output", type=str,
                        default=str(Path.home() / ".indstocks" / "watchlist_200ma.json"),
                        help="Output JSON file path")
    parser.add_argument("--min-stocks", type=int, default=0,
                        help="If fewer crossovers found, keep existing watchlist unchanged")
    args = parser.parse_args()

    output_path = Path(args.output)
    signals = compute_200ma_crossovers(
        lookback_days=args.lookback_days,
        min_volume_ratio=args.min_volume_ratio,
    )
    symbols = [s.symbol for s in signals]

    if len(symbols) < args.min_stocks and output_path.exists():
        logger.warning(
            "Only %d crossovers found (min: %d), keeping existing watchlist",
            len(symbols), args.min_stocks,
        )
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(symbols, f, indent=2)

    logger.info(
        "Watchlist written to %s — %d stocks (lookback: %d days, min vol ratio: %.1fx)",
        output_path, len(symbols), args.lookback_days, args.min_volume_ratio,
    )
    for s in signals:
        logger.info("  %s: %.2f > 200MA %.2f (%.1f%% above, vol %.1fx)",
                     s.symbol, s.price, s.ma200, s.pct_above, s.volume_ratio)


if __name__ == "__main__":
    main()
