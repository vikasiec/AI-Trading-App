"""Tests for daily backtest additions: intrabar exits, daily strategy,
train/test split, and the out-of-sample report.
"""
from datetime import date, datetime

import pytest

from jev_indstocks_trader.backtest import (
    BacktestResult,
    BacktestTrade,
    _check_intrabar_exit,
    run_backtest,
)
from jev_indstocks_trader.costs import CostRates
from jev_indstocks_trader.historical_data import Bar
from jev_indstocks_trader.hypothesis import (
    SplitReport,
    _binomial_ci,
    _split_trades,
    evaluate_out_of_sample,
)
from jev_indstocks_trader.positions import Position
from jev_indstocks_trader.strategies import daily_breakout_long


def _bar(day, o, h, l, c, v=1000):
    return Bar(
        timestamp=datetime(2024, 1, day, 9, 15),
        open=o, high=h, low=l, close=c, volume=v,
    )


def _pos(entry_price, stop_pct=0.04, target_pct=0.08):
    return Position(
        security_id="TEST", scrip_code="TEST", exchange="NSE",
        segment="EQUITY", product="CNC", qty=10,
        entry_price=entry_price,
        stop_loss_price=entry_price * (1 - stop_pct),
        target_price=entry_price * (1 + target_pct),
        opened_at=0, decision_id="test",
    )


# --- intrabar exit tests ---

class TestIntrabarExit:
    def test_stop_hit_on_low(self):
        pos = _pos(100.0, stop_pct=0.04)
        bar = _bar(2, 99.0, 101.0, 95.0, 98.0)
        reason, price = _check_intrabar_exit(pos, bar, max_hold_bars=5, elapsed_bars=1)
        assert reason == "stop_loss"
        assert price == pos.stop_loss_price

    def test_target_hit_on_high(self):
        pos = _pos(100.0, target_pct=0.08)
        bar = _bar(2, 102.0, 110.0, 101.0, 107.0)
        reason, price = _check_intrabar_exit(pos, bar, max_hold_bars=5, elapsed_bars=1)
        assert reason == "target"
        assert price == pos.target_price

    def test_both_hit_assumes_stop_first(self):
        pos = _pos(100.0, stop_pct=0.04, target_pct=0.08)
        bar = _bar(2, 100.0, 110.0, 90.0, 100.0)
        reason, price = _check_intrabar_exit(pos, bar, max_hold_bars=5, elapsed_bars=1)
        assert reason == "stop_loss"

    def test_gap_through_stop_fills_at_open(self):
        pos = _pos(100.0, stop_pct=0.04)
        bar = _bar(2, 93.0, 94.0, 92.0, 93.5)
        reason, price = _check_intrabar_exit(pos, bar, max_hold_bars=5, elapsed_bars=1)
        assert reason == "stop_loss"
        assert price == 93.0  # open is below stop, fills at open

    def test_time_exit_uses_close(self):
        pos = _pos(100.0)
        bar = _bar(2, 101.0, 102.0, 100.0, 101.5)
        reason, price = _check_intrabar_exit(pos, bar, max_hold_bars=5, elapsed_bars=5)
        assert reason == "time_exit"
        assert price == 101.5

    def test_no_exit_when_safe(self):
        pos = _pos(100.0, stop_pct=0.04, target_pct=0.08)
        bar = _bar(2, 101.0, 103.0, 99.0, 102.0)
        reason, price = _check_intrabar_exit(pos, bar, max_hold_bars=5, elapsed_bars=1)
        assert reason is None


class TestIntrabarBacktest:
    def test_intrabar_stop_fires_where_close_only_misses(self):
        bars = [
            _bar(1, 100, 101, 99, 100, v=2000),
            _bar(2, 100, 101, 99, 100.5, v=2000),
            _bar(3, 100.5, 101, 94, 99),  # low=94, close=99
        ]
        always = lambda b: len(b) == 1

        close_result = run_backtest(
            bars, always, qty=10, stop_loss_pct=0.04, target_pct=0.10,
            max_hold_bars=10, intrabar_exits=False,
        )
        intra_result = run_backtest(
            bars, always, qty=10, stop_loss_pct=0.04, target_pct=0.10,
            max_hold_bars=10, intrabar_exits=True,
        )
        # close-only: no exit (99 > 96 stop)
        assert close_result.num_trades == 0 or close_result.trades[0].exit_reason == "end_of_data"
        # intrabar: stop fires (low 94 < 96 stop)
        assert intra_result.num_trades == 1
        assert intra_result.trades[0].exit_reason == "stop_loss"


