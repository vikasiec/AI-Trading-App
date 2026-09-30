# Sonnet review fixes — 2026-09-29

Source: `docs/code-review-sonnet55-2026-09-29.md`. Local tree only. Nothing was deployed, and `PAPER_TRADING` was not changed. Stop, target, and the Jev conviction cutoff were not retuned. The morning job is still `scripts/scan_premarket.py` writing `watchlist_200ma.json`.

Unit tests: `python -m pytest tests/ -q` — 304 passed. Typecheck, lint, format, and build gates are still blank in `Claude.md`, so they were skipped.

## Closed

- H1. A recovered heartbeat flattens only after this outage already flattened. The next outage may re-check once.
- H2. `flatten_all` returns `FlattenResult`. One failed request does not skip the rest. Delivery products and ids outside the local book are not sold. The kill switch clears a local position only when that id is not still open, and writes `exit_reason=kill_switch`.
- H3. A resting target limit is cancelled for a stop, time exit, session close, kill switch, or a partial status. Filled shares are booked first.
- H4. A buy or sell that throws before an id is saved looks up a recent order. A real order id is adopted. No id leaves the old retry.
- H5. Heartbeat, reconcile, exits, session flatten, and Telegram sends no longer take down the loop.
- H6. Boolean env values strip and accept `1/true/yes/on`. `ENTRY_MODE` and a non-positive tick size fail startup. `PAPER_TRADING=false` still warns and does not block, so an intentional live start remains possible.
- H7. `/halt CONFIRM` sets a thread-safe halt flag before the callback. New entries stop. Exits still run. The flag clears on process restart.
- M1. Drawdown uses start-of-day balance, then net, then available. The live loop checks it once per tick. Paper does not halt from the real account's funds.
- M2. Orders with no timestamp are not treated as this minute. A full stop-loss close blocks that security for the rest of the IST day.
- M3. A full book skips the paid Jev call.
- M4. Minute bars reset on the date change.
- M5. An expired or rejected order that already filled shares is a partial fill.
- M6. A forced exit with no price leaves the position for the next tick.
- M7. GTT ids are cancelled on booked, partial, and timeout-fill paths.
- M8. A live sell reads broker quantity first. Unreadable: no sell. Already flat: clear local, no sell. Smaller: sell the broker quantity.
- M9. The watchlist reload docstring already matched the code.
- M10. A yfinance failure raises. The pre-market scan exits without replacing `watchlist_200ma.json`.
- M11. News fetches use an 8 second timeout. A symbol matches on a word boundary.
- M12. Log lines redact `bot<id>:<token>`.
- M13. The status pulse counts `BUY`, reads `skip_reason`, and treats the positions file as a dict of security ids.
- M14. Accuracy reads prices from the row or from `detail`, and the EOD close is the row for that date.
- M15. `BAJAJ-AUTO` stays whole. BE/BZ series are not tradable. Limit prices use the instrument tick when the entry path has one.
- M16. Weekday NSE cash holidays in 2026 are closed. Muhurat on Sunday 8 Nov 2026 is not a normal session. 2027 dates are not in the set.
- L1. Relative-strength lookback is `period_days + 1`, and the stock and Nifty share one index. Same window in `scan_uptrend.py`.
- L2. A later exit slice charges one brokerage order. A backtest share priced above the capital budget is skipped.
- L3. The token temp file is created with mode `0o600`.
- L4. Unknown position fields are dropped. Open-position reads return copies.
- L5. Kill-switch clears write an audit outcome.
- L7. Package version is `0.19.0`. `yfinance` and `pandas` are declared. They were not installed from this session.

## Left on purpose

- L6. `jev-trader-key.pem` is still in the project root. It is gitignored. It was not opened, moved, or deleted. Move that one copy off OneDrive yourself if you do not want it synced. The stray `~/.indstocks/session_token.json` inside the repo was deleted without being read. `~/.indstocks` in your real home was not touched.
- L8. `JevEvaluator.passes_threshold` is unused. The governor still owns the cutoff, so the helper was not wired in and the cutoff was not changed. Renamed tickers in the scanner pool were not swapped.
- The uptrend screen is still not the morning job.
