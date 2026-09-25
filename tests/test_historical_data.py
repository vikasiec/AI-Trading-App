from datetime import datetime

from jev_indstocks_trader.historical_data import Bar, load_from_csv, save_to_csv


def make_bars():
    return [
        Bar(timestamp=datetime(2026, 1, 2, 9, 15), open=100.0, high=102.0, low=99.0, close=101.0, volume=10000),
        Bar(timestamp=datetime(2026, 1, 2, 9, 16), open=101.0, high=103.0, low=100.5, close=102.5, volume=12000),
    ]


def test_save_and_load_roundtrip(tmp_path):
    bars = make_bars()
    path = tmp_path / "bars.csv"
    save_to_csv(bars, path)

    loaded = load_from_csv(path)
    assert len(loaded) == 2
    assert loaded[0].open == 100.0
    assert loaded[0].close == 101.0
    assert loaded[1].volume == 12000
    assert loaded[0].timestamp == datetime(2026, 1, 2, 9, 15)


def test_load_from_csv_parses_arbitrary_file(tmp_path):
    path = tmp_path / "manual.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-02T09:15:00,100,102,99,101,10000\n"
        "2026-01-02T09:16:00,101,103,100.5,102.5,12000\n"
    )
    bars = load_from_csv(path)
    assert len(bars) == 2
    assert bars[1].high == 103.0


def test_save_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "dir" / "bars.csv"
    save_to_csv(make_bars(), path)
    assert path.exists()
