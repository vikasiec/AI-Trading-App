import time

from jev_indstocks_trader.positions import Position, PositionStore
from jev_indstocks_trader.reconciliation import ReconciliationService


def make_position(security_id):
    return Position(
        security_id=security_id, scrip_code=f"NSE_{security_id}", exchange="NSE",
        segment="EQUITY", product="INTRADAY", qty=10, entry_price=100.0,
        stop_loss_price=99.0, target_price=102.0, opened_at=time.time(),
        decision_id=f"{security_id}_1",
    )


def test_reports_ok_when_states_match(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    store.add(make_position("2885"))

    gateway = mocker.Mock()
    gateway.get_positions.return_value = [{"security_id": "2885", "net_qty": 10}]

    svc = ReconciliationService(gateway, store, interval_s=60)
    report = svc.reconcile()

    assert report.ok is True
    assert report.untracked_broker_positions == []
    assert report.missing_broker_positions == []


def test_detects_untracked_broker_position(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")  # nothing tracked locally

    gateway = mocker.Mock()
    gateway.get_positions.return_value = [{"security_id": "2885", "net_qty": 10}]
    notifier = mocker.Mock()

    svc = ReconciliationService(gateway, store, interval_s=60, notifier=notifier)
    report = svc.reconcile()

    assert report.ok is False
    assert report.untracked_broker_positions == ["2885"]
    notifier.send_critical_alert.assert_called_once()


def test_detects_missing_broker_position(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    store.add(make_position("2885"))  # tracked locally

    gateway = mocker.Mock()
    gateway.get_positions.return_value = []  # broker shows it closed

    svc = ReconciliationService(gateway, store, interval_s=60)
    report = svc.reconcile()

    assert report.ok is False
    assert report.missing_broker_positions == ["2885"]


def test_zero_qty_broker_positions_ignored(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    gateway = mocker.Mock()
    gateway.get_positions.return_value = [{"security_id": "2885", "net_qty": 0}]  # flat, not open

    svc = ReconciliationService(gateway, store, interval_s=60)
    report = svc.reconcile()

    assert report.ok is True


def test_due_respects_interval(tmp_path, mocker):
    store = PositionStore(tmp_path / "positions.json")
    gateway = mocker.Mock()
    gateway.get_positions.return_value = []

    svc = ReconciliationService(gateway, store, interval_s=100)
    assert svc.due() is True  # never run yet
    svc.reconcile()
    assert svc.due() is False  # just ran, interval not elapsed
