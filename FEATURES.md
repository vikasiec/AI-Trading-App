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

## Changelog

### 2026-09-25
- Initial scaffold pushed: three-tier architecture (Jev scoring → risk governor → INDstocks execution), auth, risk governor, execution gateway, Telegram alerts, audit trail, main loop, test suite, CI.
