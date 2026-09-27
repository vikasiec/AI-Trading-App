"""Tests for ATR-based dynamic stop-loss and target."""
from jev_indstocks_trader.features import compute_atr_stops


class TestComputeAtrStops:
    def test_normal_atr(self):
        sl, tp = compute_atr_stops(
            atr_pct=0.01, stop_multiplier=2.0, target_multiplier=3.0,
            floor_pct=0.005, cap_pct=0.03,
        )
        assert sl == 0.02
        assert tp == 0.03

    def test_floor_clamp(self):
        sl, tp = compute_atr_stops(
            atr_pct=0.001, stop_multiplier=2.0, target_multiplier=3.0,
            floor_pct=0.005, cap_pct=0.03,
        )
        assert sl == 0.005
        assert tp > sl

    def test_cap_clamp(self):
        sl, tp = compute_atr_stops(
            atr_pct=0.05, stop_multiplier=2.0, target_multiplier=3.0,
            floor_pct=0.005, cap_pct=0.03,
        )
        assert sl == 0.03
        assert tp > sl

    def test_target_always_wider_than_stop(self):
        for atr in [0.002, 0.008, 0.015, 0.04]:
            sl, tp = compute_atr_stops(
                atr_pct=atr, stop_multiplier=2.0, target_multiplier=3.0,
                floor_pct=0.005, cap_pct=0.03,
            )
            assert tp > sl, f"Target {tp} should be > stop {sl} for ATR {atr}"

    def test_zero_atr_returns_floor(self):
        sl, tp = compute_atr_stops(
            atr_pct=0.0, stop_multiplier=2.0, target_multiplier=3.0,
            floor_pct=0.005, cap_pct=0.03,
        )
        assert sl == 0.005
