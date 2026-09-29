# Paper operations runbook

For Claude. Follow this on the host that already runs the bot. Do not invent a second scheduler, a second token refresh, or a live-money switch.

## Deploy decision

Paper observation is ready. Live orders are not.

`PAPER_TRADING` stays `true`. `ENTRY_MODE` stays `jev`. The daily 20-day-high rule failed its held-out year (average about −₹547 per trade). Do not wire that rule into the live loop, and do not add a gate that would turn it on.

Changing `PAPER_TRADING` to `false` requires an explicit message from the user in this chat. A green test suite, a quiet paper week, or this document is not that message.

## What “up” means

The trading process runs 24 hours, including nights and weekends. Outside 09:15–15:30 IST on weekdays it only sleeps. If the process is down at 09:15, it misses the open. If it is down at 15:15, it misses the flatten.

One token refresh. The main loop and the Telegram listener only read the cached token. A second refresh invalidates the session.

Health check binds to `127.0.0.1` unless `HEALTH_TOKEN` is set.

## Weekday clock (IST)

Times below are Asia/Kolkata. On a UTC host, 08:30 IST is 03:00 UTC. Do not schedule both.

Pre-market chain runs in dependency order — token first, then scanner, then health check:

| Time | What runs | Expected Telegram |
|---|---|---|
| 08:30 | `scripts/refresh_token.py`, once, **daily** (incl. weekends) | No routine message. A failure is a critical alert. |
| 08:35 | `scripts/scan_premarket.py` (weekdays) | Scanner alert with crossovers or "no crossovers". |
| 08:50 | `scripts/premarket_health.py` (weekdays) | One summary. Failures named. |
| 09:15–15:15 | Trading loop. Entries allowed after the open-skip window. | Startup message if the process just started: mode PAPER, watchlist, entry mode, open positions. |
| Every 30 min from 09:15 to 15:30 | `scripts/watchdog.py` | Silent if `jev-trader` is active. One alert if the service should be running and is not. |
| 15:15–15:30 | Flatten only. No new entries. | One critical message: `Session flatten completed (YYYY-MM-DD)` when the local book is empty. |
| 15:45 | `scripts/eod_accuracy.py` if `yfinance` is already installed | One accuracy note for the day’s audit rows. |
| Friday 16:00 | `scripts/run_calibration_report.py` | One Telegram note: closed trades, net after costs, conviction buckets. It does not change any setting. |
| Saturday and Sunday | Nothing new. The process stays up and idle. | No scheduled messages. |

Do not install `yfinance` or any other package to make 15:45 run. If the import fails, say so in the next status note and leave the script unscheduled.

The cron lines already written in the script docstrings are the ones to use (`premarket_health.py`, `watchdog.py`, `refresh_token.py`). Point them at this checkout. Do not add a second copy of the same job.

## Alerts and the action for each

The owner chat is the only destination. Ignore forwarded messages and any other chat.

| Message | Meaning | Action |
|---|---|---|
| `Bot started (PAPER)` | Process came up. | Read it. If the mode is not PAPER, stop the process and tell the user before doing anything else. If it says LIVE, do not restart it. |
| Premarket summary, all checks passed | Token, quotes, Jev, and the watchlist answered. | No change. |
| Premarket summary, a check failed | That boundary is down. | Do not restart in a loop. Record the failed check. If the token check failed, run `refresh_token.py` once. If that fails, tell the user. Do not refresh again. |
| `Broker heartbeat failed (n/5)` | Profile call missed. | Watch. In paper mode the bot does not flatten on this. If n reaches 5 and quotes are also failing, tell the user. Do not flip `HEARTBEAT_FLATTEN`. |
| `Session flatten completed` | Local book is empty after 15:15. | No action. One of these per day is correct. |
| `Session flatten incomplete` in the log, and no completed message by 15:25 | Positions still open into the close. | Tell the user the symbol list from the position file. Do not place a live order to finish them while paper mode is on. |
| `Order filled` / entry or exit info | A paper fill was booked. | Leave it. Do not edit the position file by hand. |
| `CRITICAL` with `Kill switch` | `/halt CONFIRM` or a drawdown flatten ran. | Confirm the local book is empty or that the message says the flatten was incomplete. If incomplete, tell the user and leave the rows in place. |
| `Jev daily call cap reached` | No more scores today. | Expected once the cap is hit. Do not raise the cap. |
| Watchdog: service not active during the session | The process is down while the market is open. | Start `jev-trader` once. If it exits again, read the journal, paste the error, and stop. Do not restart every minute. |
| Anything that mentions a real order id, a reject, or a quantity mismatch | The broker path did something. | In paper mode this should be rare. Stop new experiments and tell the user the exact text. |

`/halt CONFIRM` and `/flatten CONFIRM` from the owner flatten the book. `/status` only replies that the listener is alive. Claude does not send those commands unless the user asks.

## Daily loop for Claude

On a weekday, in this order:

1. At 08:30, confirm the token refresh ran (check ~/logs/token_refresh.log). If it failed, run `refresh_token.py` once.
2. At 08:50, read the premarket Telegram summary. If it did not arrive, run the premarket script once and read its exit code.
3. At 09:15, confirm the trader service is active.
3. During the session, act only on the table above. Do not tail the log for curiosity and do not restart a healthy process.
4. At 15:30, confirm the flatten-completed message. If it is missing, check the position file and the journal for `Session flatten incomplete`.
5. After 15:45, if the accuracy script ran, file the note. Do not change `JEV_CONVICTION_THRESHOLD`, stops, or the watchlist from that note.

Friday at 16:00 the calibration script reads the audit log and sends the result on Telegram: number of closed trades, net after costs, and whether the high-conviction bucket beat the one below it. Do not change a threshold from that message. The user decides. The cron hour is 16:00 on the server clock. On a UTC server that is 21:30 IST, which is still after the Friday close.

## Forbidden without a new explicit user message

- Set `PAPER_TRADING=false`.
- Add the daily breakout, or any new vote, to the entry path.
- Raise the Jev call cap, lower the conviction floor, or widen the slippage collar.
- Run `refresh_token.py` on a second machine or from the trading process.
- Edit `.env`, print it, or commit it.
- Delete `positions.json` or the audit log to “clean up.”
- Install packages, open the health port publicly, or force-push.

## Analysis log

The file is `audit_trail.jsonl` (`AUDIT_LOG_PATH`). It is append-only. Do not edit a line, delete the file, or rotate it during a paper week.

Each scored name gets one decision row: symbol, conviction, confidence, action, the reason (`approved`, `slippage_...`, `funds_unreadable`, a rule name), the price Jev saw, the live price, the quantity, whether it was paper, and whether headlines were attached. A closed trade appends a second row with the same `decision_id`: fill price, gross, net, costs, and exit reason (`stop_loss`, `target`, `time_exit`, `session_close`).

A veto that does not call Jev is one skip row per name, per reason, per day. The same rate-limit or gap skip on the next tick does not add another copy.

Friday's calibration report reads this file. If a row has no `reason`, it is from before this change and cannot be used to explain a skip.

## When the paper run is allowed to stop

Stop calling the result encouraging when either of these is true, and tell the user in plain words:

- Closed paper trades are negative after costs, and the higher conviction bucket is not better than the lower one.
- The process cannot stay up through a session, or flatten does not finish.

Until one of those is true, keep the paper loop on the schedule above and change nothing else.
