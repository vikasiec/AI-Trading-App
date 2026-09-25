"""Integration-style tests for main._tick — where the review bugs lived."""
from __future__ import annotations

from jev_indstocks_trader.config import load_config
from jev_indstocks_trader.jev_client import ConvictionResult
from jev_indstocks_trader.main import _tick
from jev_indstocks_trader.market_data import LiveTickCache
from jev_indstocks_trader.positions import PositionStore


def _result(score=0.95, confidence=0.9):
    return ConvictionResult(score=score, confidence=confidence, raw={})


def _setup(tmp_path, mocker, paper=True, quotes=None):
    cfg = load_config()
    from dataclasses import replace
    cfg = replace(cfg, risk=replace(cfg.risk, paper_trading=paper))

    quotes = list(quotes or [
        {"ltp": 2450.0, "day_change_pct": 1.2, "volume": 10000},
        {"ltp": 2450.0, "day_change_pct": 1.2, "volume": 10000},
    ])

    gateway = mocker.Mock()
    gateway.get_quote.side_effect = quotes
    gateway.equity_from_funds.return_value = 1_000_000.0
    gateway.get_funds.return_value = {"sod_balance": 1_000_000.0, "available_balance": 1_000_000.0}
    gateway.place_limit_order.return_value = {"data": {"order_id": "OID1"}}
    gateway.auth_headers_fn = lambda: {}

    governor = mocker.Mock()
    governor.validate_trade.return_value = (True, "approved")
    governor.size_order.return_value = 10

    evaluator = mocker.Mock()
    evaluator.evaluate_signal.return_value = _result()

    instruments = mocker.Mock()
    instruments.resolve.return_value = {
        "security_id": "2885",
        "scrip_code": "NSE_2885",
        "exchange": "NSE",
        "segment": "EQUITY",
        "name": "RELIANCE",
    }

    audit = mocker.Mock()
    audit.log_decision.return_value = "dec-1"
    notifier = mocker.Mock()
    store = PositionStore(tmp_path / "positions.json")
    cache = LiveTickCache()
    return cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache


def test_tick_paper_books_local_position_no_live_order(tmp_path, mocker):
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=True
    )
    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache)

    gateway.place_limit_order.assert_not_called()
    governor.mark_executed.assert_called_once_with("2885")
    assert store.has_open("2885")
    pos = store.list_open()[0]
    assert pos.entry_price == 2450.0
    assert pos.qty == 10


def test_tick_slippage_collar_uses_price_after_jev(tmp_path, mocker):
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=True,
        quotes=[
            {"ltp": 2450.0, "day_change_pct": 0.0, "volume": 0},
            {"ltp": 2500.0, "day_change_pct": 0.0, "volume": 0},
        ],
    )
    governor.validate_trade.return_value = (False, "slippage_0.0204_exceeds_collar")

    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache)

    args, kwargs = governor.validate_trade.call_args
    assert kwargs["scored_at_price"] == 2450.0
    assert kwargs["live_ltp"] == 2500.0
    gateway.place_limit_order.assert_not_called()
    governor.mark_executed.assert_not_called()
    assert store.list_open() == []


def test_tick_live_order_failure_does_not_mark_executed_or_store(tmp_path, mocker):
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=False
    )
    gateway.place_limit_order.side_effect = RuntimeError("broker 500")

    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache)

    governor.mark_executed.assert_not_called()
    assert store.list_open() == []


def test_tick_live_success_marks_and_stores(tmp_path, mocker):
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=False
    )
    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache)

    gateway.place_limit_order.assert_called_once()
    governor.mark_executed.assert_called_once_with("2885")
    assert store.has_open("2885")
    notifier.send_info.assert_called_once()


def test_tick_feeds_change_and_volume_to_jev(tmp_path, mocker):
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=True
    )
    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache)

    context = evaluator.evaluate_signal.call_args.args[0]
    assert "Day change: 1.20%" in context
    assert "Volume: 10000" in context
