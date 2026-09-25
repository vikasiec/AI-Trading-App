"""Data ingestion & feature preparation layer.

Turns raw ticks + unstructured context (news, filings, sentiment) into a
compact text state for Jev to score. Jev is a scorer, not a summarizer --
keep this budget tight (a few hundred tokens) rather than dumping raw feeds.

This is intentionally minimal -- plug in your real tick/news sources here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarketSnapshot:
    symbol: str
    ltp: float
    day_change_pct: float
    volume: int
    headlines: list[str]  # already deduped/recent, upstream


MAX_CONTEXT_TOKENS_APPROX = 500  # rough budget; Jev call sites should stay well under this


def build_context(snapshot: MarketSnapshot) -> str:
    headline_block = "\n".join(f"- {h}" for h in snapshot.headlines[:5]) or "- (no recent headlines)"
    return (
        f"Symbol: {snapshot.symbol}\n"
        f"LTP: {snapshot.ltp}\n"
        f"Day change: {snapshot.day_change_pct:.2f}%\n"
        f"Volume: {snapshot.volume}\n"
        f"Recent headlines:\n{headline_block}"
    )
