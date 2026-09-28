"""Price-based StrategyFn implementations for hypothesis tests (H4, H5, H6).

These functions are used by entry.rule_vote() in the live loop when
ENTRY_MODE is 'rule' or 'jev_and_rule'. They also serve as the
testable units for hypothesis tests in scripts/run_hypothesis_tests.py.

IMPORTANT: the intraday strategies (momentum_long, mean_reversion_long)
treat each bar as the next bar. On minute data, 5 bars = 5 minutes.
On daily data, 5 bars = a week. Do not reuse intraday parameters on
daily bars. Use daily_breakout_long() for daily bar strategies.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

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
        if avg_vol <= 0:
            return False
        vol_ok = window[-1].volume >= avg_vol * volume_multiple
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


def daily_breakout_long(
    lookback: int = 20,
    volume_multiple: float = 1.0,
    index_bars: Optional[dict[date, Bar]] = None,
    index_rising_days: int = 5,
):
    """H6: daily swing breakout -- close at a `lookback`-day high on
    above-average volume, with the Nifty rising over the last
    `index_rising_days` sessions.

    One sentence: "a liquid stock at a 20-day high while the Nifty is
    also rising, held for a few days."

    index_bars is a dict mapping date -> Bar for the Nifty 50 index.
    If None, the index filter is skipped.
    """

    def _fn(bars: list[Bar]) -> bool:
        if len(bars) < lookback + 1:
            return False
        current = bars[-1]
        window = bars[-(lookback + 1):-1]

        highest_close = max(b.close for b in window)
        if current.close <= highest_close:
            return False

        avg_vol = sum(b.volume for b in window) / len(window)
        if avg_vol <= 0:
            return False
        if current.volume < avg_vol * volume_multiple:
            return False

        if index_bars is not None:
            bar_date = current.timestamp.date()
            idx_bar = index_bars.get(bar_date)
            if idx_bar is None:
                return False
            idx_start_date = None
            sorted_dates = sorted(d for d in index_bars if d <= bar_date)
            if len(sorted_dates) < index_rising_days + 1:
                return False
            idx_start = index_bars[sorted_dates[-(index_rising_days + 1)]]
            if idx_bar.close <= idx_start.close:
                return False

        return True

    return _fn
