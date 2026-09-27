"""Tests for the Relative Strength ranking scanner."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts.rs_ranking import compute_rs_ranking, RSSignal


def _make_mock_data(stock_prices: list[float], index_prices: list[float]):
    pd = pytest.importorskip("pandas")
    n = len(stock_prices)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    combined = pd.DataFrame(
        {("Close", "TEST.NS"): stock_prices, ("Close", "^NSEI"): index_prices},
        index=idx,
    )
    combined.columns = pd.MultiIndex.from_tuples(
        [("Close", "TEST.NS"), ("Close", "^NSEI")]
    )
    return combined


class TestRSRanking:
    @patch("scripts.rs_ranking.NIFTY_200", ["TEST"])
    @patch("scripts.rs_ranking._load_nifty_200", lambda: None)
    def test_outperformer_has_rs_above_1(self):
        pd = pytest.importorskip("pandas")
        stock = [100.0] * 200 + [130.0]
        index = [100.0] * 200 + [110.0]
        mock_data = _make_mock_data(stock, index)

        with patch("yfinance.download", return_value=mock_data):
            signals = compute_rs_ranking(period_days=63, top_n=50)

        assert len(signals) == 1
        assert signals[0].rs_ratio > 1.0
        assert signals[0].stock_return_pct > signals[0].index_return_pct

    @patch("scripts.rs_ranking.NIFTY_200", ["TEST"])
    @patch("scripts.rs_ranking._load_nifty_200", lambda: None)
    def test_underperformer_has_rs_below_1(self):
        pd = pytest.importorskip("pandas")
        stock = [100.0] * 200 + [95.0]
        index = [100.0] * 200 + [110.0]
        mock_data = _make_mock_data(stock, index)

        with patch("yfinance.download", return_value=mock_data):
            signals = compute_rs_ranking(period_days=63, top_n=50)

        assert len(signals) == 1
        assert signals[0].rs_ratio < 1.0

    @patch("scripts.rs_ranking.NIFTY_200", ["TEST"])
    @patch("scripts.rs_ranking._load_nifty_200", lambda: None)
    def test_results_sorted_by_rs_descending(self):
        pd = pytest.importorskip("pandas")
        stock = [100.0] * 200 + [120.0]
        index = [100.0] * 200 + [110.0]
        mock_data = _make_mock_data(stock, index)

        with patch("yfinance.download", return_value=mock_data):
            signals = compute_rs_ranking(period_days=63, top_n=50)

        assert len(signals) >= 1
        for i in range(len(signals) - 1):
            assert signals[i].rs_ratio >= signals[i + 1].rs_ratio
