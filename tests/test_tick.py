"""Integration-style tests for main._tick — where the review bugs lived."""
from __future__ import annotations

from dataclasses import replace

from jev_indstocks_trader.config import load_config
from jev_indstocks_trader.execution_gateway import FillResult
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
    gateway.get_quotes_batch.return_value = {"NSE_2885": quotes[0]}
    gateway.get_quote.side_effect = quotes[1:]
    gateway.equity_from_funds.return_value = 1_000_000.0
    gateway.get_funds.return_value = {"sod_balance": 1_000_000.0, "available_balance": 1_000_000.0}
    gateway.place_limit_order.return_value = {"data": {"order_id": "OID1"}}
    gateway.wait_for_fill.return_value = FillResult(status="FILLED", filled_qty=10, avg_price=None, raw={})
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


def test_tick_rejected_fill_does_not_store_position(tmp_path, mocker):
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=False
    )
    gateway.wait_for_fill.return_value = FillResult(
        status="REJECTED", filled_qty=0, avg_price=None, raw={"status": "REJECTED"}
    )

    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache)

    governor.mark_executed.assert_not_called()
    assert store.list_open() == []


def test_tick_ambiguous_fill_cancels_and_marks_executed(tmp_path, mocker):
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=False
    )
    gateway.wait_for_fill.return_value = FillResult(status="TIMEOUT", filled_qty=0, avg_price=None, raw=None)

    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache)

    gateway.cancel_order.assert_called_once_with("OID1")
    governor.mark_executed.assert_called_once_with("2885")
    assert store.list_open() == []
    notifier.send_critical_alert.assert_called_once()


def test_tick_uses_confirmed_avg_price_for_entry(tmp_path, mocker):
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=False
    )
    gateway.wait_for_fill.return_value = FillResult(
        status="FILLED", filled_qty=10, avg_price=2452.5, raw={"status": "COMPLETE"}
    )

    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache)

    assert store.has_open("2885")
    pos = store.list_open()[0]
    assert pos.entry_price == 2452.5
    assert pos.stop_loss_price == 2452.5 * (1 - cfg.risk.stop_loss_pct)


def test_tick_partial_fill_books_only_filled_qty(tmp_path, mocker):
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=False
    )
    gateway.wait_for_fill.return_value = FillResult(
        status="PARTIAL", filled_qty=3, avg_price=2451.0, raw={}
    )

    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache)

    gateway.cancel_order.assert_called_once_with("OID1")
    pos = store.list_open()[0]
    assert pos.qty == 3
    assert pos.entry_price == 2451.0
    governor.mark_executed.assert_called_once()


def test_tick_feeds_change_and_volume_to_jev(tmp_path, mocker):
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=True
    )
    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache)

    context = evaluator.evaluate_signal.call_args.args[0]
    assert "Day change: 1.20%" in context
    assert "Volume: 10000" in context


def test_tick_drawdown_calls_kill_switch_fn(tmp_path, mocker):
    """Fix #1: daily drawdown must invoke kill_switch_fn, not a bare _kill_switch name."""
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=True
    )
    governor.validate_trade.return_value = (False, "daily_drawdown_limit_hit")
    kill_fn = mocker.Mock()

    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache, kill_switch_fn=kill_fn)

    kill_fn.assert_called_once()


def test_tick_drawdown_fires_kill_switch_only_once(tmp_path, mocker):
    """Kill switch must not re-fire for remaining symbols in the same tick."""
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=True
    )
    governor.validate_trade.return_value = (False, "daily_drawdown_limit_hit")
    kill_fn = mocker.Mock()

    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE", "TCS", "INFY"], None, cache, kill_switch_fn=kill_fn)

    kill_fn.assert_called_once()


# --- Entry cutoff tests ---

def test_tick_closing_soon_skips_entries_and_jev(tmp_path, mocker):
    """When allow_entries=False with closing_soon reason, Jev is not called."""
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=True
    )
    _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          store, ["RELIANCE"], None, cache,
          allow_entries=False, entry_block_reason="closing_soon")

    evaluator.evaluate_signal.assert_not_called()
    governor.validate_trade.assert_not_called()
    assert store.list_open() == []


# --- Sweep tests ---