# --- daily breakout strategy tests ---

class TestDailyBreakout:
    def _rising_bars(self, n=25, base=100):
        bars = []
        for i in range(n):
            c = base + i * 0.5
            bars.append(Bar(
                timestamp=datetime(2024, 1, i + 1, 9, 15),
                open=c - 0.2, high=c + 0.3, low=c - 0.5, close=c,
                volume=1000 + i * 100,
            ))
        return bars

    def test_breakout_above_20_day_high_fires(self):
        bars = self._rising_bars(25)
        fn = daily_breakout_long(lookback=20, volume_multiple=1.0, index_bars=None)
        assert fn(bars) is True

    def test_not_enough_bars_returns_false(self):
        bars = self._rising_bars(15)
        fn = daily_breakout_long(lookback=20, volume_multiple=1.0, index_bars=None)
        assert fn(bars) is False

    def test_close_below_previous_high_returns_false(self):
        bars = self._rising_bars(25)
        bars[-1] = Bar(
            timestamp=bars[-1].timestamp,
            open=bars[-1].open, high=bars[-1].high,
            low=bars[-1].low, close=bars[5].close,
            volume=bars[-1].volume,
        )
        fn = daily_breakout_long(lookback=20, volume_multiple=1.0, index_bars=None)
        assert fn(bars) is False

    def test_index_falling_blocks_entry(self):
        bars = self._rising_bars(25)
        idx = {}
        for i, b in enumerate(bars):
            d = b.timestamp.date()
            idx[d] = Bar(
                timestamp=b.timestamp,
                open=200 - i, high=201 - i, low=199 - i, close=200 - i,
                volume=100000,
            )
        fn = daily_breakout_long(lookback=20, index_bars=idx, index_rising_days=5)
        assert fn(bars) is False

    def test_index_rising_allows_entry(self):
        bars = self._rising_bars(25)
        idx = {}
        for i, b in enumerate(bars):
            d = b.timestamp.date()
            idx[d] = Bar(
                timestamp=b.timestamp,
                open=200 + i, high=201 + i, low=199 + i, close=200 + i,
                volume=100000,
            )
        fn = daily_breakout_long(lookback=20, index_bars=idx, index_rising_days=5)
        assert fn(bars) is True


# --- train/test split tests ---

class TestSplitReport:
    def _make_trade(self, entry_date: str, net_pnl: float) -> BacktestTrade:
        return BacktestTrade(
            entry_time=f"{entry_date}T09:15:00",
            exit_time=f"{entry_date}T15:15:00",
            entry_price=100.0, exit_price=100.0 + net_pnl,
            qty=10, exit_reason="target",
            gross_pnl=net_pnl + 5, net_pnl=net_pnl,
        )

    def test_split_trades_by_cutoff(self):
        trades = [
            self._make_trade("2024-06-01", 50),
            self._make_trade("2024-12-01", 30),
            self._make_trade("2025-06-01", -20),
        ]
        train, test = _split_trades(trades, "2025-01-01")
        assert len(train) == 2
        assert len(test) == 1

    def test_end_of_data_trades_dropped(self):
        trades = [
            BacktestTrade(
                entry_time="2025-06-01T09:15:00", exit_time="2025-09-01T09:15:00",
                entry_price=100, exit_price=105, qty=10,
                exit_reason="end_of_data", gross_pnl=50, net_pnl=45,
            ),
        ]
        train, test = _split_trades(trades, "2024-01-01")
        assert len(test) == 0

    def test_too_few_test_trades_fails(self):
        trades = [self._make_trade("2025-06-01", 50) for _ in range(5)]
        report = evaluate_out_of_sample(
            "test", trades, total_bars=100,
            cutoff_iso="2025-01-01", min_test_trades=30,
        )
        assert report.passed is False
        assert "not enough evidence" in report.reason

    def test_positive_expectancy_passes(self):
        trades = [self._make_trade(f"2025-{(i % 9) + 1:02d}-{(i % 28) + 1:02d}", 50)
                  for i in range(40)]
        report = evaluate_out_of_sample(
            "test", trades, total_bars=1000,
            cutoff_iso="2025-01-01", min_test_trades=30,
        )
        assert report.passed is True

    def test_negative_expectancy_fails(self):
        trades = [self._make_trade(f"2025-{(i % 9) + 1:02d}-{(i % 28) + 1:02d}", -50)
                  for i in range(40)]
        report = evaluate_out_of_sample(
            "test", trades, total_bars=1000,
            cutoff_iso="2025-01-01", min_test_trades=30,
        )
        assert report.passed is False
        assert "negative expectancy" in report.reason


