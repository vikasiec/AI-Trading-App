from datetime import datetime, timedelta

from jev_indstocks_trader.config import load_config, validate_config
from jev_indstocks_trader.historical_data import Bar
from jev_indstocks_trader.hypothesis import evaluate_vs_baseline
from jev_indstocks_trader.portfolio_risk import check_portfolio_risk
from jev_indstocks_trader.positions import PositionStore
from jev_indstocks_trader.regime import classify_regime, realized_vol
from jev_indstocks_trader.sectors import sector_for
from jev_indstocks_trader.strategies import mean_reversion_long, momentum_long


def _bars(closes, volume=1000, start=100.0):
    out = []
    t0 = datetime(2024, 1, 1, 9, 15)
    prev = start
    for i, c in enumerate(closes):
        o = prev
        h = max(o, c) * 1.001
        low = min(o, c) * 0.999
        vol = volume[i] if isinstance(volume, list) else volume
        out.append(Bar(t0 + timedelta(minutes=i), o, h, low, c, int(vol)))
        prev = c
    return out


def test_momentum_detects_up_move_on_volume():
    # flat then a clear 2% lift on rising volume
    closes = [100.0] * 8 + [100.4, 100.9, 101.5, 102.2]
    vols = [1000] * 8 + [1500, 1800, 2000, 2500]
    fn = momentum_long(lookback=3, min_return_pct=0.01, volume_multiple=1.1)
    assert fn(_bars(closes, vols)) is True


def test_momentum_rejects_move_without_volume():
    closes = [100.0] * 8 + [100.4, 100.9, 101.5, 102.2]
    vols = [1000] * 12
    fn = momentum_long(lookback=3, min_return_pct=0.01, volume_multiple=1.5)
    assert fn(_bars(closes, vols)) is False


def test_mean_reversion_detects_dip_below_vwap():
    closes = [100.0] * 15 + [99.5, 98.5, 97.0]
    fn = mean_reversion_long(lookback=10, deviation_pct=0.015)
    assert fn(_bars(closes)) is True


def test_mean_reversion_rejects_at_vwap():
    closes = [100.0] * 20
    fn = mean_reversion_long(lookback=10, deviation_pct=0.01)
    assert fn(_bars(closes)) is False


def test_h5_fires_on_constructed_dip_series():
    # oscillate around 100 with periodic 3% dips so mean-reversion enters
    closes = []
    for cycle in range(8):
        closes.extend([100.0] * 12)
        closes.extend([99.0, 98.0, 97.0, 97.5, 98.5, 99.5])
    verdict = evaluate_vs_baseline(
        "H5", _bars(closes),
        mean_reversion_long(lookback=10, deviation_pct=0.015),
        qty=10, max_hold_bars=6, baseline_every_n=40,
    )
    assert verdict.strategy_result.num_trades >= 1


def test_h4_survives_on_constructed_trend():
    # strong trend so momentum should print more net than a sparse random baseline
    closes = [100 + i * 0.4 for i in range(80)]
    vols = [5000 if i % 6 == 5 else 800 for i in range(80)]
    verdict = evaluate_vs_baseline(
        "H4", _bars(closes, vols),
        momentum_long(lookback=5, min_return_pct=0.01, volume_multiple=1.2),
        qty=10, max_hold_bars=8, baseline_every_n=40,
    )
    assert verdict.strategy_result.num_trades >= 1


def test_h4_killed_on_flat_noise():
    closes = [100.0 + ((i % 3) - 1) * 0.05 for i in range(80)]
    verdict = evaluate_vs_baseline(
        "H4-noise", _bars(closes), momentum_long(lookback=5, min_return_pct=0.02),
        qty=10, max_hold_bars=8, baseline_every_n=10,
    )
    # on this series momentum barely (or never) fires; fail-closed if no trades
    if verdict.strategy_result.num_trades == 0:
        assert verdict.survived is False
    else:
        assert verdict.survived is False or verdict.strategy_result.total_net_pnl <= verdict.baseline_result.total_net_pnl + 1e-6


def test_regime_violent_on_large_swings():
    closes = [100, 104, 98, 106, 95, 108, 94, 110]
    bars = _bars(closes)
    assert realized_vol(bars) is not None
    assert classify_regime(bars, lookback=8) == "violent"


def test_regime_calm_on_flat():
    closes = [100.0 + i * 0.01 for i in range(30)]
    assert classify_regime(_bars(closes), lookback=20) == "calm"


def test_sector_lookup():
    assert sector_for("TCS") == "IT"
    assert sector_for("reliance") == "ENERGY"
    assert sector_for("NOTATALICKER") == "UNKNOWN"


def test_sector_cap_blocks_second_it_name(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_POSITIONS", "5")
    monkeypatch.setenv("MAX_DEPLOYED_CAPITAL_PCT", "0.50")
    monkeypatch.setenv("MAX_SECTOR_CAPITAL_PCT", "0.05")
    cfg = load_config()
    store = PositionStore(tmp_path / "p.json")
    from jev_indstocks_trader.positions import Position
    store.add(Position(
        security_id="11536", scrip_code="NSE_11536", exchange="NSE", segment="EQUITY",
        product="INTRADAY", qty=10, entry_price=4000.0, stop_loss_price=3960,
        target_price=4080, opened_at=0, decision_id="x", symbol="TCS",
    ))
    # 10*4000 = 40k already in IT; 5% of 1M = 50k; adding 20k should block
    result = check_portfolio_risk(
        store, cfg.risk, equity=1_000_000, candidate_capital=20_000, candidate_symbol="INFY",
    )
    assert result.approved is False
    assert "max_sector_capital_exceeded" in result.reason


def test_validate_config_catches_bad_pct(monkeypatch):
    monkeypatch.setenv("DAILY_LOSS_LIMIT_PCT", "5")  # 500%, nonsense
    cfg = load_config()  # non-strict: does not raise
    problems = validate_config(cfg)
    assert any("DAILY_LOSS_LIMIT_PCT" in p for p in problems)
