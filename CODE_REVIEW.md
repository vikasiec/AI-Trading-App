## Summary

The bot is a single-threaded INDstocks loop: Jev (or rule) scoring, a risk governor, limit entries, and stop/target/time exits, with `PAPER_TRADING` defaulting to the string `true`. The happy path (paper book, confirmed full fills, local position store) is coherent and prices stay floats. The dominant risks are live orders that can still fire in paper mode, duplicate live buys/sells after an ambiguous or partial fill, a kill switch that cancels on the wrong HTTP method and then forgets local stops, and a once-per-day session flatten that does not retry.

## Issues

### Issue 1 -- Severity: bug
- File: src/jev_indstocks_trader/main.py:379
- Description: Paper mode is not isolated from live orders. `_tick` always calls `RiskGovernor.validate_trade` before the `if not cfg.risk.paper_trading` branch. On `drawdown >= daily_loss_limit_pct`, `validate_trade` calls `flatten_all` (`risk_governor.py:114-116`), which POSTs real MARKET square-offs and tries to cancel working orders. Drawdown is the broker `/funds` P&L, not paper P&L, so a loss on the linked account (manual trades, or a prior live session) while `PAPER_TRADING=true` still sends live flatten orders. The Telegram/heartbeat kill path does check the paper flag (`main.py:75`); this path does not. `tests/test_tick.py` mocks the governor, so it never catches this.
- Suggestion: Do not call `flatten_all` from `validate_trade`. Let the caller flatten, and only when `paper_trading` is false. In paper mode, either skip the broker drawdown check or compute it from the local paper book.
- Status: open

### Issue 2 -- Severity: bug
- File: src/jev_indstocks_trader/config.py:67
- Description: Paper mode fails open. `paper_trading` is true only when the env value lowercases to exactly `true`. `1`, `yes`, `on`, and a set-but-empty `PAPER_TRADING=` all select live trading (`os.environ.get` returns `""` instead of the default). `load_config()` defaults to `strict=False` (`config.py:167-170`). `run_loop` only logs `validate_config` warnings (`main.py:59-62`), including the live-capital warning, and then places orders. Compose `env_file` overrides the Dockerfile `PAPER_TRADING=true`, so a sloppy `.env` value is enough to go live with no second confirmation.
- Suggestion: Accept only an explicit `true`/`false` (reject anything else at startup). Refuse to start a live loop unless a separate hard gate is set (strict config, or a dedicated `LIVE_TRADING_CONFIRM` value), instead of logging a warning and continuing.
- Status: open

### Issue 3 -- Severity: bug
- File: src/jev_indstocks_trader/main.py:440
- Description: An entry that is not a clean reject can be submitted again on the next 1s tick. `TIMEOUT` / `NOT_FOUND` does not call `mark_executed` (`main.py:440-459`). `validate_trade` only blocks a key after `mark_executed`, and the governor test locks that in (`tests/test_risk_governor.py` allows a second approval before the mark). `wait_for_fill` can return `TIMEOUT` with a non-zero `filled_qty` on the last seen row (`execution_gateway.py:291-296`), but the entry branch ignores that qty unless status is `FILLED` or `PARTIAL`. Best-effort `cancel_order` is not checked: if the cancel throws, or the order fills as the cancel races, the position is untracked and the next loop buys again. Reconciliation only alerts (`main.py:188-189`, `reconciliation.py:64-72`); it does not block the new order. `tests/test_tick.py:test_tick_ambiguous_fill_cancels_and_does_not_store` asserts the key is not marked, so it stays green while this hole exists.
- Suggestion: On any broker ack, reserve the idempotency key (and persist an in-flight order id) before waiting. If timeout `filled_qty > 0`, book that qty and do not place another buy. If cancel fails, halt new entries in that symbol until a human or a broker-position check clears it.
- Status: open

### Issue 4 -- Severity: bug
- File: src/jev_indstocks_trader/exits.py:156
- Description: Exit retries can sell shares the bot no longer has, or sell the same tail twice. A `TIMEOUT` / `NOT_FOUND` exit leaves the full position open and does not cancel (`exits.py:156-168`; `tests/test_exits.py` asserts `cancel_order` is not called). The next tick still sees the stop/target/time condition and submits another SELL for the original qty. That is a second live order if the first MARKET sell filled during the 3s poll or is still working. A `PARTIAL` exit reduces `position.qty` and returns without cancelling the original order (`exits.py:169-193`). If that order is still live for the residual, the next tick sells the residual again. The comment that "left open is the safe default" is misleading: open-plus-retry is how the double sell happens. Reconciliation does not stop the retry.
- Suggestion: Store the working exit order id on the position. On timeout, poll that id; do not place another sell until it is terminal. On partial, cancel the residual before re-arming, and size the next sell to broker `net_qty` minus still-open order qty. Treat timeout `filled_qty > 0` as a partial close.
- Status: open