class TestBinomialCI:
    def test_zero_trades(self):
        lo, hi = _binomial_ci(0, 0.5)
        assert lo == 0.0 and hi == 1.0

    def test_reasonable_bounds(self):
        lo, hi = _binomial_ci(100, 0.6)
        assert 0.49 < lo < 0.6
        assert 0.6 < hi < 0.71


class TestBacktestResultStats:
    def test_avg_winner_and_loser(self):
        trades = [
            BacktestTrade("a", "b", 100, 110, 10, "target", 100, 80),
            BacktestTrade("c", "d", 100, 120, 10, "target", 200, 180),
            BacktestTrade("e", "f", 100, 95, 10, "stop_loss", -50, -60),
        ]
        r = BacktestResult(trades=trades, total_bars=100)
        assert r.avg_winner == pytest.approx(130.0)  # (80+180)/2
        assert r.avg_loser == pytest.approx(-60.0)
        assert r.avg_net_per_trade == pytest.approx((80 + 180 - 60) / 3)


class TestPositionCapitalSizing:
    def test_each_trade_sized_from_its_own_entry_price(self):
        bars = [
            _bar(1, 50, 51, 49, 50, v=2000),
            _bar(2, 50, 51, 49, 50, v=2000),
            _bar(3, 50, 51, 49, 50.5, v=2000),
            _bar(4, 50.5, 55, 50, 52, v=2000),   # target hit
            _bar(5, 100, 101, 99, 100, v=2000),
            _bar(6, 100, 101, 99, 100, v=2000),
            _bar(7, 100, 101, 99, 100.5, v=2000),
            _bar(8, 100.5, 110, 99, 105, v=2000),  # target hit
        ]
        enter_every_4 = lambda b: len(b) in (2, 6)

        result = run_backtest(
            bars, enter_every_4,
            stop_loss_pct=0.10, target_pct=0.04,
            max_hold_bars=10,
            intrabar_exits=True,
            position_capital=1000.0,
        )
        assert result.num_trades == 2
        assert result.trades[0].qty == 20   # 1000 // 50
        assert result.trades[1].qty == 10   # 1000 // 100

    def test_zero_position_capital_uses_fixed_qty(self):
        bars = [_bar(d, 100, 101, 99, 100, v=2000) for d in range(1, 6)]
        enter_first = lambda b: len(b) == 1

        result = run_backtest(
            bars, enter_first, qty=7,
            stop_loss_pct=0.50, target_pct=0.50,
            max_hold_bars=10,
            position_capital=0.0,
        )
        assert result.num_trades == 1
        assert result.trades[0].qty == 7


class TestSplitReportNoCostLine:
    def test_report_shows_avg_cost_per_trade(self):
        trades = [
            BacktestTrade("2025-06-01T09:15:00", "2025-06-05T09:15:00",
                          100, 105, 10, "target", 50, 40),
        ] * 35
        report = evaluate_out_of_sample(
            "test", trades, total_bars=1000,
            cutoff_iso="2025-01-01", min_test_trades=30,
        )
        text = report.summary_text()
        assert "avg_cost/trade" in text
        assert "baseline_delta" not in text
