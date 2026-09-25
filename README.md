# Jev AI + INDstocks Automated Trading System

AI scores conviction; deterministic code owns sizing, risk, and order lifecycle.

Full design doc, architecture diagram, and change log vs. the original draft:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The separate plan for the
trading *intelligence* itself -- what data, what hypotheses, what
algorithms, in that order -- is [`docs/INTELLIGENCE_ROADMAP.md`](docs/INTELLIGENCE_ROADMAP.md).
For hands-on things to try after cloning this, see
[`docs/TEST_SCENARIOS.md`](docs/TEST_SCENARIOS.md) -- every command in it
has been run and verified.

## Status

**Paper-trading scaffold.** No live capital should touch this until every
item in the [Pre-Flight Checklist](docs/ARCHITECTURE.md#12-pre-flight-checklist)
is done. `PAPER_TRADING=true` is the default in `.env.example` -- keep it
that way until you mean to change it.

## Layout

```
src/jev_indstocks_trader/
  config.py            env-var driven config, no secrets in code
  auth.py               INDstocks TOTP auth -- single-owner token refresh
  jev_client.py         Jev AI (System One) conviction scoring
  feature_prep.py       compresses market context for Jev
  instruments.py        symbol -> security_id lookup (Instruments Master)
  watchlist.py          real watchlist source (env var, file, or default)
  news_feed.py           RSS/Atom headline ingestion for feature_prep
  market_data.py         WebSocket tick feed with reconnect/backoff (gated, see below)
  retry.py                retry/backoff for flaky reads (not order placement)
  risk_governor.py      sizing, drawdown, idempotency, kill switch
  portfolio_risk.py       aggregate exposure caps across all open positions
  costs.py                statutory cost engine (STT/GST/exchange/SEBI/stamp duty)
  execution_gateway.py  INDstocks order placement / order book / funds
  positions.py            persisted open-position store with exit rules
  exits.py                stop-loss / target / time-based exit checks
  reconciliation.py       periodic local-vs-broker position cross-check
  gtt_orders.py            exchange-side OCO bracket orders (gated, best-effort)
  telegram_alerts.py    alerts + authenticated remote kill switch
  audit.py               JSONL audit trail for OTR review + Jev calibration
  calibration.py          analyzes audit_trail.jsonl: does Jev's score predict outcomes?
  historical_data.py       OHLCV bar loading (CSV always works; INDstocks fetch is a placeholder)
  backtest.py               event-driven backtest engine, reuses live exit/cost logic
  main.py                wires it all together, the trading loop entrypoint
scripts/
  refresh_token.py      cron entrypoint -- the ONE process allowed to refresh the token
  run_calibration_report.py  CLI: prints the Jev calibration report
tests/                   pytest suite (86 tests) covering risk governor, portfolio risk, costs, auth, exits, GTT, reconciliation, watchlist, retry, news/ticks, calibration, historical data, backtesting, Telegram auth/polling, and main._tick collar/idempotency paths
docs/ARCHITECTURE.md     full design doc + Mermaid architecture diagram
docs/INTELLIGENCE_ROADMAP.md  data / hypotheses / algorithms backlog for the trading intelligence itself
FEATURES.md               living feature list, roadmap, and changelog
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e . -r requirements.txt
cp .env.example .env   # fill in real credentials -- .env is gitignored
```

Run the test suite:

```bash
pytest tests/ -v
```

Run the trading loop (paper mode by default):

```bash
python -m jev_indstocks_trader.main
```

Schedule the token refresh (single owner process only -- see the big
warning in `scripts/refresh_token.py` and `docs/ARCHITECTURE.md` §5):

```
0 9 * * 1-5 /usr/bin/python3 /path/to/scripts/refresh_token.py
```

## What's still a placeholder

See [`FEATURES.md`](FEATURES.md) for the full, currently-maintained list.
As of this writing:

- `instruments.py`'s exact CSV column names are still a best guess --
  the general symbol → security_id/scrip_code shape is corroborated by a
  third-party INDstocks MCP tool, but not the literal header names.
- `market_data.py`'s WebSocket protocol (URL, subscribe/tick message
  shapes) is a best-effort placeholder -- **gated behind
  `WEBSOCKET_ENABLED=false`** for exactly this reason. `main.py` falls
  back to the already-confirmed REST `/market/quotes/ltp` poll whenever
  the feed is off or has no fresh tick.
- No traded instrument is chosen yet (Nifty future vs. ETF vs.
  individual equities) -- that's a strategy decision, not a wiring gap.

## Safety

- Keep `PAPER_TRADING=true` until you've completed shadow-mode Jev
  logging and the paper-trading run described in `docs/ARCHITECTURE.md` §10.
- The kill switch (`/halt` or `/flatten` in Telegram, or the daily
  drawdown breach) cancels open orders **and** squares off positions.
- Never run `scripts/refresh_token.py` from more than one place --
  doing so invalidates the token other processes are using.
