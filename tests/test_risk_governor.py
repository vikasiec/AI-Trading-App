import pytest

from jev_indstocks_trader.config import load_config
from jev_indstocks_trader.risk_governor import RiskGovernor, round_to_tick


def make_governor(mocker, funds=None, order_book=None):
    cfg = load_config()
    mocker.patch(
        "jev_indstocks_trader.risk_governor.requests.get",
        side_effect=lambda url, **kw: _fake_response(
            order_book if "order-book" in url else funds
        ),
    )
    gov = RiskGovernor(cfg.indstocks, cfg.risk, auth_headers_fn=lambda: {})
    return gov, cfg


class _fake_response:
    def __init__(self, payload):
        self._payload = payload or {"data": []}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_round_to_tick():
    assert round_to_tick(2450.03, 0.05) == 2450.05
    assert round_to_tick(2450.00, 0.05) == 2450.00
    assert round_to_tick(2450.024, 0.05) == 2450.0


def test_idempotency_rebuilds_from_order_book(mocker):
    order_book = {"data": [{"security_id": "2885", "order_epoch": 1_700_000_000}]}
    gov, _ = make_governor(mocker, order_book=order_book)
    assert "2885_28333333" in gov.executed_keys  # 1700000000 // 60


def test_drawdown_blocks_trade_and_triggers_flatten(mocker):
    funds = {"data": {"sod_balance": 100000, "realized_pnl": -3000, "unrealized_pnl": -500}}
    gov, cfg = make_governor(mocker, funds=funds)
    flatten_mock = mocker.patch.object(gov, "flatten_all")

    approved, reason = gov.validate_trade(
        security_id="2885", live_ltp=2450, scored_at_price=2450, conviction=0.9, confidence=0.9
    )

    assert approved is False
    assert reason == "daily_drawdown_limit_hit"
    flatten_mock.assert_called_once()


def test_slippage_collar_rejects_moved_price(mocker):
    funds = {"data": {"sod_balance": 100000, "realized_pnl": 0, "unrealized_pnl": 0}}
    gov, _ = make_governor(mocker, funds=funds)

    approved, reason = gov.validate_trade(
        security_id="2885", live_ltp=2500, scored_at_price=2450, conviction=0.9, confidence=0.9
    )

    assert approved is False
    assert "slippage" in reason


def test_low_conviction_rejected(mocker):
    funds = {"data": {"sod_balance": 100000, "realized_pnl": 0, "unrealized_pnl": 0}}
    gov, _ = make_governor(mocker, funds=funds)

    approved, reason = gov.validate_trade(
        security_id="2885", live_ltp=2450, scored_at_price=2450, conviction=0.5, confidence=0.9
    )

    assert approved is False
    assert reason == "below_conviction_or_confidence_threshold"


def test_duplicate_order_in_same_window_rejected(mocker):
    funds = {"data": {"sod_balance": 100000, "realized_pnl": 0, "unrealized_pnl": 0}}
    gov, _ = make_governor(mocker, funds=funds)

    first = gov.validate_trade("2885", 2450, 2450, 0.9, 0.9)
    # Approval alone must not burn the slot — a later failed place_order
    # should still be allowed to retry in the same minute.
    retry_before_mark = gov.validate_trade("2885", 2450, 2450, 0.9, 0.9)
    gov.mark_executed("2885")
    second = gov.validate_trade("2885", 2450, 2450, 0.9, 0.9)

    assert first[0] is True
    assert retry_before_mark[0] is True
    assert second == (False, "duplicate_in_window")


def test_size_order_respects_both_caps(mocker):
    gov, cfg = make_governor(mocker, funds={"data": {}})
    # 2% of 10,00,00,000 equity would be 20,00,000 -- capped at MAX_POSITION_CAPITAL_INR (50,000)
    qty = gov.size_order(equity=10_00_00_000, price=100)
    assert qty == 500  # 50,000 / 100

    # small equity: 2% of equity binds instead
    qty_small = gov.size_order(equity=10_000, price=100)
    assert qty_small == 2  # 2% of 10,000 = 200 / 100


def test_size_order_snaps_to_lot(mocker):
    gov, _ = make_governor(mocker, funds={"data": {}})
    assert gov.size_order(equity=1_000_000, price=100, lot_size=15) % 15 == 0
    assert gov.size_order(equity=1_000, price=100, lot_size=15) == 0
