"""Static NSE sector map (D4, first cut).

Not a live classification feed — a hand-maintained lookup for the names
this bot is likely to put on a watchlist. Unknown symbols return
"UNKNOWN" so a missing row cannot silently pass a sector cap.
"""
from __future__ import annotations

SECTOR_BY_SYMBOL: dict[str, str] = {
    "RELIANCE": "ENERGY",
    "ONGC": "ENERGY",
    "BPCL": "ENERGY",
    "TCS": "IT",
    "INFY": "IT",
    "WIPRO": "IT",
    "HCLTECH": "IT",
    "TECHM": "IT",
    "HDFCBANK": "BANKING",
    "ICICIBANK": "BANKING",
    "SBIN": "BANKING",
    "KOTAKBANK": "BANKING",
    "AXISBANK": "BANKING",
    "HDFC": "BANKING",
    "ITC": "FMCG",
    "HINDUNILVR": "FMCG",
    "NESTLEIND": "FMCG",
    "TATAMOTORS": "AUTO",
    "M&M": "AUTO",
    "MARUTI": "AUTO",
    "BAJFINANCE": "FINANCIALS",
    "BAJAJFINSV": "FINANCIALS",
    "SUNPHARMA": "PHARMA",
    "DRREDDY": "PHARMA",
    "TATASTEEL": "METALS",
    "HINDALCO": "METALS",
    "JSWSTEEL": "METALS",
    "NTPC": "POWER",
    "POWERGRID": "POWER",
    "BHARTIARTL": "TELECOM",
}


def sector_for(symbol: str) -> str:
    if not symbol:
        return "UNKNOWN"
    return SECTOR_BY_SYMBOL.get(symbol.upper().strip(), "UNKNOWN")
