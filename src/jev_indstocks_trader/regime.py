"""Volatility-regime labeler (A2 scaffolding / D5-lite).

India VIX is not wired yet. Until D5 exists, classify a bar window from
its own realized volatility so H6 can be prototyped on the same CSVs
used for H4/H5. Labels are relative to the series, not an absolute VIX level.
"""
from __future__ import annotations

import math
from typing import Optional

from .historical_data import Bar

Regime = str  # "calm" | "normal" | "violent"


def realized_vol(bars: list[Bar]) -> Optional[float]:
    """Close-to-close stdev of returns. None if not enough bars."""
    if len(bars) < 3:
        return None
    rets = []
    for a, b in zip(bars, bars[1:]):
        if a.close <= 0:
            continue
        rets.append((b.close - a.close) / a.close)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def classify_regime(bars: list[Bar], lookback: int = 20) -> Regime:
    window = bars[-lookback:] if len(bars) >= lookback else bars
    vol = realized_vol(window)
    if vol is None:
        return "normal"
    # Heuristic bands on per-bar return stdev. For 1-minute bars these
    # numbers are small; for daily bars they are larger. Relative cuts
    # keep the function usable on either without a VIX feed.
    if vol < 0.004:
        return "calm"
    if vol > 0.015:
        return "violent"
    return "normal"
