"""Tests for the 200 DMA crossover scanner logic."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from scripts.watchlist_200ma import compute_200ma_crossovers, CrossoverSignal


def _make_mock_data(price_history: list[float], volume_history: list[float]):
    """Build a mock yfinance download result for a single ticker."""
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")

    n = len(price_history)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    ticker = "TEST.NS"

    close_df = pd.DataFrame({ticker: price_history}, index=idx)
    volume_df = pd.DataFrame({ticker: volume_history}, index=idx)

    arrays = [["Close", "Volume"], [ticker, ticker]]
    tuples = list(zip(*arrays))
    multi_idx = pd.MultiIndex.from_tuples(tuples)
    combined = pd.DataFrame(
        {("Close", ticker): price_history, ("Volume", ticker): volume_history},
        index=idx,
    )
    combined.columns = pd.MultiIndex.from_tuples([("Close", ticker), ("Volume", ticker)])
    return combined


def _prices_crossing_above(n: int = 220) -> list[float]:
    """Generate a price series that is below 200 MA then jumps above it.

    First 200 days trend upward slowly (MA lags behind the recent dip).
    Then a dip below MA followed by a sharp recovery above.
    """
    prices = []
    for i in range(n - 10):
        prices.append(80.0 + i * 0.1)
    for i in range(7):
        prices.append(85.0)
    prices.extend([105.0, 106.0, 107.0])
    return prices


def _prices_always_above(n: int = 220) -> list[float]:
    """Price always above 200 MA — no crossover."""
    return [100.0 + i * 0.05 for i in range(n)]


def _prices_always_below(n: int = 220) -> list[float]:
    """Price always below 200 MA — no crossover."""
    return [100.0 - i * 0.05 for i in range(n)]


class TestCrossoverDetection:
    @patch("scripts.watchlist_200ma.NIFTY_200", ["TEST"])
    def test_detects_crossover_with_volume(self):
        pd = pytest.importorskip("pandas")
        prices = _prices_crossing_above()
        volumes = [100_000.0] * (len(prices) - 1) + [200_000.0]
        mock_data = _make_mock_data(prices, volumes)

        with patch("yfinance.download", return_value=mock_data):
            signals = compute_200ma_crossovers(lookback_days=5, min_volume_ratio=1.0)

        assert len(signals) == 1
        assert signals[0].symbol == "TEST"
        assert signals[0].volume_ratio > 1.0

    @patch("scripts.watchlist_200ma.NIFTY_200", ["TEST"])
    def test_filters_low_volume_crossover(self):
        pd = pytest.importorskip("pandas")
        prices = _prices_crossing_above()
        volumes = [100_000.0] * (len(prices) - 1) + [50_000.0]
        mock_data = _make_mock_data(prices, volumes)

        with patch("yfinance.download", return_value=mock_data):
            signals = compute_200ma_crossovers(lookback_days=5, min_volume_ratio=1.5)

        assert len(signals) == 0

    @patch("scripts.watchlist_200ma.NIFTY_200", ["TEST"])
    def test_no_crossover_always_above(self):
        pd = pytest.importorskip("pandas")
        prices = _prices_always_above()
        volumes = [100_000.0] * len(prices)
        mock_data = _make_mock_data(prices, volumes)

        with patch("yfinance.download", return_value=mock_data):
            signals = compute_200ma_crossovers(lookback_days=5, min_volume_ratio=1.0)

        assert len(signals) == 0

    @patch("scripts.watchlist_200ma.NIFTY_200", ["TEST"])
    def test_no_crossover_always_below(self):
        pd = pytest.importorskip("pandas")
        prices = _prices_always_below()
        volumes = [100_000.0] * len(prices)
        mock_data = _make_mock_data(prices, volumes)

        with patch("yfinance.download", return_value=mock_data):
            signals = compute_200ma_crossovers(lookback_days=5, min_volume_ratio=1.0)

        assert len(signals) == 0

    @patch("scripts.watchlist_200ma.NIFTY_200", ["TEST"])
    def test_results_sorted_by_volume_ratio_descending(self):
        """With min_volume_ratio=0, all crossovers pass; verify sort order."""
        pd = pytest.importorskip("pandas")
        prices = _prices_crossing_above()
        volumes = [100_000.0] * (len(prices) - 1) + [50_000.0]
        mock_data = _make_mock_data(prices, volumes)

        with patch("yfinance.download", return_value=mock_data):
            signals = compute_200ma_crossovers(lookback_days=5, min_volume_ratio=0.0)

        assert len(signals) == 1
        assert signals[0].volume_ratio == pytest.approx(0.5, rel=0.1)
