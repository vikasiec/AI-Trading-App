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
| Real watchlist source | `watchlist.py` | `WATCHLIST_SYMBOLS` env var, or `WATCHLIST_FILE` JSON file, or a small built-in default -- no more hardcoded list in `main.py` |
| News/headline ingestion | `news_feed.py` | RSS/Atom feeds (`NEWS_RSS_FEEDS`), filtered by symbol/company name, TTL-cached; feeds `feature_prep.py`'s `headlines` |
| Jev endpoint + payload (confirmed) | `jev_client.py` | `POST https://api.typesafe.ai/v1/systemone`, with the required `model` field added -- confirmed against TypeSafe's public API reference |
| WebSocket market-data feed (best-effort, gated off by default) | `market_data.py` | Reconnect/backoff loop; `main.py` uses a fresh tick when available and falls back to the already-confirmed REST `/market/quotes/ltp` poll otherwise. **Protocol unconfirmed** -- see note below |
| Retry/backoff for flaky reads | `retry.py`, used in `main.py` | Wraps LTP fetches and Jev calls (not order placement -- retrying a write isn't safe without an idempotency key) |
| Telegram alerts + kill switch | `telegram_alerts.py` | `/halt` and `/flatten` restricted to `TELEGRAM_OWNER_CHAT_ID`; other senders silently ignored |
| Audit trail | `audit.py` | Append-only JSONL, one line per decision, `update_outcome()` for closing the Jev-calibration loop |
| Main trading loop | `main.py` | Wires all of the above; `PAPER_TRADING=true` by default |
| Config via env vars | `config.py` | No secrets in code; fails loudly if a required var is missing |
| Unit tests | `tests/` | 11 tests covering risk governor logic, auth cache round-trip, Telegram authorization |
| CI | `.github/workflows/ci.yml` | Runs the test suite on every push to `main` |

## Stubbed / placeholder — needs real wiring

| Item | Where | What's needed |
|---|---|---|
| Instruments Master CSV columns | `instruments.py` | Column names (`symbol`, `security_id`, `scrip_code`) are still a best guess -- the general shape (symbol -> security_id + scrip code like `NSE_2885`) is corroborated by a third-party INDstocks MCP tool, but the exact CSV header names are unconfirmed against `api-docs.indstocks.com/instruments/` |
| WebSocket protocol | `market_data.py` | Connection URL, subscribe-message shape, and tick-message shape are a best-effort placeholder modeled on the REST quotes response -- `api-docs.indstocks.com/Websockets/` wasn't reachable while building this. Gated behind `WEBSOCKET_ENABLED=false` for exactly this reason |
| Traded instrument | project-wide | Nothing yet resolves to a specific tradable instrument (Nifty future vs. ETF vs. individual equities) |

## Known gaps / not started

- No integration test against a real or sandboxed INDstocks/Jev response — only unit-level coverage exists so far.
- No Jev shadow-mode logging run yet (see `docs/ARCHITECTURE.md` §6 and §10) — the 0.80 conviction threshold is unvalidated against real outcomes.
- No statutory cost engine (STT/GST/SEBI fees/brokerage) to check strategy edge net of costs.
- No multi-instrument portfolio-level risk (correlation, aggregate exposure across symbols) — the risk governor currently reasons per-order.
- No backtesting harness.

---

## Roadmap — next set of features

Grouped by priority. P0 blocks paper trading from being meaningful; P1 is needed
before real capital; P2 is production hardening once P0/P1 are done.

### P0 — makes paper trading meaningful

- [x] Real market-data feed: WebSocket tick stream (`market_data.py`), used when fresh, falling back to the confirmed REST `/market/quotes/ltp` poll otherwise. **Protocol still needs confirmation** -- gated off by default. *(done 2026-09-25, v3)*
- [x] Real watchlist/universe selection (`watchlist.py`) -- env var, file, or built-in default, replacing the hardcoded list. *(done 2026-09-25, v3)*
- [x] News/filing ingestion (`news_feed.py`) -- configurable RSS/Atom feeds, filtered per symbol, feeding `feature_prep.py`. *(done 2026-09-25, v3)*
- [x] Confirm Jev's real endpoint + response schema -- `POST https://api.typesafe.ai/v1/systemone`, `model` field added. *(done 2026-09-25, v3)*
- [ ] Confirm Instruments Master CSV schema (`instruments.py` is still a best guess; general shape corroborated third-party, exact columns not)
- [x] **Exit logic** -- stop-loss, target, and time-based exits. Positions this bot opens are tracked in `positions.py` and closed by `exits.py` every tick, before new entries are considered. *(done 2026-09-25, v2)*

### P1 — before real capital

- [ ] Statutory cost engine (STT / GST / SEBI fees / brokerage) so backtests reflect net edge, not gross
- [ ] Backtesting harness against historical data, isolated from the live/paper code path
- [ ] Scheduled job to score `audit_trail.jsonl` outcomes against logged Jev conviction, validating the 0.80 threshold instead of assuming it
- [ ] Portfolio-level risk: aggregate exposure across symbols, correlation/sector concentration limits, max concurrent positions -- the risk governor currently reasons per-order only
- [ ] GTT / bracket orders -- exchange-side stop-loss + target placed atomically with entry (client-side stops die if the process crashes)
- [ ] Order/position reconciliation loop -- periodic polling of `/order-book` and `/positions`, not just the one-time load on startup
- [x] Reconnect/backoff for the market-data feed (`market_data.py`) and for LTP/Jev reads (`retry.py`) *(done 2026-09-25, v3)* -- order placement is deliberately **not** auto-retried, since a blind retry could double-submit without an idempotency key

### P2 — production hardening

- [ ] Structured logging shipped somewhere durable, not just stdout
- [ ] Metrics/dashboard: live P&L, open exposure, win rate, Jev calibration drift over time
- [ ] Health-check + external uptime monitor, distinct from the Telegram heartbeat
- [ ] Multi-instrument, multi-strategy support (currently one strategy, one signal type)
- [ ] Config validation on startup -- catch a bad `.env` before market open, not mid-loop
- [ ] Secrets management beyond `.env` once this runs on a real server long-term

---

## Changelog

### 2026-09-25 (v3)
- **Real watchlist** (`watchlist.py`): `WATCHLIST_SYMBOLS` env var → `WATCHLIST_FILE` JSON file → small built-in default. Replaces the hardcoded `["RELIANCE"]`.
- **News ingestion** (`news_feed.py`): configurable RSS/Atom feeds (`NEWS_RSS_FEEDS`), filtered per symbol/company name, TTL-cached so every tick doesn't refetch every feed. Feeds `feature_prep.py`'s previously-always-empty `headlines`.
- **Jev endpoint fixed** (`jev_client.py`): confirmed against TypeSafe's public API reference -- `POST https://api.typesafe.ai/v1/systemone`, and a required `model` field (`JEV_MODEL`, default `jev-latest`) that v1/v2 were missing entirely.
- **WebSocket market-data feed** (`market_data.py`): `INDstocksWebSocketFeed` with exponential-backoff reconnect, plus `LiveTickCache` for staleness-aware tick storage. **Gated behind `WEBSOCKET_ENABLED=false` by default** -- the connection URL and message shapes are a best-effort placeholder, since INDstocks' actual WebSocket protocol page wasn't reachable while building this. `main.py` uses a fresh tick when the feed has one and falls back to the already-confirmed REST `/market/quotes/ltp` poll otherwise, so nothing depends on the placeholder being correct.
- **Retry/backoff** (`retry.py`): wraps LTP fetches and Jev scoring calls with exponential backoff. Deliberately *not* applied to order placement -- retrying a write without an idempotency key risks double-submitting.
- 15 new tests: watchlist resolution priority, retry/backoff behavior (success, eventual success, exhaustion, non-retryable exceptions), news filtering/caching/failure handling, tick-cache freshness. Full suite: 36/36 passing.
- `FEATURES.md` updated: watchlist, news ingestion, Jev endpoint, and feed reconnect moved from Roadmap to Implemented; Instruments Master schema and the WebSocket protocol itself remain open (now called out explicitly rather than silently assumed).

### 2026-09-25 (v2)
- Added exit logic: `positions.py` (persisted open-position store with stop-loss/target/max-hold metadata, survives restarts) and `exits.py` (`ExitManager`, checked every tick before new entries).
- Exits are LIMIT orders at current LTP, consistent with the no-MARKET-orders price collar policy -- the kill switch's `flatten_all()` remains the one deliberate exception, since its job is guaranteed exit, not price control.
- `main.py` now: skips scoring a symbol it's already holding, opens a tracked position on every filled entry, and runs `ExitManager.check_and_exit_all()` at the top of every loop tick.
- New config: `STOP_LOSS_PCT`, `TARGET_PCT`, `MAX_HOLD_MINUTES`, `POSITIONS_STORE_PATH`.
- 10 new tests covering stop-loss/target/time-exit branch logic, paper vs. live order behavior, failed-exit-order retry safety, and position-store persistence across restarts. Full suite: 21/21 passing.

### 2026-09-25
- Initial scaffold pushed: three-tier architecture (Jev scoring → risk governor → INDstocks execution), auth, risk governor, execution gateway, Telegram alerts, audit trail, main loop, test suite, CI.
- Added prioritized roadmap (P0/P1/P2) for the next set of features.
