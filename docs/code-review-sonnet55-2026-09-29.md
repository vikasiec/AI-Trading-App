# Codebase Review — Sonnet 5.5 (2026-09-29)

Scope: every file in `src/jev_indstocks_trader/` and `scripts/`, plus tests.
273 tests pass. Typecheck, lint and format gates not configured (skipped).

Most live-mode findings are latent while PAPER_TRADING=true.

## Invariant Check

- PAPER_TRADING: strict true/false parsing, defaults true, Dockerfile pins true. No code flips it. Weak spot: `PAPER_TRADING=false` only warns, doesn't block.
- .env: gitignored, never committed, never opened during review.
- Jev cap/rate limit: enforced correctly. `retry_with_backoff(max_retries=1)` = one attempt, no double billing.
- Prices: float throughout. `round_to_tick` uses Decimal internally but returns float.
- Token cache: atomic tmp+replace (auth.py:57-62).

---

## HIGH (live-money risk)

### H1. heartbeat.py:81-88 — Single transient blip flattens everything on recovery

On the first successful ping after any failure, `_flatten_confirmed_clean` is never set True, so the condition is always true. One failed /user/profile call followed by success triggers full kill switch (close all positions, clear store).

**Fix:** Only flatten on recovery if `_flattened_this_outage` is already True.

### H2. risk_governor.py:167-244 — Kill switch reports success even when flatten failed

`flatten_all()` catches its own `RequestException` and never raises. So `flatten_ok` in `_kill_switch` is always True. GTTs are cancelled and local positions removed even when broker positions are still open. The Telegram "INCOMPLETE" branch (main.py:100-101) is dead code.

Additional gaps in `flatten_all`:
- A single exception in the cancel or flatten loop aborts all remaining orders/positions.
- Only cancels orders in exact-case `O-PENDING/OPEN/PENDING/TRIGGER_PENDING`. PARTIALLY FILLED orders keep filling.
- Flattens every broker position, including manual or non-bot positions.

**Fix:** Return/raise on leftover positions. Only clear store for confirmed-flat positions. Wrap each request in its own try/except.

### H3. exits.py:193-198 — Resting target LIMIT sell blocks stop-loss

Target exits use LIMIT SELL at LTP. If it doesn't fill within 3s, `pending_exit_order_id` is stored. On later ticks, `_execute_exit` finds the prior order still open and returns "still in-flight" — even when the new reason is `stop_loss` or `time_exit`. Price reversal after target order = stop never fires until 15:15 flatten.

PARTIALLY FILLED prior order stays in-flight forever (neither completed nor dead).

**Fix:** If new reason is a market-type exit and prior order is still open, cancel it first (booking any partial fill), then send MARKET order.

### H4. No order idempotency on the send path

- Entry (main.py:676-681): Any exception from `place_limit_order` (including ReadTimeout after broker accepted) = treated as "not placed". `mark_executed` not called. Next rescore can buy again.
- Exit (exits.py:234-244): Exception leaves `pending_exit_order_id` unset, so next tick sends another SELL for `position.qty` — can double-sell into a short.
- Governor's idempotency key only set after ACK.

**Fix:** On ambiguous POST failure, query order book (by symbol and time) before retrying.

### H5. main.py:220-247 — Uncaught exceptions kill the trading loop

Unwrapped calls: `heartbeat.ping()`, `reconciler.reconcile()`, `exit_manager.check_and_exit_all()`, `force_exit_all()`, `notifier.send_critical_alert()`.

Telegram sends unguarded at exits.py:362, heartbeat.py:63, main.py:554.

A Telegram outage or broker 5xx raises out of `run_loop`. Process exits with open positions and no exit management until systemd restarts. `StartLimitBurst=8` per 300s can give up.

**Fix:** Wrap each per-tick subsystem call in try/except with logging. Make `TelegramAlertNotifier.send_*` swallow exceptions internally.

### H6. config.py:151-189 — Config validation is warn-only

`load_config()` runs non-strict; only warnings containing "placeholder" block startup.

Configs that only warn but proceed:
- `DAILY_LOSS_LIMIT_PCT=2` (unreachable drawdown limit)
- `STOP_LOSS_PCT<=0`

Not validated at all:
- `ENTRY_MODE` — typo like "jev_and_rules" silently drops rule gate (falls into jev-only branch of `combine_votes`)
- `DEFAULT_TICK_SIZE_INR<=0` — raises Decimal error in `round_to_tick`
- Boolean parsing is `.lower()=="true"` with no strip — "True " or "1" silently disables JEV_NOISE_VETO

**Fix:** Run `validate_config` strictly. Validate enum and tick size. Strip booleans.

### H7. telegram_alerts.py:69-71 — /halt doesn't actually halt

Nothing persists a "halted" state. Loop opens new positions on next tick after /halt. Also thread-unsafe: `_kill_switch` runs on Telegram polling thread while main thread may be mid-order (double-sell or Position added after store cleared).

**Fix:** Add thread-safe `halted` flag checked in tick and exit loops. Reply with actual outcome.

---

## MEDIUM

### M1. risk_governor.py:87-105 — Drawdown check is weak

Only runs at `validate_trade`, not on open P&L. Equity is `available_balance` (shrinks as capital deployed = inflated percentage). In paper mode, /funds reflects real account. Once tripped, every candidate triggers `flatten_all` each tick.

### M2. Re-entry churn

