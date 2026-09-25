"""Price-based StrategyFn implementations for hypothesis tests (H4, H5).

These are NOT wired into the live loop. Roadmap rule: a strategy earns a
live hook only after it beats a random-entry baseline net of costs on
real history. `scripts/run_hypothesis_tests.py` is the gate.
"""
from __future__ import annotations

from .historical_data import Bar


def momentum_long(
    lookback: int = 5,
    min_return_pct: float = 0.005,
    volume_multiple: float = 1.2,
):
    """H4: last `lookback` bars up more than min_return_pct on above-avg volume."""

    def _fn(bars: list[Bar]) -> bool:
        if lookback < 1 or len(bars) < lookback + 1:
            return False
        window = bars[-(lookback + 1) :]
        start, end = window[0].close, window[-1].close
        if start <= 0:
            return False
        ret = (end - start) / start
        vols = [b.volume for b in window[:-1]]
        avg_vol = sum(vols) / len(vols) if vols else 0
        vol_ok = avg_vol <= 0 or window[-1].volume >= avg_vol * volume_multiple
        return ret >= min_return_pct and vol_ok

    return _fn


def mean_reversion_long(
    lookback: int = 20,
    deviation_pct: float = 0.01,
):
    """H5: close is more than deviation_pct below simple VWAP of last lookback bars."""

    def _fn(bars: list[Bar]) -> bool:
        if lookback < 2 or len(bars) < lookback:
            return False
        window = bars[-lookback:]
        pv = sum(b.close * max(b.volume, 1) for b in window)
        vol = sum(max(b.volume, 1) for b in window)
        vwap = pv / vol
        if vwap <= 0:
            return False
        return window[-1].close <= vwap * (1 - deviation_pct)

    return _fn


def random_entry(every_n: int = 15, offset: int = 0):
    """Baseline: enter on a fixed cadence. Same costs/exits as the strategy under test."""

    def _fn(bars: list[Bar]) -> bool:
        n = len(bars)
        if n < 2:
            return False
        return (n + offset) % max(every_n, 1) == 0

    return _fn
