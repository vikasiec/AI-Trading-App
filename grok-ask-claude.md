# Ask for Claude

Please fix the four open items in `grok-claude-fixes-review.md`. Do not add the live entry gate. Do not add the nightly log reader. Do not retune the 20-day, 4%, or 5-day parameters.

## 1. Size each trade from its own entry price

In `scripts/run_daily_backtest.py`, `qty` is `POSITION_CAPITAL // bars[-1].close` and that one quantity is reused for every trade in the file. A stock that rose or fell a lot then distorts the rupee result.

Change `run_backtest` so each fill uses `max(1, int(capital / entry_price))` from that trade's own next-bar open. Keep `POSITION_CAPITAL` as the rupee budget. Add a test where the last close is far from an earlier entry and the trade's quantity follows the entry, not the last close.

## 2. Escape ticker symbols

`fetch_yahoo` builds `.../chart/{symbol}{suffix}` without quoting. `M&M.NS` is parsed as a query string, and `^NSEI` needs the same treatment.

Quote the path segment (`M&M` → `M%26M`, `^NSEI` → `%5ENSEI`) before the request. Add a test that the URL requested for `M&M` contains `M%26M.NS` and does not contain a raw `&`.

## 3. Reject short downloads, and sort

An empty list still writes a header-only CSV and is counted as ok. A handful of rows is counted as ok too.

If a symbol returns fewer than 600 daily bars, do not write the file, print the failure, and count it as failed. Sort bars by `timestamp` before writing. Add a test for the short-file rejection and a test that out-of-order timestamps are written in order.

## 4. Stop calling zero a baseline

`run_daily_backtest.py` does not pass a baseline, so `baseline_delta` is the test profit minus zero.

Run the existing random-entry baseline on the same later-year trades, and print the strategy's test net minus that baseline. The pass rule stays what it is now: at least 30 later-year trades, and average net per trade above zero. Beating the random entry must not flip a negative strategy to a pass. Also print the average net next to the cost of one round trip at `POSITION_CAPITAL`, so a ₹1 edge is visible.

When those four have tests, run `python -m pytest tests/test_fetch_nse_daily.py tests/test_daily_backtest.py tests/test_hypothesis.py -q`.
