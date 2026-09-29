"""Tests for scan_uptrend.py using synthetic OHLCV data — no network."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _make_ohlcv_frame(symbols: list[str], n_days: int = 250, **overrides):
    """Build a synthetic multi-level DataFrame mimicking yfinance output.

    overrides: symbol -> dict with optional keys: close, high, low, volume,
    each a list of n_days values. Unset columns get sensible defaults.
    """
    import pandas as pd
    import numpy as np

    dates = pd.bdate_range(end="2026-09-26", periods=n_days)
    tickers = [f"{s}.NS" for s in symbols] + ["^NSEI"]

    close_data: dict[str, list[float]] = {}
    high_data: dict[str, list[float]] = {}
    low_data: dict[str, list[float]] = {}
    vol_data: dict[str, list[float]] = {}

    for sym in symbols:
        t = f"{sym}.NS"
        ov = overrides.get(sym, {})
        base = 100.0
        # Default: gentle uptrend (0.1% per day)
        if "close" in ov:
            closes = ov["close"]
        else:
            closes = [base * (1 + 0.001 * i) for i in range(n_days)]
        close_data[t] = closes

        if "high" in ov:
            high_data[t] = ov["high"]
        else:
            high_data[t] = [c * 1.025 for c in closes]  # 2.5% above close

        if "low" in ov:
            low_data[t] = ov["low"]
        else:
            low_data[t] = [c * 0.975 for c in closes]  # 2.5% below close

        if "volume" in ov:
            vol_data[t] = ov["volume"]
        else:
            vol_data[t] = [1_000_000.0] * n_days  # ₹1Cr * close

    # Nifty: flat
    nifty_close = [20000.0 * (1 + 0.0005 * i) for i in range(n_days)]
    close_data["^NSEI"] = nifty_close
    high_data["^NSEI"] = [c * 1.01 for c in nifty_close]
    low_data["^NSEI"] = [c * 0.99 for c in nifty_close]
    vol_data["^NSEI"] = [1e9] * n_days

    arrays = {
        "Close": pd.DataFrame(close_data, index=dates),
        "High": pd.DataFrame(high_data, index=dates),
        "Low": pd.DataFrame(low_data, index=dates),
        "Volume": pd.DataFrame(vol_data, index=dates),
    }
    return pd.concat(arrays, axis=1)


def _run_scanner(pool, data, **kwargs):
    """Run screen() directly with synthetic data — no mocking needed."""
    import scan_uptrend

    close = data.get("Close") if hasattr(data, "get") else data["Close"]
    high = data.get("High") if hasattr(data, "get") else data["High"]
    low = data.get("Low") if hasattr(data, "get") else data["Low"]
    volume = data.get("Volume") if hasattr(data, "get") else data["Volume"]
    return scan_uptrend.screen(close, high, low, volume, pool, **kwargs)


def _uptrend_close(n=250, start=80.0, daily_pct=0.002):
    """Generate a clean uptrend: close > 50 DMA > 200 DMA guaranteed."""
    return [start * (1 + daily_pct) ** i for i in range(n)]


def _downtrend_close(n=250, start=150.0, daily_pct=-0.001):
    return [start * (1 + daily_pct) ** i for i in range(n)]


class TestFilters:
    def test_uptrend_stock_passes(self):
        closes = _uptrend_close()
        data = _make_ohlcv_frame(["GOOD"], close=None, GOOD={"close": closes})
        signals, funnel = _run_scanner(["GOOD"], data)
        assert len(signals) == 1
        assert signals[0].symbol == "GOOD"
        assert funnel["passed"] == 1

    def test_downtrend_stock_filtered(self):
        closes = _downtrend_close()
        data = _make_ohlcv_frame(["BAD"], BAD={"close": closes})
        signals, funnel = _run_scanner(["BAD"], data)
        assert len(signals) == 0
        assert funnel["no_uptrend"] >= 1

    def test_low_range_filtered(self):
        closes = _uptrend_close()
        # High and low very close to close: range < 2%
        highs = [c * 1.005 for c in closes]
        lows = [c * 0.995 for c in closes]
        data = _make_ohlcv_frame(["FLAT"], FLAT={"close": closes, "high": highs, "low": lows})
        signals, funnel = _run_scanner(["FLAT"], data)
        assert len(signals) == 0
        assert funnel["low_range"] >= 1

    def test_low_liquidity_filtered(self):
        closes = _uptrend_close()
        # Very low volume
        vols = [10.0] * 250
        data = _make_ohlcv_frame(["ILLIQUID"], ILLIQUID={"close": closes, "volume": vols})
        signals, funnel = _run_scanner(["ILLIQUID"], data)
        assert len(signals) == 0
        assert funnel["low_liquidity"] >= 1


class TestRankingAndCap:
    def test_12_qualifiers_returns_10(self):
        pool = [f"S{i}" for i in range(12)]
        overrides = {}
        for i, sym in enumerate(pool):
            overrides[sym] = {
                "close": _uptrend_close(daily_pct=0.002 + 0.0002 * i),
            }
        data = _make_ohlcv_frame(pool, **overrides)
        signals, funnel = _run_scanner(pool, data, max_picks=10)
        assert len(signals) == 10
        assert funnel["passed"] == 12

    def test_3_qualifiers_returns_3_no_padding(self):
        pool = [f"S{i}" for i in range(3)]
        overrides = {}
        for i, sym in enumerate(pool):
            overrides[sym] = {"close": _uptrend_close(daily_pct=0.002 + 0.0001 * i)}
        data = _make_ohlcv_frame(pool, **overrides)
        signals, funnel = _run_scanner(pool, data, max_picks=10)
        assert len(signals) == 3

    def test_0_qualifiers_returns_empty(self):
        pool = ["DOWN"]
        data = _make_ohlcv_frame(pool, DOWN={"close": _downtrend_close()})
        signals, funnel = _run_scanner(pool, data, max_picks=10)
        assert signals == []

    def test_ranked_by_rs_descending(self):
        pool = ["SLOW", "FAST"]
        overrides = {
            "SLOW": {"close": _uptrend_close(daily_pct=0.001)},
            "FAST": {"close": _uptrend_close(daily_pct=0.004)},
        }
        data = _make_ohlcv_frame(pool, **overrides)
        signals, funnel = _run_scanner(pool, data, max_picks=10)
        assert len(signals) == 2
        assert signals[0].symbol == "FAST"
        assert signals[0].rs_ratio > signals[1].rs_ratio


class TestAsOf:
    def test_as_of_slices_data(self):
        """screen(as_of) must not see rows after the cutoff date."""
        import pandas as pd

        closes = _uptrend_close(n=300)
        data = _make_ohlcv_frame(["GOOD"], n_days=300, GOOD={"close": closes})
        close_df = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data.get("Close")
        # 251 rows available — enough for 200-day MA + screen
        cutoff = close_df.index[250]

        signals, funnel = _run_scanner(["GOOD"], data, as_of=cutoff)
        assert len(signals) == 1
        assert signals[0].close == pytest.approx(closes[250], rel=1e-6)

    def test_as_of_too_early_gives_insufficient_data(self):
        """If as_of is so early there aren't 201 rows, stock is skipped."""
        import pandas as pd

        closes = _uptrend_close(n=250)
        data = _make_ohlcv_frame(["GOOD"], GOOD={"close": closes})
        close_df = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data.get("Close")
        cutoff = close_df.index[99]

        signals, funnel = _run_scanner(["GOOD"], data, as_of=cutoff)
        assert len(signals) == 0
        assert funnel["insufficient_data"] >= 1


class TestDeduplication:
    def test_duplicate_in_pool_emitted_once(self):
        import scan_uptrend
        with patch.object(scan_uptrend, "NIFTY_200", ["DUP", "DUP", "OTHER"]):
            result = scan_uptrend._deduplicated_pool()
        assert result == ["DUP", "OTHER"]