def _setup_jev_and_rule(tmp_path, mocker, sweep_score=0.72, sweep_conf=0.45):
    """Like _setup but with jev_and_rule mode so rule gate blocks normal entries."""
    cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache = _setup(
        tmp_path, mocker, paper=True
    )
    cfg = replace(cfg, risk=replace(cfg.risk, entry_mode="jev_and_rule"))
    evaluator.evaluate_signal.return_value = ConvictionResult(
        score=sweep_score, confidence=sweep_conf, raw={},
    )
    return cfg, gateway, governor, evaluator, instruments, audit, notifier, store, cache


def test_sweep_fires_on_rule_gated_symbol(tmp_path, mocker):
    """A rule-gated symbol in sweep_pending gets a Jev call logged as SWEEP."""
    cfg, gw, gov, ev, inst, audit, notif, store, cache = _setup_jev_and_rule(tmp_path, mocker)
    jev_daily = [0]
    jev_last = {}
    sweep_pending = {"RELIANCE"}
    counter = mocker.Mock()

    _tick(cfg, gw, gov, ev, inst, audit, notif, store, ["RELIANCE"], None, cache,
          jev_last_called=jev_last, jev_daily_count=jev_daily, jev_counter=counter,
          sweep_pending=sweep_pending)

    ev.evaluate_signal.assert_called_once()
    calls = audit.log_decision.call_args_list
    assert len(calls) == 1
    _, kw = calls[0]
    assert kw["action"] == "SWEEP"
    assert kw["jev_conviction"] == 0.72
    assert kw["detail"]["would_pass"] is False
    assert kw["detail"]["rule_reason"] is not None
    # No entry opened
    gov.validate_trade.assert_not_called()
    assert store.list_open() == []


def test_sweep_does_not_update_jev_last_called(tmp_path, mocker):
    """Sweep must not set jev_last_called, so real scoring isn't blocked."""
    cfg, gw, gov, ev, inst, audit, notif, store, cache = _setup_jev_and_rule(tmp_path, mocker)
    jev_last: dict[str, float] = {}
    jev_daily = [0]
    counter = mocker.Mock()

    _tick(cfg, gw, gov, ev, inst, audit, notif, store, ["RELIANCE"], None, cache,
          jev_last_called=jev_last, jev_daily_count=jev_daily, jev_counter=counter,
          sweep_pending={"RELIANCE"})

    assert "RELIANCE" not in jev_last


def test_sweep_increments_daily_count(tmp_path, mocker):
    """Sweep calls must count toward the daily cap."""
    cfg, gw, gov, ev, inst, audit, notif, store, cache = _setup_jev_and_rule(tmp_path, mocker)
    jev_daily = [0]
    counter = mocker.Mock()

    _tick(cfg, gw, gov, ev, inst, audit, notif, store, ["RELIANCE"], None, cache,
          jev_last_called={}, jev_daily_count=jev_daily, jev_counter=counter,
          sweep_pending={"RELIANCE"})

    assert jev_daily[0] == 1
    counter.increment.assert_called_once()


def test_sweep_respects_per_tick_cap(tmp_path, mocker):
    """Only sweep_per_tick_cap symbols scored per tick."""
    cfg, gw, gov, ev, inst, audit, notif, store, cache = _setup_jev_and_rule(tmp_path, mocker)
    cfg = replace(cfg, risk=replace(cfg.risk, sweep_per_tick_cap=1))
    inst.resolve.side_effect = [
        {"security_id": "100", "scrip_code": "NSE_100", "exchange": "NSE", "segment": "EQUITY", "name": "A"},
        {"security_id": "200", "scrip_code": "NSE_200", "exchange": "NSE", "segment": "EQUITY", "name": "B"},
    ]
    gw.get_quotes_batch.return_value = {
        "NSE_100": {"ltp": 100.0, "day_change_pct": 0.0, "volume": 0},
        "NSE_200": {"ltp": 200.0, "day_change_pct": 0.0, "volume": 0},
    }
    sweep_pending = {"SYM_A", "SYM_B"}
    jev_daily = [0]
    counter = mocker.Mock()

    _tick(cfg, gw, gov, ev, inst, audit, notif, store, ["SYM_A", "SYM_B"], None, cache,
          jev_last_called={}, jev_daily_count=jev_daily, jev_counter=counter,
          sweep_pending=sweep_pending)

    assert ev.evaluate_signal.call_count == 1


