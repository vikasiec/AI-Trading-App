"""Tests for BarCache session VWAP, OR-low, and last_completed_close."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

from jev_indstocks_trader.bar_cache import BarCache
from jev_indstocks_trader.historical_data import Bar

IST = ZoneInfo("Asia/Kolkata")


def _utc(hour: int, minute: int, second: int = 5) -> datetime:
    """Build a UTC timestamp from IST hour:minute (for 2026-10-01)."""
    ist_dt = datetime(2026, 10, 1, hour, minute, second, tzinfo=IST)
    return ist_dt.astimezone(timezone.utc)


def _feed_bar(cache: BarCache, symbol: str, ist_h: int, ist_m: int,
              close: float, volume: int, high: float | None = None,
              low: float | None = None) -> None:
    """Simulate a completed 1-min bar by feeding a tick at ist_h:ist_m."""
    ts = _utc(ist_h, ist_m)
    h = high if high is not None else close
    lo = low if low is not None else close
    cache.update(symbol, lo, volume, ts)
    if h != lo:
        cache.update(symbol, h, volume, ts)
    cache.update(symbol, close, volume, ts)


class TestSessionVwap:
    def test_vwap_excludes_forming_bar(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        _feed_bar(cache, "A", 9, 15, 100.0, 1000)
        assert cache.session_vwap("A") is None
        _feed_bar(cache, "A", 9, 16, 110.0, 2000)
        vwap = cache.session_vwap("A")
        assert vwap is not None
        assert vwap == pytest.approx(100.0)

    def test_vwap_zero_volume_gets_weight_one(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        _feed_bar(cache, "A", 9, 15, 100.0, 0)
        _feed_bar(cache, "A", 9, 16, 200.0, 0)
        vwap = cache.session_vwap("A")
        assert vwap is not None
        assert vwap == pytest.approx(100.0)

    def test_vwap_weighted_correctly(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        _feed_bar(cache, "A", 9, 15, 100.0, 100)
        _feed_bar(cache, "A", 9, 16, 200.0, 300)
        _feed_bar(cache, "A", 9, 17, 150.0, 500)
        vwap = cache.session_vwap("A")
        expected = (100.0 * 100 + 200.0 * 200) / (100 + 200)
        assert vwap == pytest.approx(expected)

    def test_vwap_survives_trimming(self):
        cache = BarCache(max_bars=5)
        cache.roll_day("2026-10-01")
        for m in range(9 * 60 + 15, 9 * 60 + 15 + 10):
            h, mi = divmod(m, 60)
            _feed_bar(cache, "A", h, mi, 100.0 + m - (9 * 60 + 15), 1000 + m)
        _feed_bar(cache, "A", 9, 26, 999.0, 2000)
        assert len(cache.bars("A")) <= 5
        vwap = cache.session_vwap("A")
        assert vwap is not None

    def test_vwap_none_before_session(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        assert cache.session_vwap("A") is None

    def test_roll_day_resets_vwap(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        _feed_bar(cache, "A", 9, 15, 100.0, 1000)
        _feed_bar(cache, "A", 9, 16, 110.0, 2000)
        assert cache.session_vwap("A") is not None
        cache.roll_day("2026-10-02")
        assert cache.session_vwap("A") is None


class TestOrLow:
    def test_or_low_none_before_finalized(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        _feed_bar(cache, "A", 9, 15, 100.0, 1000, low=95.0)
        _feed_bar(cache, "A", 9, 16, 105.0, 1000, low=98.0)
        assert cache.or_low("A") is None

    def test_or_low_correct_at_0925(self):
        """OR low finalizes when 09:25 bar starts (after 09:24 bar is folded)."""
        cache = BarCache()
        cache.roll_day("2026-10-01")
        for m in range(15, 25):
            _feed_bar(cache, "A", 9, m, 100.0 + m, 1000, low=90.0 + m)
        # First tick at 09:25 folds the 09:24 bar AND finalizes OR
        _feed_bar(cache, "A", 9, 25, 200.0, 1000)
        or_low = cache.or_low("A")
        assert or_low is not None
        assert or_low == pytest.approx(90.0 + 15)

    def test_or_low_excludes_0925_bar(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        for m in range(15, 25):
            _feed_bar(cache, "A", 9, m, 100.0, 1000, low=100.0)
        # 09:25 bar has very low value — should NOT be in OR range
        _feed_bar(cache, "A", 9, 25, 50.0, 1000, low=50.0)
        or_low = cache.or_low("A")
        assert or_low == pytest.approx(100.0)

    def test_or_low_none_without_0915_bar(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        _feed_bar(cache, "A", 9, 25, 100.0, 1000)
        assert cache.or_low("A") is None

    def test_roll_day_resets_or_low(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        for m in range(15, 25):
            _feed_bar(cache, "A", 9, m, 100.0, 1000, low=95.0)
        _feed_bar(cache, "A", 9, 25, 110.0, 1000)
        assert cache.or_low("A") is not None
        cache.roll_day("2026-10-02")
        assert cache.or_low("A") is None


class TestLastCompletedClose:
    def test_none_with_single_bar(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        _feed_bar(cache, "A", 9, 15, 100.0, 1000)
        assert cache.last_completed_close("A") is None

    def test_returns_previous_bar_close(self):
        cache = BarCache()
        cache.roll_day("2026-10-01")
        _feed_bar(cache, "A", 9, 15, 100.0, 1000)
        _feed_bar(cache, "A", 9, 16, 110.0, 2000)
        assert cache.last_completed_close("A") == pytest.approx(100.0)
