import json

from jev_indstocks_trader.config import load_config
from jev_indstocks_trader.watchlist import DEFAULT_WATCHLIST, load_watchlist


def test_env_symbols_take_priority(monkeypatch):
    monkeypatch.setenv("WATCHLIST_SYMBOLS", "tcs, infy , hdfcbank")
    cfg = load_config()
    assert load_watchlist(cfg) == ["TCS", "INFY", "HDFCBANK"]


def test_falls_back_to_file_when_no_env_symbols(tmp_path, monkeypatch):
    wl_file = tmp_path / "watchlist.json"
    wl_file.write_text(json.dumps(["wipro", "itc"]))
    monkeypatch.setenv("WATCHLIST_FILE", str(wl_file))
    cfg = load_config()
    assert load_watchlist(cfg) == ["WIPRO", "ITC"]


def test_falls_back_to_default_when_nothing_configured():
    cfg = load_config()
    assert load_watchlist(cfg) == DEFAULT_WATCHLIST


def test_falls_back_to_default_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCHLIST_FILE", str(tmp_path / "does_not_exist.json"))
    cfg = load_config()
    assert load_watchlist(cfg) == DEFAULT_WATCHLIST
