# Review of Claude's daily-data and out-of-sample work

Recheck after the four-item fix pass. `tests/test_fetch_nse_daily.py`, `tests/test_daily_backtest.py`, and `tests/test_hypothesis.py`: **42 passed**.

## Fixed

1. **Share count.** `run_backtest` sizes each trade as `floor(position_capital / that trade's entry price)`. `run_daily_backtest.py` passes `POSITION_CAPITAL` and no longer uses the last close. `test_each_trade_sized_from_its_own_entry_price` checks a ₹50 entry gets 20 shares and a ₹100 entry gets 10, on the same capital.

2. **Ticker escaping.** `fetch_yahoo` percent-encodes the path. `M&M` is requested as `M%26M`. The same function encodes `^NSEI`. A test asserts the captured URL contains `M%26M` and not a raw `&`.

3. **Short files and order.** A symbol with fewer than `MIN_BARS` bars is not written and is counted as failed. Rows are sorted by timestamp before they are returned. A test feeds reversed timestamps and checks they come out in order.

4. **The fake baseline is gone.** `baseline_delta` is no longer printed. A pass is still "at least 30 later-year trades and average net above zero." A negative average cannot pass. The report prints `avg_net/trade` next to `avg_cost/trade`.

The live sit-out gate and the nightly log note are still absent. Leave them off until this report has been run on real files.

## Leftovers closed

- `MIN_BARS` is **600**. A short download is not written. `test_main_rejects_short_history_and_writes_nothing` covers that.
- The daily report prints the later-year random-entry net and the difference. That line does not decide pass or fail. A negative average still fails.

Do not tune the 20-day lookback, the 4% stop, or the 5-day hold after seeing the report and then keep the same cutoff.
