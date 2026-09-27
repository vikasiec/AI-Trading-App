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
from datetime import datetime
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


def compute_200ma_crossovers(lookback_days: int = 5) -> list[str]:
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
    if close is None or close.empty:
        logger.error("No price data returned")
        return []

    crossovers = []
    for symbol in NIFTY_200:
        ticker = f"{symbol}.NS"
        if ticker not in close.columns:
            continue
        series = close[ticker].dropna()
        if len(series) < 201:
            continue

        ma200 = series.rolling(200).mean()
        recent = series.iloc[-lookback_days:]
        recent_ma = ma200.iloc[-lookback_days:]
        before_price = series.iloc[-(lookback_days + 1)]
        before_ma = ma200.iloc[-(lookback_days + 1)]

        current_price = series.iloc[-1]
        current_ma = ma200.iloc[-1]
        if current_price != current_price or current_ma != current_ma:
            continue
        if current_price <= current_ma:
            continue
        if before_price < before_ma:
            pct_above = ((current_price - current_ma) / current_ma) * 100
            logger.info(
                "CROSSOVER: %s — price %.2f crossed above 200MA %.2f (%.1f%% above)",
                symbol, current_price, current_ma, pct_above,
            )
            crossovers.append(symbol)

    return crossovers


def main():
    parser = argparse.ArgumentParser(description="200 DMA crossover watchlist scanner")
    parser.add_argument("--lookback-days", type=int, default=5,
                        help="How many recent days to check for crossover (default: 5)")
    parser.add_argument("--output", type=str,
                        default=str(Path.home() / ".indstocks" / "watchlist_200ma.json"),
                        help="Output JSON file path")
    parser.add_argument("--min-stocks", type=int, default=0,
                        help="If fewer crossovers found, keep existing watchlist unchanged")
    args = parser.parse_args()

    output_path = Path(args.output)
    crossovers = compute_200ma_crossovers(lookback_days=args.lookback_days)

    if len(crossovers) < args.min_stocks and output_path.exists():
        logger.warning(
            "Only %d crossovers found (min: %d), keeping existing watchlist",
            len(crossovers), args.min_stocks,
        )
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(crossovers, f, indent=2)

    logger.info(
        "Watchlist written to %s — %d stocks crossing above 200MA (lookback: %d days)",
        output_path, len(crossovers), args.lookback_days,
    )
    logger.info("Symbols: %s", ", ".join(crossovers) if crossovers else "(none)")


if __name__ == "__main__":
    main()
