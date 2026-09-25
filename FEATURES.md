# Features & Application Documentation

This is the living reference for what the app does, how each piece works, and
what's still open. It's written to double as onboarding material — read the
**Implemented** section top to bottom and you should understand the whole
system without opening the code. Roadmap and Changelog sit at the bottom for
tracking what changes next and what already changed. See
`docs/ARCHITECTURE.md` for the system diagram and the Pre-Flight Checklist
that gates anything here from touching live capital.

**One-line summary:** Jev AI scores how convincing a trade setup is; every
other decision — whether to act on that score, how much to risk, when to get
out, when to stop entirely — is made by deterministic code that never trusts
the AI's output blindly.

---

## Implemented

Grouped by the stage of the trading loop each piece belongs to.

### 1. Getting data in

**Watchlist (`watchlist.py`)**
Decides which symbols the bot even looks at. Resolution order: the
`WATCHLIST_SYMBOLS` env var (comma-separated, e.g. `RELIANCE,TCS,HDFCBANK`)
wins if set; otherwise `WATCHLIST_FILE` points at a JSON file with a list of
symbols, so you can maintain a watchlist separately from your `.env` and edit
it without restarting the bot; otherwise a small built-in default
(`RELIANCE, TCS, HDFCBANK, INFY`) so the bot still runs out of the box.

**Instruments Master (`instruments.py`)**
INDstocks' order API needs a numeric `security_id`, not a ticker symbol like
`RELIANCE`. This module downloads INDstocks' Instruments Master CSV, caches it
for 24 hours, and resolves a watchlist symbol to its `security_id` and
`scrip_code` (the `NSE_2885`-style code used for quote lookups). *Caveat:* the
exact CSV column names are a best guess — see Open Items below.

**Market data — REST poll + WebSocket feed (`execution_gateway.py`,
`market_data.py`)**
The confirmed, always-available path is a REST call to
`/market/quotes/ltp` for each symbol's last traded price. Layered on top of
that (only when `WEBSOCKET_ENABLED=true`) is `INDstocksWebSocketFeed`, which
holds a persistent connection and keeps a `LiveTickCache` of the freshest
price per symbol, with an exponential-backoff reconnect loop so a dropped
connection doesn't crash the process. `main.py` asks the tick cache first and
only falls back to the REST poll when the cache has nothing fresher than 5
seconds old — so a stale or misconfigured WebSocket degrades gracefully
instead of silently trading on old prices. *Caveat:* INDstocks' real
WebSocket protocol (URL, subscribe/tick message shapes) wasn't reachable
while building this, so it's a best-effort placeholder — hence gated off by
default.

**News ingestion (`news_feed.py`)**
Pulls headlines from one or more RSS/Atom feeds you configure
(`NEWS_RSS_FEEDS`, comma-separated URLs — market news, corporate
announcements, whatever you trust), filters entries whose title or summary
mentions the symbol or company name, and caches each feed's results for
`NEWS_CACHE_TTL_S` seconds (default 5 minutes) so scoring 10 symbols a second
doesn't refetch every feed 10 times a second. If `NEWS_RSS_FEEDS` is empty,
the bot still runs — Jev just scores on price data alone.

**Context compression (`feature_prep.py`)**
Jev is a scorer, not a summarizer, so it should see a compact state, not a
raw feed dump. This module turns a symbol's price, day change, volume, and
top 5 matching headlines into a fixed-shape text block (roughly a 500-token
budget) that becomes the `state` Jev evaluates.

### 2. Scoring the signal

