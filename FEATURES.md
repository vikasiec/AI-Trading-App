# Features List

A running log of what's implemented, what's stubbed, and what's still needed.
Append to this file as the codebase changes — newest entry at the top of each
section. See `docs/ARCHITECTURE.md` for the full design and the Pre-Flight
Checklist before anything here touches live capital.

---

## Implemented

| Feature | Where | Notes |
|---|---|---|
| INDstocks TOTP auth (`/generate/token`) | `auth.py` | Single-owner token refresh + shared read-only cache for other processes |
| Jev AI conviction scoring | `jev_client.py` | Typed `Score` question, returns calibrated score + confidence |
| Market context compression | `feature_prep.py` | Fixed-budget text state for Jev, not raw feed dumping |
| Symbol → security_id resolution | `instruments.py` | Caches Instruments Master CSV, 24h TTL |
| Risk governor: position sizing | `risk_governor.py` | `min(₹50,000, 2% of live equity)`, both caps enforced |
| Risk governor: drawdown tracking | `risk_governor.py` | Reads live `/funds`, not an in-memory counter |
| Risk governor: price collar | `risk_governor.py` | Rejects if live LTP has moved >0.15% since the score was computed |
| Risk governor: idempotency | `risk_governor.py` | Rebuilt from `/order-book` on startup, not trusted from memory alone |
| Risk governor: kill switch | `risk_governor.py` | Cancels open orders **and** squares off open positions |
| Tick-size rounding | `risk_governor.py::round_to_tick` | Decimal-based, avoids float rounding errors |
| Order placement | `execution_gateway.py` | Matches INDstocks' real `/order` payload (`security_id`, `algo_id`, `segment`, `validity`) |
| Exit logic (stop-loss, target, time-based) | `exits.py`, `positions.py` | Every position this bot opens is tracked with exit rules and force-closed by one of the three; checked every tick before new entries |
| Telegram alerts + kill switch | `telegram_alerts.py` | `/halt` and `/flatten` restricted to `TELEGRAM_OWNER_CHAT_ID`; other senders silently ignored |
| Audit trail | `audit.py` | Append-only JSONL, one line per decision, `update_outcome()` for closing the Jev-calibration loop |
| Main trading loop | `main.py` | Wires all of the above; `PAPER_TRADING=true` by default |
| Config via env vars | `config.py` | No secrets in code; fails loudly if a required var is missing |
| Unit tests | `tests/` | 11 tests covering risk governor logic, auth cache round-trip, Telegram authorization |
| CI | `.github/workflows/ci.yml` | Runs the test suite on every push to `main` |

## Stubbed / placeholder — needs real wiring

| Item | Where | What's needed |
|---|---|---|
| Watchlist source | `main.py::_tick` | Hardcoded to `["RELIANCE"]`; needs a real symbol source |
| News / sentiment feed | `feature_prep.py` | `headlines` is always empty; needs a real ingestion source |
| Jev endpoint & response schema | `jev_client.py` | URL is a placeholder; confirm against TypeSafe's current docs before relying on it |
| Instruments Master CSV columns | `instruments.py` | Column names (`symbol`, `security_id`, `scrip_code`) are a best guess; confirm against `api-docs.indstocks.com/instruments/` |
| Traded instrument | project-wide | Nothing yet resolves to a specific tradable instrument (Nifty future vs. ETF vs. individual equities) |

## Known gaps / not started

- No integration test against a real or sandboxed INDstocks/Jev response — only unit-level coverage exists so far.
- No Jev shadow-mode logging run yet (see `docs/ARCHITECTURE.md` §6 and §10) — the 0.80 conviction threshold is unvalidated against real outcomes.
- No statutory cost engine (STT/GST/SEBI fees/brokerage) to check strategy edge net of costs.
- No WebSocket streaming — current design polls `/market/quotes/ltp` per tick.
- No multi-instrument portfolio-level risk (correlation, aggregate exposure across symbols) — the risk governor currently reasons per-order.
- No backtesting harness.

---

## Roadmap — next set of features

Grouped by priority. P0 blocks paper trading from being meaningful; P1 is needed
before real capital; P2 is production hardening once P0/P1 are done.

### P0 — makes paper trading meaningful

- [ ] Real market-data feed: WebSocket tick stream, replacing the per-symbol `/market/quotes/ltp` poll in `main.py`
- [ ] Real watchlist/universe selection, replacing the hardcoded `["RELIANCE"]`
- [ ] News/filing ingestion for `feature_prep.py` -- `headlines` is currently always empty, so Jev only ever sees price data
- [ ] Confirm Jev's real endpoint + response schema (`jev_client.py` is a placeholder)
- [ ] Confirm Instruments Master CSV schema (`instruments.py` is a best guess)
- [x] **Exit logic** -- stop-loss, target, and time-based exits. Positions this bot opens are tracked in `positions.py` and closed by `exits.py` every tick, before new entries are considered. *(done 2026-09-25)*

### P1 — before real capital

- [ ] Statutory cost engine (STT / GST / SEBI fees / brokerage) so backtests reflect net edge, not gross
- [ ] Backtesting harness against historical data, isolated from the live/paper code path
- [ ] Scheduled job to score `audit_trail.jsonl` outcomes against logged Jev conviction, validating the 0.80 threshold instead of assuming it
- [ ] Portfolio-level risk: aggregate exposure across symbols, correlation/sector concentration limits, max concurrent positions -- the risk governor currently reasons per-order only
- [ ] GTT / bracket orders -- exchange-side stop-loss + target placed atomically with entry (client-side stops die if the process crashes)
- [ ] Order/position reconciliation loop -- periodic polling of `/order-book` and `/positions`, not just the one-time load on startup
- [ ] Reconnect/backoff for the market-data feed and for INDstocks/Jev API failures -- a network blip currently just throws

### P2 — production hardening

- [ ] Structured logging shipped somewhere durable, not just stdout
- [ ] Metrics/dashboard: live P&L, open exposure, win rate, Jev calibration drift over time
- [ ] Health-check + external uptime monitor, distinct from the Telegram heartbeat
- [ ] Multi-instrument, multi-strategy support (currently one strategy, one signal type)
- [ ] Config validation on startup -- catch a bad `.env` before market open, not mid-loop
- [ ] Secrets management beyond `.env` once this runs on a real server long-term

---

## Changelog

### 2026-09-25 (v2)
- Added exit logic: `positions.py` (persisted open-position store with stop-loss/target/max-hold metadata, survives restarts) and `exits.py` (`ExitManager`, checked every tick before new entries).
- Exits are LIMIT orders at current LTP, consistent with the no-MARKET-orders price collar policy -- the kill switch's `flatten_all()` remains the one deliberate exception, since its job is guaranteed exit, not price control.
- `main.py` now: skips scoring a symbol it's already holding, opens a tracked position on every filled entry, and runs `ExitManager.check_and_exit_all()` at the top of every loop tick.
- New config: `STOP_LOSS_PCT`, `TARGET_PCT`, `MAX_HOLD_MINUTES`, `POSITIONS_STORE_PATH`.
- 10 new tests covering stop-loss/target/time-exit branch logic, paper vs. live order behavior, failed-exit-order retry safety, and position-store persistence across restarts. Full suite: 21/21 passing.

### 2026-09-25
- Initial scaffold pushed: three-tier architecture (Jev scoring → risk governor → INDstocks execution), auth, risk governor, execution gateway, Telegram alerts, audit trail, main loop, test suite, CI.
- Added prioritized roadmap (P0/P1/P2) for the next set of features.