def test_sweep_stops_at_daily_cap(tmp_path, mocker):
    """When daily count is at sweep_cap_pct of daily_call_cap, no sweep fires."""
    cfg, gw, gov, ev, inst, audit, notif, store, cache = _setup_jev_and_rule(tmp_path, mocker)
    # daily_call_cap=500, sweep_cap_pct=0.80 → sweep_cap=400
    jev_daily = [400]
    counter = mocker.Mock()

    _tick(cfg, gw, gov, ev, inst, audit, notif, store, ["RELIANCE"], None, cache,
          jev_last_called={}, jev_daily_count=jev_daily, jev_counter=counter,
          sweep_pending={"RELIANCE"})

    ev.evaluate_signal.assert_not_called()


def test_sweep_skipped_when_recently_scored(tmp_path, mocker):
    """If jev_last_called has a recent entry, sweep skips that symbol."""
    import time as _time
    cfg, gw, gov, ev, inst, audit, notif, store, cache = _setup_jev_and_rule(tmp_path, mocker)
    jev_last = {"RELIANCE": _time.monotonic()}  # just scored
    jev_daily = [0]
    counter = mocker.Mock()

    _tick(cfg, gw, gov, ev, inst, audit, notif, store, ["RELIANCE"], None, cache,
          jev_last_called=jev_last, jev_daily_count=jev_daily, jev_counter=counter,
          sweep_pending={"RELIANCE"})

    ev.evaluate_signal.assert_not_called()


def test_sweep_disabled_when_pending_is_none(tmp_path, mocker):
    """sweep_pending=None (SWEEP_ENABLED=false) means no sweep calls."""
    cfg, gw, gov, ev, inst, audit, notif, store, cache = _setup_jev_and_rule(tmp_path, mocker)
    jev_daily = [0]
    counter = mocker.Mock()

    _tick(cfg, gw, gov, ev, inst, audit, notif, store, ["RELIANCE"], None, cache,
          jev_last_called={}, jev_daily_count=jev_daily, jev_counter=counter,
          sweep_pending=None)

    ev.evaluate_signal.assert_not_called()


def test_sweep_would_pass_false_with_noise_veto(tmp_path, mocker):
    """High conviction+confidence but noisy tape → would_pass=False."""
    cfg, gw, gov, ev, inst, audit, notif, store, cache = _setup_jev_and_rule(
        tmp_path, mocker, sweep_score=0.95, sweep_conf=0.95,
    )
    ev.evaluate_signal.return_value = ConvictionResult(
        score=0.95, confidence=0.95, raw={}, noise_score=0.85,
    )
    jev_daily = [0]
    counter = mocker.Mock()

    _tick(cfg, gw, gov, ev, inst, audit, notif, store, ["RELIANCE"], None, cache,
          jev_last_called={}, jev_daily_count=jev_daily, jev_counter=counter,
          sweep_pending={"RELIANCE"})

    calls = audit.log_decision.call_args_list
    assert len(calls) == 1
    _, kw = calls[0]
    assert kw["action"] == "SWEEP"
    assert kw["detail"]["would_pass"] is False


def test_sweep_high_conviction_never_opens_position(tmp_path, mocker):
    """Even if sweep Jev returns 0.95/0.95, no entry is opened."""
    cfg, gw, gov, ev, inst, audit, notif, store, cache = _setup_jev_and_rule(
        tmp_path, mocker, sweep_score=0.95, sweep_conf=0.95,
    )
    jev_daily = [0]
    counter = mocker.Mock()

    _tick(cfg, gw, gov, ev, inst, audit, notif, store, ["RELIANCE"], None, cache,
          jev_last_called={}, jev_daily_count=jev_daily, jev_counter=counter,
          sweep_pending={"RELIANCE"})

    calls = audit.log_decision.call_args_list
    assert len(calls) == 1
    _, kw = calls[0]
    assert kw["action"] == "SWEEP"
    assert kw["detail"]["would_pass"] is True
    gov.validate_trade.assert_not_called()
    gw.place_limit_order.assert_not_called()
    assert store.list_open() == []