**Jev AI conviction scoring (`jev_client.py`)**
Sends the compressed context to Jev (TypeSafe's System One model) as a typed
`Score` question — "rate conviction that this setup is a high-probability
long entry" — and gets back a calibrated score and confidence, not free text.
Endpoint and payload are confirmed against TypeSafe's public API reference:
`POST https://api.typesafe.ai/v1/systemone`, with `model`, `state`, and
`questions` fields. *Caveat, always worth repeating:* "calibrated" means
calibrated on the question asked, not on whether the trade will actually be
profitable — that mapping only exists once conviction scores are checked
against real logged outcomes (see the Roadmap item below).

**Retry/backoff for reads (`retry.py`)**
Wraps the LTP fetch and the Jev scoring call with exponential backoff (a
transient network blip retries instead of killing that tick). Deliberately
**not** applied to order placement — retrying a write without an idempotency
key risks silently double-submitting an order.

### 3. Deciding whether to trade

**Per-order risk checks (`risk_governor.py`)**
The single gate every entry has to clear, checked in this order:
- **Daily drawdown** — reads live `/funds` (not an in-memory counter) and
  refuses new trades, triggering the kill switch, once today's realized +
  unrealized loss hits `DAILY_LOSS_LIMIT_PCT` of equity.
- **Conviction/confidence floor** — enforced here too, independently of the
  Jev client's own threshold check, as defense in depth.
- **Price collar** — rejects the trade if the live price has moved more than
  `MAX_SLIPPAGE_PCT` since Jev scored it, so a stale score can't fire into a
  price that's already run away.
- **Idempotency** — a `security_id` + one-minute-window key, rebuilt from the
  broker's own `/order-book` on startup (not trusted from memory alone),
  stops the same signal from firing twice.

**Position sizing (`risk_governor.py::size_order`)**
`min(MAX_POSITION_CAPITAL_INR, MAX_POSITION_PCT_EQUITY x live equity)` — both
caps enforced, sized against real equity fetched from `/funds`, not a
hardcoded number.

**Portfolio-level risk (`portfolio_risk.py`)**
Everything above reasons about one order in isolation — it has no idea how
many other positions are already open. This module is the aggregate check,
run after an order passes the per-order gate and before it's actually placed:
- **`MAX_CONCURRENT_POSITIONS`** — a hard cap on how many symbols can be held
  open at once, regardless of how attractive a new signal looks.
- **`MAX_DEPLOYED_CAPITAL_PCT`** — total capital tied up across *all* open
  positions, as a fraction of equity, capped independently of any single
  position's own size limit.
Without this, a bot with a per-order cap of 2% of equity could still open 20
positions and have 40% of the account at risk at once — no single order ever
crossed a limit, but the portfolio did.

**Tick-size rounding (`risk_governor.py::round_to_tick`)**
Every price sent to the broker is rounded to the instrument's real tick size
using `Decimal` arithmetic (not float rounding, which silently produces
invalid prices like `2450.049999999998`).

### 4. Acting on the decision

**Order placement (`execution_gateway.py`)**
Matches INDstocks' actual confirmed `/order` payload: `security_id` (not a
ticker), `algo_id` (`99999` for regular orders — required or the order is
rejected), `segment`, `validity`, and the rest. Also wraps `/order-book`,
`/positions`, `/funds`, and `/market/quotes/ltp`.

**Statutory cost engine (`costs.py`)**
Computes the real cost of a round trip (one entry + one exit): brokerage
(INDstocks' confirmed flat Rs.5/order), STT (0.025% on the sell side for
intraday, 0.1% on both sides for delivery), NSE exchange transaction charges,
SEBI turnover fee, stamp duty (buy side only), and 18% GST on
brokerage + exchange charges + SEBI charges. Every closed position logs both
gross P&L and net-of-cost P&L — the number that actually says whether a
strategy has real edge, since a small scalp can look profitable gross and be
a loser once flat brokerage and GST are subtracted from a small move. All
rates are configurable, since government/exchange rates do change.

**Exit logic (`exits.py`, `positions.py`)**
Every position this bot opens is tracked in a persisted `PositionStore` (a
JSON file, not just memory — survives a restart) with its own stop-loss,
target, and max-hold-time, computed from `STOP_LOSS_PCT` / `TARGET_PCT` /
`MAX_HOLD_MINUTES` at entry. `ExitManager.check_and_exit_all()` runs at the
top of every loop tick, **before** any new entries are considered — closing
existing risk always takes priority over opening new risk. Whichever of
stop-loss, target, or time-exit triggers first closes the position; the exit
order is a LIMIT order at current LTP, consistent with the no-MARKET-orders
price-collar policy everywhere else (the kill switch's `flatten_all()`
remains the one deliberate MARKET-order exception, since its job is
guaranteed exit, not price control). A failed exit order keeps the position
tracked for retry next tick rather than silently dropping it.

**Kill switch (`risk_governor.py::flatten_all`)**
Cancels every open order **and** squares off every open position — not just
one or the other. Triggered by the daily drawdown breach, or manually via
Telegram.

### 5. Staying in sync and staying safe

