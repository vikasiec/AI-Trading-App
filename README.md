# Jev AI + INDstocks Automated Trading System

AI scores conviction; deterministic code owns sizing, risk, and order lifecycle.

Full design doc, architecture diagram, and change log vs. the original draft:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

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
  risk_governor.py      sizing, drawdown, idempotency, kill switch
  execution_gateway.py  INDstocks order placement / order book / funds
  telegram_alerts.py    alerts + authenticated remote kill switch
  audit.py               JSONL audit trail for OTR review + Jev calibration
  main.py                wires it all together, the trading loop entrypoint
scripts/
  refresh_token.py      cron entrypoint -- the ONE process allowed to refresh the token
tests/                   pytest suite covering the risk governor and auth/telegram auth checks
docs/ARCHITECTURE.md     full design doc + Mermaid architecture diagram
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

- `main.py`'s watchlist and `feature_prep.py`'s data source are stubs --
  wire in your real tick/news feed.
- `instruments.py`'s CSV column names are a best guess pending
  confirmation against the current Instruments Master schema.
- `jev_client.py`'s endpoint URL should be confirmed against Jev's
  current docs before going live -- it's a young, fast-moving API.

## Safety

- Keep `PAPER_TRADING=true` until you've completed shadow-mode Jev
  logging and the paper-trading run described in `docs/ARCHITECTURE.md` §10.
- The kill switch (`/halt` or `/flatten` in Telegram, or the daily
  drawdown breach) cancels open orders **and** squares off positions.
- Never run `scripts/refresh_token.py` from more than one place --
  doing so invalidates the token other processes are using.
