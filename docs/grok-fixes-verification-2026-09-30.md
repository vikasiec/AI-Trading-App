# Verification of Grok's Sonnet 5.5 Review Fixes — 2026-09-30

Reviewer: Claude Opus 4.6
Source: `grok-sonnet-review-fixes.md` claims, verified against actual code.
Tests: 331 passed (up from 273 pre-review).

## HIGH SEVERITY — All 7 addressed

| # | Issue | Verdict | Key code |
|---|-------|---------|----------|
| H1 | Heartbeat recovery flatten | FIXED | heartbeat.py:86-91 — guards on `_flattened_this_outage` |
| H2 | Kill switch false success | FIXED | risk_governor.py:183-303 — `FlattenResult`, per-request try/except, delivery excluded |
| H3 | Target LIMIT blocks stop | FIXED | exits.py:258-286 — cancels resting exit for market-type reasons |
| H4 | No order idempotency | FIXED | exits.py:329-348, execution_gateway.py:198-214 — lookback on failure |
| H5 | Uncaught exceptions kill loop | FIXED | main.py:263-371 — every subsystem wrapped |
| H6 | Config validation warn-only | FIXED | config.py:151-195 — tick_size, entry_mode, booleans validated |
| H7 | /halt doesn't persist | PARTIAL | Thread-safe flag works per-session. Does NOT survive process restart. |

## MEDIUM SEVERITY

| # | Issue | Verdict | Key code |
|---|-------|---------|----------|
| M1 | Drawdown weak | FIXED | risk_governor.py:111-116, main.py:306-314 |
| M2 | Re-entry churn | FIXED | risk_governor.py:83-90, exits.py:168-171 |
| M3 | Wasted Jev calls | FIXED | main.py:589-591 — position cap checked before Jev |
| M4 | BarCache never resets | FIXED | bar_cache.py:20-26, main.py:321 |
| M5 | Timeout under-counts fills | FIXED | execution_gateway.py:362-363 |
| M6 | force_exit falls back to entry price | FIXED | exits.py:96-100 — no LTP = skip |
| M7 | GTT cleanup gaps | FIXED | exits.py:124, timeout-fill paths |
| M8 | Stale positions / blind sell | FIXED | exits.py:185-226 — `_broker_qty()` |
| M9 | Watchlist docstring | FIXED | Already done |
| M10 | Scanner failure wipes watchlist | FIXED | scan_uptrend.py:291-293 |
| M11 | feedparser no timeout | FIXED | news_feed.py:44 — 8s timeout, word-boundary match |
| M12 | Bot token leaks in logs | FIXED | slog.py:10 — RedactSecretsFilter |
| M13 | status_pulse wrong data | NOT VERIFIED | Reporting script, not trading loop |
| M14 | eod_accuracy never matches | NOT VERIFIED | Reporting script, not trading loop |
| M15 | instruments.py symbol bugs | FIXED | BAJAJ-AUTO preserved, BE/BZ filtered, tick_size parsed |
| M16 | No NSE holiday handling | FIXED | session.py:17-34 |

## LOW SEVERITY

| # | Verdict |
|---|---------|
| L1 | FIXED — RS period `iloc[-(period_days+1)]`, Nifty reindexed per stock |
| L2 | FIXED — partial exit uses 1 brokerage order |
| L3 | FIXED — token temp file chmod 0o600 |
| L4 | FIXED — unknown fields dropped, list_open returns copies |
| L5 | FIXED — kill-switch writes audit outcome |
| L7 | FIXED — version and dependencies updated |
| L6 | LEFT — .pem in root (user's responsibility) |
| L8 | LEFT — passes_threshold unused (intentional) |

## Open items for live transition

1. **H7 halt persistence**: Write a halt file on `/halt CONFIRM`, check on startup.
   Risk: systemd restart after /halt resumes trading with open positions.
   Mitigation: PAPER_TRADING=true makes this low-risk currently.

2. **M13/M14**: Verify status_pulse.py and eod_accuracy.py fixes manually.
   Risk: Reporting only — no trading impact.