Idempotency keys are per-minute (risk_governor.py:45-48). No cooldown after stop-out — bot re-buys same symbol at next Jev rescore. Startup rebuild uses `now` for rows without timestamp.

### M3. main.py:607-615 — Wasted Jev calls

Portfolio-risk check (max concurrent positions, capital caps) runs AFTER the paid Jev call. With position cap full, every tick still scores every symbol up to daily cap.

**Fix:** Pre-check `len(open) >= max_concurrent_positions` before calling Jev.

### M4. main.py:188 — BarCache never resets across days

Only `or_book` and `gap_book` roll over. Overnight data flows into ATR, regime, ret_20, VWAP distance. ATR-based stops and regime label are distorted at open.

### M5. BUY/SELL timeout under-counts fills

After cancel, qty from stale `filled_qty`. Shares filled between last poll and cancel are untracked. `EXPIRED` in REJECTED_STATUSES means partially filled expired LIMIT = treated as no fill.

### M6. exits.py:90-95 — force_exit_all falls back to entry price

Paper P&L booked as 0 when LTP unavailable. In flatten phase, `force_exit_all` + `flatten_all` on retry ticks can double-sell.

### M7. GTT cleanup gaps

`cancel_gtt` skipped in `_book_pending_fill` and timeout-fill/partial paths. If GTT fires at exchange, local position still exists and bot sells again.

### M8. Stale positions across days

main.py:156-158 only logs "resuming". `_execute_exit` sells `position.qty` blindly. If broker auto-squared-off an INTRADAY position or GTT fired, bot opens a short.

**Fix:** Verify against broker `net_qty` before any SELL.

### M9. watchlist.py — Reload docstring stale

Docstring says it keeps `last_mtime` on failure, but code now returns `stat.st_mtime` (changed per Grok review to prevent log spam). Docstring needs updating.

### M10. scan_premarket.py:46-63 — Scanner failure wipes watchlist

If yfinance fails, both scanners return `[]` and empty watchlist is written, overwriting yesterday's list. No data-failure guard (unlike scan_uptrend.py:285). watchlist_200ma.py has the same problem (`min_stocks` defaults to 0).

### M11. news_feed.py:42-52 — feedparser has no timeout

Stalled feed freezes single-threaded trading loop including exits. feedparser doesn't raise on failure, so `entries=[]` cached for full TTL. Substring matching can attach unrelated headlines.

### M12. Telegram bot token can leak into logs

Requests exceptions include `/bot<TOKEN>/sendMessage`. Logged via `logger.exception`.

**Fix:** Add logging filter that redacts `bot\d+:[A-Za-z0-9_-]+`.

### M13. status_pulse.py — Reports wrong data

- Line 82 counts `action == "ENTRY"` but main logs "BUY" / "BUY (paper)". Entries always 0.
- Lines 106-109 expect list or `{"positions": []}` but PositionStore writes dict keyed by security_id. Open positions always 0.
- Line 86 reads `reason` but `log_skip` writes `skip_reason`.

### M14. eod_accuracy.py — Tracker never matches trades

Reads top-level `scored_at_price` and `fill_price`, but real audit rows use `detail.scored_at_price`. `fill_price` is None at decision time. Every BUY/SKIP is skipped, report is always empty. Test fixtures use top-level key, masking the bug. Line 76 takes `iloc[0]` which on a holiday = wrong day's close.

### M15. instruments.py — Symbol resolution bugs

- Line 61: `split("-")[0]` turns BAJAJ-AUTO into "BAJAJ" — never resolves. `_tick` logs warning every second.
- Line 111: BE/BZ series admitted but reject INTRADAY orders.
- Per-instrument `tick_size` parsed but ignored; gateway uses global tick.

### M16. No NSE holiday handling (session.py)

On weekday holidays, bot scores stale LTPs, spending Jev budget and logging paper BUYs at stale prices.

---

## LOW

- **L1.** RS period off-by-one: `iloc[-period_days]` is 62-session return, not 63. Stock and Nifty series can misalign after separate dropna.
- **L2.** costs.py: each partial exit slice charges full round trip (2x brokerage). Backtest sizing forces at least 1 share above capital.
- **L3.** auth.py:59-62: token temp file briefly readable before chmod. Use `os.open(..., 0o600)`.
- **L4.** positions.py:57: `Position(**v)` raises on unknown fields — rollback to older code after new fields crashes with open positions. `list_open()` returns shared mutable objects.
- **L5.** Kill-switch removal never writes audit `update_outcome` — dangling decisions in trail.
- **L6.** Secret-adjacent artifacts: `jev-trader-key.pem` in OneDrive-synced root (gitignored but cloud-synced). Stray `./~/.indstocks/session_token.json` in repo root.
- **L7.** yfinance and pandas missing from requirements.txt/pyproject.toml. `__version__` stuck at "0.1.0" vs project 0.19.0.
- **L8.** Dead code: `JevEvaluator.passes_threshold` unused, threshold duplication in RiskConfig. Renamed/demerged tickers (ZOMATO, TATAMOTORS, IIFL, GMRINFRA) silently skipped.
- **L9.** Scanner alert failures swallowed at debug level — real send failures invisible.

---

## Recommended Fix Order

**Before live trading:** H1, H2, H5, H3, H4, H7, H6 (safety gates)
**Then:** M8, M1, M10, M14, M13 (data integrity and monitoring)
**Then:** remaining medium and low findings