### Issue 5 -- Severity: bug
- File: src/jev_indstocks_trader/risk_governor.py:172
- Description: Kill-switch cancel uses `DELETE /order/{oid}` (`risk_governor.py:172-174`). The confirmed cancel, and the one `ExecutionGateway.cancel_order` uses, is `POST /order/cancel` with `order_id` and `segment` (`execution_gateway.py:236-247`; also `FEATURES.md`). Drawdown flatten, session flatten, and `/halt` all go through `flatten_all`, so working limits are not actually cancelled. A later MARKET square-off can then race those still-live orders (double fill, or a sell that the resting limit also fills).
- Suggestion: Cancel through `ExecutionGateway.cancel_order` (same body and method as entries/exits). Do not treat a non-2xx DELETE as success.
- Status: open

### Issue 6 -- Severity: bug
- File: src/jev_indstocks_trader/main.py:73
- Description: `_kill_switch` always deletes every local position after `flatten_all` (`main.py:73-83`). `flatten_all` swallows HTTP errors and, when leftovers remain, only logs them (`risk_governor.py:222-228`); it does not raise. The local book is cleared anyway, so client-side stops are gone while the broker may still be long. The next loop will not exit those shares, and `has_open` will not block a new buy. The same callback runs on the Telegram polling thread (`telegram_alerts.py:70`, `telegram_alerts.py:104`) with no lock around `place_limit_order` / `place_market_order`, so a flatten can interleave with an in-flight entry on the main thread and leave an order that the kill just "finished".
- Suggestion: Clear a local position only after the broker shows `net_qty == 0` for that id (or after a confirmed exit fill). If flatten is incomplete, keep the store, alert, and stop new entries. Run kill and the trading loop under one lock so a buy cannot start after the flatten decision.
- Status: open

### Issue 7 -- Severity: bug
- File: src/jev_indstocks_trader/main.py:175
- Description: The 15:15–15:30 IST flatten window runs `force_exit_all` and (live) `governor.flatten_all` once, then sets `flattened_on = today` even if those calls left positions open (`main.py:175-187`). `force_exit_all` does not report per-symbol failure; a rejected or ambiguous exit stays in the store (`exits.py:124-134`, `exits.py:156-168`). Later iterations of the flatten phase only sleep. After 15:30, `session_phase` is `closed` and the loop idles without another exit attempt (`main.py:171-174`). An INTRADAY position the broker did not square itself can be held overnight. Paper mode does not have the broker flatten backup.
- Suggestion: Set `flattened_on` only when the local store is empty and, in live mode, a positions read shows no `net_qty`. While phase is `flatten` or the store is non-empty after the close, keep calling the exit path.
- Status: open

### Issue 8 -- Severity: bug
- File: src/jev_indstocks_trader/heartbeat.py:66
- Description: With `HEARTBEAT_FLATTEN=true` (live), flatten runs only after `/user/profile` has already failed, i.e. while the broker is the thing that is down. `_flattened_this_outage` is set even when `flatten_fn` throws (`heartbeat.py:73-77`). The next successful ping zeros the flag and does not flatten (`heartbeat.py:80-84`). Combined with Issue 6, a failed attempt still wipes the local book via `_kill_switch`, then trading resumes on recovery with stops forgotten and no second square-off. Default is off, but the flag does not implement the protection it advertises.
- Suggestion: Do not mark the outage flattened unless a positions read is flat. On the first successful ping after failures, flatten if anything is still open, and do not delete local state from inside the failed attempt.
- Status: open

### Issue 9 -- Severity: bug
- File: src/jev_indstocks_trader/main.py:201
- Description: `run_loop` has no `except Exception` around `_tick` (`main.py:165-212`). Inside `_tick`, `validate_trade` (`main.py:379`) and `gateway.get_funds` (`main.py:392`) sit outside the per-symbol `try` blocks. `get_drawdown_pct` and `get_funds` call `raise_for_status` and index `["data"]` (`risk_governor.py:85-87`, `execution_gateway.py:178-181`). One `/funds` 500, timeout, or shape change kills the process. Exits do not run again until something restarts it. `deploy/jev-trader.service` uses `Restart=on-failure` (5s), which turns that into a crash loop and re-opens Issue 3/11 (in-memory idempotency and the Jev counter are gone).
- Suggestion: Catch broker errors per symbol and around the tick, log them, and keep the exit loop running. A funds failure should skip new entries, not exit the process.
- Status: open

### Issue 10 -- Severity: bug
- File: src/jev_indstocks_trader/main.py:163
- Description: `JEV_DAILY_CALL_CAP` is not a daily cap. `jev_daily_count` is a process-local list reset at startup (`main.py:163-164`) and only on a date change inside a still-running process (`main.py:193-196`). A restart, including systemd `Restart=on-failure`, starts at 0 and can call Jev again up to the cap. The in-tick check (`main.py:330-331`) therefore does not enforce the invariant across the calendar day. The rescore map is also memory-only, so a restart immediately re-scores every symbol.
- Suggestion: Persist the IST date and the call count (atomic write, same pattern as the token cache) and refuse new `evaluate_signal` calls when the stored count is at the cap. Increment before the HTTP call, including retries inside `retry_with_backoff`.
- Status: open

