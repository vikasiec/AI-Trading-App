"""Tests for the Yahoo Finance daily data fetcher."""
import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetch_nse_daily import MIN_BARS, fetch_yahoo, main, save_csv


YAHOO_RESPONSE = {
    "chart": {
        "result": [{
            "timestamp": [
                1695859200,  # 2023-09-28
                1695945600,  # 2023-09-29
                1696032000,  # 2023-09-30 (Saturday — should be skipped by Yahoo but we handle None)
            ],
            "indicators": {
                "quote": [{
                    "open": [100.0, 101.0, None],
                    "high": [102.0, 103.0, None],
                    "low": [99.0, 100.5, None],
                    "close": [101.5, 102.5, None],
                    "volume": [50000, 60000, None],
                }]
            },
        }],
    },
}


class FakeResponse:
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return YAHOO_RESPONSE


def test_fetch_yahoo_skips_none_rows():
    with patch("fetch_nse_daily.requests.get", return_value=FakeResponse()):
        with patch("fetch_nse_daily.date") as mock_date:
            mock_date.today.return_value = date(2023, 10, 5)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            bars = fetch_yahoo("TEST")

    assert len(bars) == 2
    assert bars[0]["open"] == 100.0
    assert bars[1]["close"] == 102.5


def test_fetch_yahoo_drops_today():
    today_ts = 1696032000  # use as "today"
    resp = {
        "chart": {
            "result": [{
                "timestamp": [1695859200, 1696032000],
                "indicators": {
                    "quote": [{
                        "open": [100.0, 101.0],
                        "high": [102.0, 103.0],
                        "low": [99.0, 100.0],
                        "close": [101.0, 102.0],
                        "volume": [50000, 60000],
                    }]
                },
            }],
        },
    }

    class Resp:
        def raise_for_status(self): pass
        def json(self): return resp

    with patch("fetch_nse_daily.requests.get", return_value=Resp()):
        with patch("fetch_nse_daily.date") as mock_date:
            mock_date.today.return_value = date(2023, 9, 30)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            bars = fetch_yahoo("TEST")

    assert len(bars) == 1
    assert "2023-09-28" in bars[0]["timestamp"]


def test_save_csv_creates_loadable_file(tmp_path):
    bars = [
        {"timestamp": "2024-01-02T09:15:00", "open": 100.0, "high": 102.0,
         "low": 99.0, "close": 101.0, "volume": 5000},
    ]
    path = save_csv("TEST", bars, out_dir=tmp_path)
    assert path.exists()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from jev_indstocks_trader.historical_data import load_from_csv
    loaded = load_from_csv(path)
    assert len(loaded) == 1
    assert loaded[0].close == 101.0


def test_timestamp_format_is_iso():
    with patch("fetch_nse_daily.requests.get", return_value=FakeResponse()):
        with patch("fetch_nse_daily.date") as mock_date:
            mock_date.today.return_value = date(2023, 10, 5)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            bars = fetch_yahoo("TEST")

    for bar in bars:
        assert "T09:15:00" in bar["timestamp"]


def test_special_chars_in_ticker_are_url_encoded():
    """M&M and ^NSEI must be percent-encoded in the URL path."""
    captured_url = []

    class Resp:
        def raise_for_status(self): pass
        def json(self): return YAHOO_RESPONSE

    def capture_get(url, **kw):
        captured_url.append(url)
        return Resp()

    with patch("fetch_nse_daily.requests.get", side_effect=capture_get):
        with patch("fetch_nse_daily.date") as mock_date:
            mock_date.today.return_value = date(2023, 10, 5)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            fetch_yahoo("M&M")

    assert "M%26M" in captured_url[0]
    assert "&M.NS" not in captured_url[0]


def test_bars_are_sorted_by_timestamp():
    unsorted_resp = {
        "chart": {
            "result": [{
                "timestamp": [1695945600, 1695859200],  # reversed
                "indicators": {
                    "quote": [{
                        "open": [101.0, 100.0],
                        "high": [103.0, 102.0],
                        "low": [100.5, 99.0],
                        "close": [102.5, 101.5],
                        "volume": [60000, 50000],
                    }]
                },
            }],
        },
    }

    class Resp:
        def raise_for_status(self): pass
        def json(self): return unsorted_resp

    with patch("fetch_nse_daily.requests.get", return_value=Resp()):
        with patch("fetch_nse_daily.date") as mock_date:
            mock_date.today.return_value = date(2023, 10, 5)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            bars = fetch_yahoo("TEST")

    assert bars[0]["timestamp"] < bars[1]["timestamp"]


def test_main_rejects_short_history_and_writes_nothing(tmp_path, monkeypatch):
    short = [{
        "timestamp": "2024-01-02T09:15:00",
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1,
    }]
    monkeypatch.setattr("fetch_nse_daily.fetch_yahoo", lambda *a, **k: list(short))
    monkeypatch.setattr("fetch_nse_daily.DATA_DIR", tmp_path / "daily")
    monkeypatch.setattr("fetch_nse_daily.INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr("fetch_nse_daily.INDICES", {})

    code = main(["TEST"])

    assert MIN_BARS >= 600
    assert code == 1
    assert list(tmp_path.rglob("*.csv")) == []
