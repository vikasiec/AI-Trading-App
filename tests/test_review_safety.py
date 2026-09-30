"""Guards for the 2026-09-29 review that sit outside the older module tests."""
from __future__ import annotations

import logging
from dataclasses import replace

from jev_indstocks_trader import __version__
from jev_indstocks_trader.bar_cache import BarCache
from jev_indstocks_trader.config import AppConfig, load_config, validate_config
from jev_indstocks_trader.positions import PositionStore
from jev_indstocks_trader.slog import RedactSecretsFilter
from jev_indstocks_trader.telegram_alerts import HaltState, TelegramAlertNotifier


def test_package_version_matches_release():
    assert __version__ == "0.19.0"


def test_bad_entry_mode_and_tick_block_startup():
    cfg = load_config()
    bad = replace(
        cfg,
        risk=replace(cfg.risk, entry_mode="jev_and_rules", tick_size_inr=0),
    )
    problems = validate_config(bad)
    assert any("ENTRY_MODE" in p for p in problems)
    assert any("DEFAULT_TICK_SIZE_INR" in p for p in problems)


def test_bool_env_accepts_one_and_strips(monkeypatch):
    monkeypatch.setenv("JEV_NOISE_VETO", " 1 ")
    monkeypatch.setenv("GTT_ENABLED", " yes ")
    cfg = AppConfig()
    assert cfg.jev.noise_veto is True
    assert cfg.risk.gtt_enabled is True


def test_bar_cache_drops_yesterday():
    cache = BarCache()
    from datetime import datetime, timezone
    ts = datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)
    cache.roll_day("2026-09-28")
    cache.update("RELIANCE", 100.0, volume=10, ts=ts)
    assert cache.bars("RELIANCE")
    cache.roll_day("2026-09-29")
    assert cache.bars("RELIANCE") == []


def test_redact_filter_hides_bot_token():
    record = logging.LogRecord(
        name="t", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="post failed %s", args=("https://api.telegram.org/bot123:ABC_def-9/sendMessage",),
        exc_info=None,
    )
    assert RedactSecretsFilter().filter(record) is True
    assert record.getMessage() == "post failed https://api.telegram.org/bot<redacted>/sendMessage"


def test_halt_flag_stays_set():
    state = HaltState()
    assert state.is_halted() is False
    state.trip()
    assert state.is_halted() is True


def test_critical_alert_swallows_send_errors(mocker):
    cfg = load_config().telegram
    notifier = mocker.Mock()
    send = mocker.Mock(side_effect=RuntimeError("telegram down"))
    # Build the real notifier only far enough to call the guarded sender.
    obj = TelegramAlertNotifier.__new__(TelegramAlertNotifier)
    obj.cfg = cfg
    obj.bot = notifier
    notifier.send_message = send
    obj.send_critical_alert("positions open")
    send.assert_called_once()


def test_position_store_ignores_unknown_fields_and_returns_copies(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text(
        '{"2885": {"security_id": "2885", "scrip_code": "NSE_2885", "exchange": "NSE",'
        ' "segment": "EQUITY", "product": "INTRADAY", "qty": 2, "entry_price": 10,'
        ' "stop_loss_price": 9, "target_price": 11, "opened_at": 1, "decision_id": "d",'
        ' "future_field": "ignore-me"}}',
        encoding="utf-8",
    )
    store = PositionStore(path)
    first = store.list_open()[0]
    first.qty = 99
    assert store.get("2885").qty == 2