### Issue 11 -- Severity: bug
- File: src/jev_indstocks_trader/risk_governor.py:78
- Description: Idempotency fails open and does not survive a restart. If the startup order-book GET fails, `_load_idempotency_state` logs and returns an empty set (`risk_governor.py:78-80`). `executed_keys` is never written to disk; `mark_executed` only mutates the set (`risk_governor.py:137-139`). Keys are `{security_id}_{epoch_minute}`, so they stop matching after 60s even when the book reload works. A crash after a broker ack and before `position_store.add` (or a restart whose book fetch fails) allows another live buy. Orders with no timestamp are keyed to "now" (`risk_governor.py:74-75`), so a book of undated rows only blocks the current minute. The module docstring says broker state is authoritative; a failed read is treated as "no prior orders".
- Suggestion: If the order book cannot be loaded, refuse new entries until it can. Persist in-flight and filled keys. Block a symbol while the broker still shows a position or a non-terminal order, not only for the current minute.
- Status: open

### Issue 12 -- Severity: bug
- File: .gitignore:1
- Description: An untracked private key file `jev-trader-key.pem` is present at the repository root. `.gitignore` ignores `.env`, `session_token.json`, and `.indstocks/`, but it has no rule for `*.pem` or that filename. A later `git add .` can commit the key. The file was not opened.
- Suggestion: Add `*.pem` (and this filename) to `.gitignore`, keep the key outside the tree, and rotate it if it has ever been copied into a remote or a backup of the repo.
- Status: open

### Issue 13 -- Severity: bug
- File: deploy/jev-token.timer:5
- Description: The timer description says weekday 09:00 IST, but `[Timer]` is `OnCalendar=Mon..Fri 09:00` with no `Timezone=Asia/Kolkata`. systemd evaluates that in the host timezone. A UTC cloud image (the usual default; `docs/DEPLOY.md` allows any VPS) fires at 09:00 UTC, which is 14:30 IST, not before the 09:15 open. The trading process never refreshes the token itself. Morning orders and exits then run on a missing or >24h token (`auth.py` only warns after 23.5h and still returns it). Docker compose has the same gap: the token service is `restart: "no"` and is not on this timer.
- Suggestion: Set `Timezone=Asia/Kolkata` on the timer. For compose, run the refresh on an IST schedule instead of once at `up`. If the cached token is older than the broker TTL, stop new entries and keep retrying exits rather than only logging a warning.
- Status: open

### Issue 14 -- Severity: suggestion
- File: src/jev_indstocks_trader/main.py:220
- Description: `validate_config` reports `WEBSOCKET_ENABLED` and `GTT_ENABLED` as unsafe placeholders (`config.py:158-161`) but `run_loop` does not refuse to start. With the socket on, `_get_quote` prefers a fresh tick and forces `day_change_pct` and `volume` to 0 (`main.py:220-224`). `GapBook.observe` freezes the first print (`gaps.py:15-20`), so the day's gap becomes 0 and `gap_veto` never trips. Index veto also sees 0 instead of "unknown", so a down index does not block longs. Those ticks are still used as the order price. GTT placement hits an unconfirmed `/gtt/place` (`gtt_orders.py:66`). `cancel_gtt` swallows errors (`gtt_orders.py:81-90`) and the exit path still removes the local position (`exits.py:203-214`), so a live exchange leg can sell again after the bot is flat.
- Suggestion: Treat those two config problems as startup failures until the protocols are confirmed. Do not let a tick that lacks day-change replace the REST quote fields the vetoes use. If a GTT cancel fails, keep the position tracked and alert instead of dropping it.
- Status: open

### Issue 15 -- Severity: suggestion
- File: src/jev_indstocks_trader/bar_cache.py:32
- Description: `strategies.py:3-5` says the momentum and mean-reversion functions are not wired into the live loop. They are: `entry.rule_vote` calls them, and `ENTRY_MODE=rule` or `jev_and_rule` trades on that vote (`entry.py:20-43`, `main.py:288`, `main.py:357`). `BarCache.update` adds the quote `volume` onto the current minute on every poll (`bar_cache.py:32`). The LTP payload's volume is a day snapshot, not a 1-second delta, and the loop polls about once a second (`main.py:274`), so minute volume is a sum of cumulative prints. Rule volume filters and VWAP weights (`features.py:26-27`) then do not mean what the rule code says. The comment in `strategies.py` is false and will send the next change to the wrong place.
- Suggestion: Diff cumulative volume (or ignore it) before storing the bar. Delete or rewrite the "not wired into the live loop" comment so it matches `entry.rule_vote`.
- Status: open
