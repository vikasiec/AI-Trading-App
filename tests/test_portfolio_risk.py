from jev_indstocks_trader.config import load_config
from jev_indstocks_trader.portfolio_risk import check_portfolio_risk
from jev_indstocks_trader.positions import Position, PositionStore


def make_position(security_id="2885", entry_price=2450.0, qty=10):
    return Position(
        security_id=security_id, scrip_code=f"NSE_{security_id}", exchange="NSE",
        segment="EQUITY", product="INTRADAY", qty=qty, entry_price=entry_price,
        stop_loss_price=entry_price * 0.99, target_price=entry_price * 1.02,
        opened_at=0.0, decision_id=f"{security_id}_1",
    )


def test_approves_when_room_available(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_POSITIONS", "3")
    monkeypatch.setenv("MAX_DEPLOYED_CAPITAL_PCT", "0.10")
    cfg = load_config()
    store = PositionStore(tmp_path / "positions.json")

    result = check_portfolio_risk(store, cfg.risk, equity=1_000_000, candidate_capital=50_000)
    assert result.approved is True


def test_blocks_when_max_concurrent_positions_reached(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_POSITIONS", "2")
    monkeypatch.setenv("MAX_DEPLOYED_CAPITAL_PCT", "0.50")
    cfg = load_config()
    store = PositionStore(tmp_path / "positions.json")
    store.add(make_position("2885"))
    store.add(make_position("11536"))

    result = check_portfolio_risk(store, cfg.risk, equity=1_000_000, candidate_capital=10_000)
    assert result.approved is False
    assert "max_concurrent_positions_reached" in result.reason


def test_blocks_when_deployed_capital_exceeded(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_POSITIONS", "10")
    monkeypatch.setenv("MAX_DEPLOYED_CAPITAL_PCT", "0.10")
    cfg = load_config()
    store = PositionStore(tmp_path / "positions.json")
    # 90,000 already deployed on 1,000,000 equity (9%)
    store.add(make_position("2885", entry_price=9000, qty=10))

    # candidate would push it to 9% + 5% = 14%, over the 10% cap
    result = check_portfolio_risk(store, cfg.risk, equity=1_000_000, candidate_capital=50_000)
    assert result.approved is False
    assert "max_deployed_capital_exceeded" in result.reason


def test_blocks_on_zero_equity(tmp_path):
    cfg = load_config()
    store = PositionStore(tmp_path / "positions.json")
    result = check_portfolio_risk(store, cfg.risk, equity=0, candidate_capital=1000)
    assert result.approved is False
    assert result.reason == "invalid_equity"