**Order/position reconciliation (`reconciliation.py`)**
`PositionStore` is only rebuilt from the broker on startup. Between restarts,
local and broker state can drift — a fill the process missed on a crash, a
manual trade on the INDstocks app, a partial fill mishandled somewhere.
`ReconciliationService.reconcile()` runs on its own interval (default 60s,
independent of the main tick rate) and cross-checks local open positions
against the broker's own `/positions`. On any mismatch it does **not**
auto-correct — guessing which side is right is how a data bug becomes a
trading bug — it logs loudly and sends a Telegram alert so a human decides.

**Telegram alerts + authenticated kill switch (`telegram_alerts.py`)**
Sends trade/exit notifications and critical alerts (drawdown breach, failed
exit, reconciliation mismatch) to one chat. `/halt` and `/flatten` are
restricted to `TELEGRAM_OWNER_CHAT_ID` — messages from any other chat are
silently ignored, so the bot doesn't even confirm its own existence to a
stranger who finds the bot username.

**Audit trail (`audit.py`)**
Append-only JSONL, one line per scoring decision (traded or skipped) —
security, Jev conviction/confidence, action, latency, OTR check. When a
position closes, a second `outcome_update` record appends the fill price,
gross P&L, net-of-cost P&L, and the full cost breakdown, linked by
`decision_id`. This is both the SEBI Order-to-Trade-Ratio compliance record
and the dataset a future calibration job checks Jev's conviction scores
against.

**Jev calibration analysis (`calibration.py`, `scripts/run_calibration_report.py`)**
Tests the load-bearing assumption of the whole system: does Jev's conviction
score actually predict profitable trades? Reads `audit_trail.jsonl`, joins
each decision to its closed-trade outcome, and buckets by conviction (and
separately by confidence) to report mean/median net P&L and win rate per
bucket. Deliberately a report for a human to read, not a signal the bot acts
on — the 0.80 threshold in `RiskConfig` stays a config constant until this
analysis, run on a real sample, says otherwise. Flags explicitly when a
bucket's sample size is too small to mean anything (fewer than 30 trades),
rather than implying false confidence from a handful of paper trades. See
`docs/INTELLIGENCE_ROADMAP.md` for the full hypothesis this tests (H1, H2)
and everything queued up behind it.

### 6. Intelligence layer — see `docs/INTELLIGENCE_ROADMAP.md`

Everything above is plumbing: it gets an order to the exchange correctly,
safely, with the right costs and risk limits. `docs/INTELLIGENCE_ROADMAP.md`
is the separate, explicit plan for the part that actually decides whether
any of this makes money — what data the system needs beyond a live tick,
what falsifiable hypotheses get tested before any of them become trading
logic, and what algorithms only get built once a hypothesis survives a real
backtest. Built so far off that backlog:

- **`calibration.py`** (H1/H2/H3) — see section 5 above.
- **`historical_data.py`** — loads OHLCV bars for backtesting. The primary
  path, `load_from_csv()`, needs no broker API at all (point it at any
  standard OHLCV export), so the backtest engine below is never blocked on
  an unconfirmed contract. `fetch_from_indstocks()` is a best-effort
  placeholder for later automation, gated the same way as the WebSocket
  feed.
