import time

from jev_indstocks_trader.exits import ExitManager, determine_exit_reason
from jev_indstocks_trader.positions import Position, PositionStore


def make_position(**overrides):
    defaults = dict(
        security_id="2885",
        scrip_code="NSE_2885",
        exchange="NSE",
        segment="EQUITY",
        product="INTRADAY",
        qty=10,
        entry_price=2450.0,
        stop_loss_price=2425.5,   # 1% below entry
        target_price=2499.0,      # 2% above entry
        opened_at=time.time(),
        decision_id="2885_123456",
    )
    defaults.update(overrides)
    return Position(**defaults)


# -- pure exit-reason logic --------------------------------------------------

def test_no_exit_when_price_in_range():
    pos = make_position()
    reason = determine_exit_reason(pos, live_ltp=2460.0, now=time.time(), max_hold_seconds=3600)
    assert reason is None


def test_stop_loss_triggers():
    pos = make_position()
    reason = determine_exit_reason(pos, live_ltp=2420.0, now=time.time(), max_hold_seconds=3600)
    assert reason == "stop_loss"


def test_target_triggers():
    pos = make_position()
    reason = determine_exit_reason(pos, live_ltp=2500.0, now=time.time(), max_hold_seconds=3600)
    assert reason == "target"


def test_time_exit_triggers():
    pos = make_position(opened_at=time.time() - 4000)
    reason = determine_exit_reason(pos, live_ltp=2460.0, now=time.time(), max_hold_seconds=3600)
    assert reason == "time_exit"


def test_stop_loss_takes_priority_over_time_exit_when_both_true():
    pos = make_position(opened_at=time.time() - 4000)
    reason = determine_exit_reason(pos, live_ltp=2420.0, now=time.time(), max_hold_seconds=3600)
    assert reason == "stop_loss"


# -- ExitManager wiring (paper mode -- no real orders) --------------------------------------------------

def test_paper_mode_exit_updates_audit_and_clears_position(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    store.add(make_position())

    gateway = mocker.Mock()
    gateway.get_ltp.return_value = 2500.0  # hits target
    audit = mocker.Mock()

    manager = ExitManager(
        gateway=gateway, store=store, audit=audit, max_hold_minutes=375, paper_trading=True
    )
    manager.check_and_exit_all()

    gateway.place_limit_order.assert_not_called()  # paper mode: no real order
    audit.update_outcome.assert_called_once()
    assert store.list_open() == []


def test_live_mode_exit_places_sell_order(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    store.add(make_position())

    gateway = mocker.Mock()
    gateway.get_ltp.return_value = 2420.0  # hits stop loss
    audit = mocker.Mock()

    manager = ExitManager(
        gateway=gateway, store=store, audit=audit, max_hold_minutes=375, paper_trading=False
    )
    manager.check_and_exit_all()

    gateway.place_market_order.assert_called_once()
    kwargs = gateway.place_market_order.call_args.kwargs
    assert kwargs["side"] == "SELL"
    assert kwargs["qty"] == 10
    gateway.place_limit_order.assert_not_called()
    assert store.list_open() == []


def test_live_mode_target_exit_places_limit_order(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    store.add(make_position())

    gateway = mocker.Mock()
    gateway.get_ltp.return_value = 2500.0  # hits target
    audit = mocker.Mock()

    manager = ExitManager(
        gateway=gateway, store=store, audit=audit, max_hold_minutes=375, paper_trading=False
    )
    manager.check_and_exit_all()

    gateway.place_limit_order.assert_called_once()
    gateway.place_market_order.assert_not_called()
    assert store.list_open() == []


def test_failed_exit_order_keeps_position_open(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    store.add(make_position())

    gateway = mocker.Mock()
    gateway.get_ltp.return_value = 2420.0
    gateway.place_market_order.side_effect = RuntimeError("network error")
    audit = mocker.Mock()
    notifier = mocker.Mock()

    manager = ExitManager(
        gateway=gateway, store=store, audit=audit, max_hold_minutes=375,
        notifier=notifier, paper_trading=False,
    )
    manager.check_and_exit_all()

    assert len(store.list_open()) == 1  # position NOT cleared -- will retry next tick
    audit.update_outcome.assert_not_called()
    notifier.send_critical_alert.assert_called_once()


def test_gtt_cancelled_on_live_exit(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    store.add(make_position(decision_id="2885_1", gtt_id="GTT999"))

    gateway = mocker.Mock()
    gateway.get_ltp.return_value = 2420.0  # hits stop loss
    audit = mocker.Mock()
    cancel_mock = mocker.patch("jev_indstocks_trader.exits.gtt_orders.cancel_gtt")

    manager = ExitManager(
        gateway=gateway, store=store, audit=audit, max_hold_minutes=375, paper_trading=False,
        indstocks_cfg=object(), auth_headers_fn=lambda: {},
    )
    manager.check_and_exit_all()

    cancel_mock.assert_called_once()
    assert cancel_mock.call_args.args[-1] == "GTT999"


def test_no_gtt_cancel_when_position_has_no_gtt_id(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    store.add(make_position())  # no gtt_id -- defaults to None

    gateway = mocker.Mock()
    gateway.get_ltp.return_value = 2420.0
    audit = mocker.Mock()
    cancel_mock = mocker.patch("jev_indstocks_trader.exits.gtt_orders.cancel_gtt")

    manager = ExitManager(
        gateway=gateway, store=store, audit=audit, max_hold_minutes=375, paper_trading=False,
        indstocks_cfg=object(), auth_headers_fn=lambda: {},
    )
    manager.check_and_exit_all()

    cancel_mock.assert_not_called()


def test_no_gtt_cancel_in_paper_mode(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    store.add(make_position(decision_id="2885_1", gtt_id="GTT999"))

    gateway = mocker.Mock()
    gateway.get_ltp.return_value = 2500.0  # hits target
    audit = mocker.Mock()
    cancel_mock = mocker.patch("jev_indstocks_trader.exits.gtt_orders.cancel_gtt")

    manager = ExitManager(
        gateway=gateway, store=store, audit=audit, max_hold_minutes=375, paper_trading=True,
    )
    manager.check_and_exit_all()

    cancel_mock.assert_not_called()  # paper mode never touches real GTT orders


# -- PositionStore persistence --------------------------------------------------

def test_position_store_persists_across_instances(tmp_path):
    path = tmp_path / "positions.json"
    store1 = PositionStore(path)
    store1.add(make_position())

    store2 = PositionStore(path)  # simulates a process restart
    assert store2.has_open("2885")
    assert len(store2.list_open()) == 1


def test_position_store_remove(tmp_path):
    path = tmp_path / "positions.json"
    store = PositionStore(path)
    store.add(make_position())
    store.remove("2885")
    assert store.list_open() == []
    assert not store.has_open("2885")
