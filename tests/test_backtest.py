from datetime import datetime, timedelta

from jev_indstocks_trader.backtest import run_backtest
from jev_indstocks_trader.costs import CostRates
from jev_indstocks_trader.historical_data import Bar


def make_bars(closes: list[float]) -> list[Bar]:
    base = datetime(2026, 1, 2, 9, 15)
    bars = []
    for i, c in enumerate(closes):
        bars.append(Bar(
            timestamp=base + timedelta(minutes=i),
            open=c, high=c * 1.001, low=c * 0.999, close=c, volume=1000,
        ))
    return bars


def never_enter(bars):
    return False


def enter_on_first_bar_only(bars):
    return len(bars) == 1


def test_strategy_that_never_enters_produces_no_trades():
    bars = make_bars([100, 101, 102, 103, 104])
    result = run_backtest(bars, never_enter, qty=10)
    assert result.num_trades == 0
    assert result.win_rate is None
    assert "0 trades" in result.summary_text()


def test_target_hit_closes_position():
    closes = [100, 100, 105, 105, 105]
    bars = make_bars(closes)
    result = run_backtest(bars, enter_on_first_bar_only, qty=10, target_pct=0.02, stop_loss_pct=0.5)
    assert result.num_trades == 1
    assert result.trades[0].exit_reason == "target"
    assert result.trades[0].gross_pnl > 0


def test_stop_loss_hit_closes_position():
    closes = [100, 100, 90, 90, 90]
    bars = make_bars(closes)
    result = run_backtest(bars, enter_on_first_bar_only, qty=10, target_pct=0.5, stop_loss_pct=0.02)
    assert result.num_trades == 1
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].gross_pnl < 0


def test_time_exit_when_price_never_moves():
    closes = [100] * 20
    bars = make_bars(closes)
    result = run_backtest(
        bars, enter_on_first_bar_only, qty=10,
        target_pct=0.5, stop_loss_pct=0.5, max_hold_bars=5,
    )
    assert result.num_trades == 1
    assert result.trades[0].exit_reason == "time_exit"


def test_unresolved_position_force_closed_at_end_of_data():
    closes = [100, 100, 100.1, 100.1, 100.1]
    bars = make_bars(closes)
    result = run_backtest(bars, enter_on_first_bar_only, qty=10, target_pct=0.5, stop_loss_pct=0.5, max_hold_bars=1000)
    assert result.num_trades == 1
    assert result.trades[0].exit_reason == "end_of_data"


def test_net_pnl_is_less_than_gross_due_to_costs():
    closes = [100, 100, 105, 105, 105]
    bars = make_bars(closes)
    result = run_backtest(bars, enter_on_first_bar_only, qty=10, target_pct=0.02, stop_loss_pct=0.5)
    trade = result.trades[0]
    assert trade.net_pnl < trade.gross_pnl


def test_custom_cost_rates_applied():
    closes = [100, 100, 105, 105, 105]
    bars = make_bars(closes)
    high_brokerage = CostRates(brokerage_per_order=1000.0)
    result = run_backtest(bars, enter_on_first_bar_only, qty=10, target_pct=0.02,
                           stop_loss_pct=0.5, cost_rates=high_brokerage)
    assert result.trades[0].net_pnl < result.trades[0].gross_pnl - 1900


def test_summary_text_reports_correctly():
    closes = [100, 100, 105, 105, 105]
    bars = make_bars(closes)
    result = run_backtest(bars, enter_on_first_bar_only, qty=10, target_pct=0.02, stop_loss_pct=0.5)
    text = result.summary_text()
    assert "1 trades" in text
    assert "win_rate" in text