- **`backtest.py`** — an event-driven backtest engine that reuses the
  *exact same* exit logic (`exits.determine_exit_reason`) and cost engine
  (`costs.compute_round_trip_cost`) that live trading uses, so a backtest
  result means what a live result would mean. Strategy-agnostic — takes any
  `bars_seen_so_far -> enter_long?` function, with no lookahead (a decision
  on bar *i* fills at bar *i+1*'s open). First smoke-test result: a naive
  momentum rule on synthetic data looked marginally profitable gross but
  was **net negative after real costs** — exactly the failure mode this
  engine exists to catch before it costs real money.

### 7. Wiring and operations

**Main trading loop (`main.py`)**
Every tick, in order: reconcile (if due) -> check exits -> look for new
entries across the watchlist, skipping any symbol already held. Ties
together every module above.

**Single-owner token refresh (`auth.py`, `scripts/refresh_token.py`)**
INDstocks issues one live TOTP token at a time — generating a new one
invalidates the last. `scripts/refresh_token.py` is meant to run from exactly
one cron job; every other process (`main.py`, the Telegram bot) only ever
*reads* the cached token via `auth.get_cached_token()`, never regenerates it.

**Config via env vars (`config.py`)**
No secrets in code. Every setting has a documented default in `.env.example`.
A missing required variable fails loudly at startup, not mid-loop.

**Tests & CI (`tests/`, `.github/workflows/ci.yml`)**
52 tests covering risk governor logic, exit-reason branches, portfolio risk
caps, cost-engine math, reconciliation mismatch detection, watchlist
resolution priority, retry/backoff behavior, news filtering/caching, auth
cache round-trips, and Telegram authorization. Runs on every push to `main`.

---

## Open items — confirm before relying on these

- **Instruments Master CSV column names** (`instruments.py`) — the general
  shape (symbol -> `security_id` + `scrip_code` like `NSE_2885`) is
  corroborated by a third-party INDstocks MCP tool, but the literal CSV
  header names are unconfirmed against `api-docs.indstocks.com/instruments/`.
- **WebSocket protocol** (`market_data.py`) — connection URL and
  message shapes are a best-effort placeholder; `api-docs.indstocks.com/Websockets/`
  wasn't reachable while building this. Gated behind `WEBSOCKET_ENABLED=false`
  for exactly this reason, with a REST fallback so nothing depends on it
  being correct.
- **Traded instrument** — nothing yet resolves to a specific tradable
  instrument (Nifty future vs. ETF vs. individual equities); that's a
  strategy decision, not a wiring gap.

## Known gaps / not started

- No integration test against a real or sandboxed INDstocks/Jev response —
  only unit-level coverage exists so far.
- Jev shadow-mode logging hasn't accumulated a real sample yet — `calibration.py`
  is built and tested, but needs actual paper-trading volume before its
  output means anything (see `docs/INTELLIGENCE_ROADMAP.md`).
- No backtesting harness.
- No GTT/exchange-side bracket orders — exits are client-side (see Roadmap).
- See `docs/INTELLIGENCE_ROADMAP.md` for the much larger backlog of data,
  hypotheses, and algorithms behind the trading intelligence itself, as
  opposed to the infrastructure around it.

---

## Roadmap — next set of features

Grouped by priority. P0 blocks paper trading from being meaningful; P1 is
needed before real capital; P2 is production hardening once P0/P1 are done.

### P0 — makes paper trading meaningful — all done

- [x] Real market-data feed (`market_data.py`) *(v3)*
- [x] Real watchlist/universe selection (`watchlist.py`) *(v3)*
- [x] News/filing ingestion (`news_feed.py`) *(v3)*
- [x] Confirm Jev's real endpoint + response schema *(v3)*
- [x] **Exit logic** — stop-loss, target, time-based *(v2)*
- [ ] Confirm Instruments Master CSV schema — still open, see Open Items above

### P1 — before real capital

- [x] Statutory cost engine (`costs.py`) — net P&L now logged alongside gross on every exit *(v4)*
- [x] Portfolio-level risk (`portfolio_risk.py`) — max concurrent positions + max deployed capital *(v4)*
- [x] Order/position reconciliation loop (`reconciliation.py`) — runs every 60s, alerts on mismatch, does not auto-correct *(v4)*
- [x] Reconnect/backoff for the market-data feed and for LTP/Jev reads *(v3)*
- [x] Scheduled job to score `audit_trail.jsonl` outcomes against logged Jev conviction, validating the 0.80 threshold instead of assuming it -- `calibration.py` built and tested; still needs real trading volume to produce a meaningful result *(v5)*
- [x] Backtesting harness against historical data, isolated from the live/paper code path -- `backtest.py`, reuses the same exit/cost logic as live trading *(v6)*
- [ ] GTT / bracket orders — exchange-side stop-loss + target placed atomically with entry (client-side stops in `exits.py` die if the process crashes; this is the remaining gap that closes)

### P2 — production hardening

- [ ] Structured logging shipped somewhere durable, not just stdout
- [ ] Metrics/dashboard: live P&L, open exposure, win rate, Jev calibration drift over time
- [ ] Health-check + external uptime monitor, distinct from the Telegram heartbeat
- [ ] Multi-instrument, multi-strategy support (currently one strategy, one signal type)
- [ ] Config validation on startup — catch a bad `.env` before market open, not mid-loop
- [ ] Secrets management beyond `.env` once this runs on a real server long-term

---

## Changelog

### 2026-09-25 (v6)
- **H3 news comparison**: `audit.py` now records `had_news` on every
  decision; `calibration.py` reports a three-way with-news / without-news /
  unknown net-P&L split.
- **`historical_data.py`**: `load_from_csv()` (always works, no API
  dependency) plus `fetch_from_indstocks()` (best-effort placeholder,
  same "confirm before relying on this" treatment as `market_data.py`'s
  WebSocket feed). Decouples the backtest engine from any unconfirmed
  broker contract.
- **`backtest.py`**: event-driven, strategy-agnostic backtest engine.
  Reuses `exits.determine_exit_reason()` and `costs.compute_round_trip_cost()`
  directly from the live code path -- not a reimplementation -- so a
  backtest result means the same thing a live result would. No-lookahead
  fill model (signal on bar *i* fills at bar *i+1*'s open). First
  smoke-test: a naive momentum rule on synthetic data was gross-positive
  but net-negative after real costs, demonstrating exactly why this engine
  needed to exist before any hypothesis test could be trusted.
- 20 new tests (news-comparison bucketing, CSV round-trip, backtest exit
  branches, cost-sensitivity, force-close-at-end-of-data). Full suite:
  72/72 passing.
- `docs/INTELLIGENCE_ROADMAP.md` and `FEATURES.md` updated: backlog items
  2-4 (news comparison, historical data, backtest engine) marked done; H4/H5
  momentum and mean-reversion tests on *real* (not synthetic) historical
  data are next.

### 2026-09-25 (v5)
- **`docs/INTELLIGENCE_ROADMAP.md`**: the full plan for the trading
  intelligence itself, as distinct from the infrastructure around it --
  sequenced Data -> Hypotheses -> Algorithms, since testing a hypothesis
  needs data that doesn't exist yet for most of it, and an algorithm should
  only get built once its hypothesis has survived a real backtest. Lists 6
  data sources, 7 falsifiable hypotheses (each with what it needs and its
  kill criteria), and 6 candidate algorithms, plus a sequenced 8-item
  backlog for building them one at a time.
- **`calibration.py`**: item #1 off that backlog. Reads `audit_trail.jsonl`,
  joins every decision to its closed-trade outcome, and buckets by Jev
  conviction and confidence to report mean/median net P&L and win rate per
  bucket -- the first real test of whether Jev's score means anything,
  using data the system was already collecting. Explicitly flags when a
  bucket's sample is too small to trust (<30 trades) rather than implying
  false confidence from early paper trading. `scripts/run_calibration_report.py`
  is the CLI entrypoint.
- 8 new tests (empty/missing log handling, decision-outcome joining,
  bucketing correctness, win-rate math, small-sample flagging). Full suite:
  60/60 passing.

### 2026-09-25 (v4)
- **Statutory cost engine** (`costs.py`): brokerage, STT (intraday vs.
  delivery rates), NSE exchange transaction charges, SEBI turnover fee, stamp
  duty, and GST, computed per round trip. `exits.py` now logs net-of-cost P&L
  alongside gross on every closed position, and the Telegram exit
  notification shows gross, costs, and net separately.
- **Portfolio-level risk** (`portfolio_risk.py`): `MAX_CONCURRENT_POSITIONS`
  and `MAX_DEPLOYED_CAPITAL_PCT` caps checked against the live `PositionStore`
  before a new entry is placed, closing the gap where per-order sizing alone
  could never see aggregate exposure across several open positions.
- **Reconciliation loop** (`reconciliation.py`): runs on its own 60s
  interval, cross-checks local open positions against the broker's real
  `/positions`, and alerts (without auto-correcting) on any mismatch.
- 16 new tests (cost-engine math, portfolio risk caps, reconciliation
  mismatch detection). Full suite: 52/52 passing.
- **`FEATURES.md` restructured** from a terse table into full documentation,
  organized by stage of the trading loop, meant to double as an onboarding
  doc for the app as a whole rather than just a changelog.

### 2026-09-25 (v3)
- **Real watchlist** (`watchlist.py`): `WATCHLIST_SYMBOLS` env var -> `WATCHLIST_FILE` JSON file -> small built-in default. Replaces the hardcoded `["RELIANCE"]`.
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
- Initial scaffold pushed: three-tier architecture (Jev scoring -> risk governor -> INDstocks execution), auth, risk governor, execution gateway, Telegram alerts, audit trail, main loop, test suite, CI.
- Added prioritized roadmap (P0/P1/P2) for the next set of features.
