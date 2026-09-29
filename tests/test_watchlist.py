import json
import time

from jev_indstocks_trader.config import load_config
from jev_indstocks_trader.watchlist import (
    DEFAULT_WATCHLIST,
    load_watchlist,
    try_reload_watchlist_file,
)


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


def test_empty_file_returns_empty_list(tmp_path, monkeypatch):
    wl_file = tmp_path / "watchlist.json"
    wl_file.write_text(json.dumps([]))
    monkeypatch.setenv("WATCHLIST_FILE", str(wl_file))
    cfg = load_config()
    assert load_watchlist(cfg) == []


def test_falls_back_to_default_when_nothing_configured():
    cfg = load_config()
    assert load_watchlist(cfg) == DEFAULT_WATCHLIST


def test_falls_back_to_default_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCHLIST_FILE", str(tmp_path / "does_not_exist.json"))
    cfg = load_config()
    assert load_watchlist(cfg) == DEFAULT_WATCHLIST


# --- try_reload_watchlist_file tests ---

def test_reload_detects_changed_file(tmp_path):
    wl_file = tmp_path / "watchlist.json"
    wl_file.write_text(json.dumps(["TCS", "INFY"]))
    old_mtime = wl_file.stat().st_mtime

    time.sleep(0.05)
    wl_file.write_text(json.dumps(["RELIANCE", "HDFCBANK"]))

    new_list, new_mtime, changed = try_reload_watchlist_file(
        wl_file, ["TCS", "INFY"], old_mtime,
    )
    assert changed is True
    assert new_list == ["RELIANCE", "HDFCBANK"]
    assert new_mtime > old_mtime


def test_reload_no_change_when_mtime_same(tmp_path):
    wl_file = tmp_path / "watchlist.json"
    wl_file.write_text(json.dumps(["TCS"]))
    mtime = wl_file.stat().st_mtime

    new_list, new_mtime, changed = try_reload_watchlist_file(
        wl_file, ["TCS"], mtime,
    )
    assert changed is False
    assert new_list == ["TCS"]


def test_reload_keeps_previous_on_truncated_file(tmp_path):
    wl_file = tmp_path / "watchlist.json"
    wl_file.write_text(json.dumps(["TCS"]))
    old_mtime = wl_file.stat().st_mtime

    time.sleep(0.05)
    wl_file.write_text("")  # truncated mid-write

    new_list, new_mtime, changed = try_reload_watchlist_file(
        wl_file, ["TCS"], old_mtime,
    )
    assert changed is False
    assert new_list == ["TCS"]
    assert new_mtime > old_mtime  # records bad mtime so next tick stays quiet


def test_reload_keeps_previous_on_corrupt_json(tmp_path):
    wl_file = tmp_path / "watchlist.json"
    wl_file.write_text(json.dumps(["TCS"]))
    old_mtime = wl_file.stat().st_mtime

    time.sleep(0.05)
    wl_file.write_text("{corrupt")

    new_list, new_mtime, changed = try_reload_watchlist_file(
        wl_file, ["TCS"], old_mtime,
    )
    assert changed is False
    assert new_list == ["TCS"]
    assert new_mtime > old_mtime  # records bad mtime so next tick stays quiet


def test_reload_keeps_previous_on_missing_file(tmp_path):
    missing = tmp_path / "gone.json"
    new_list, new_mtime, changed = try_reload_watchlist_file(
        missing, ["TCS"], 0.0,
    )
    assert changed is False
    assert new_list == ["TCS"]


def test_reload_empty_list_is_valid_change(tmp_path):
    """A file that parses to [] is a valid new watchlist (no padding)."""
    wl_file = tmp_path / "watchlist.json"
    wl_file.write_text(json.dumps(["TCS"]))
    old_mtime = wl_file.stat().st_mtime

    time.sleep(0.05)
    wl_file.write_text(json.dumps([]))

    new_list, new_mtime, changed = try_reload_watchlist_file(
        wl_file, ["TCS"], old_mtime,
    )
    assert changed is True
    assert new_list == []


def test_reload_same_content_different_mtime_not_changed(tmp_path):
    """File was touched but content is identical — no spurious reload."""
    wl_file = tmp_path / "watchlist.json"
    wl_file.write_text(json.dumps(["TCS", "INFY"]))
    old_mtime = wl_file.stat().st_mtime

    time.sleep(0.05)
    wl_file.write_text(json.dumps(["TCS", "INFY"]))

    new_list, new_mtime, changed = try_reload_watchlist_file(
        wl_file, ["TCS", "INFY"], old_mtime,
    )
    assert changed is False
