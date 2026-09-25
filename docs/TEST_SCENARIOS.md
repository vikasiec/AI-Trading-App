# Test Scenarios

A hands-on guide for trying this out after you `git clone` it locally. Ordered
from "needs nothing but the repo" to "needs real credentials, still no real
money." Every scenario says exactly what to run and what you should see.

---

## 0. Setup (do this once)

```bash
git clone https://github.com/vikasiec/AI-Trading-App.git
cd AI-Trading-App
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e . -r requirements.txt
cp .env.example .env
```

Leave `.env` mostly untouched for scenarios 1-4 below — they don't need real
credentials. You'll fill in real values starting at scenario 5.

**Sanity check — run this first:**
```bash
pytest tests/ -v
```
Expect `79 passed`. If anything fails here, stop and fix the environment
before trying the scenarios below — they build on the same code.

---

## 1. Calibration report on a synthetic audit trail

No credentials needed. This exercises `calibration.py` against fake data you
generate yourself, so you can see what the real report will eventually look
like once the bot has actually traded.

```bash
python3 - <<'EOF'
import json, random

random.seed(1)
records = []
for i in range(60):
    conviction = random.uniform(0.5, 0.95)
    confidence = random.uniform(0.5, 0.95)
    # deliberately correlated with conviction, so you can see H1 "work"
    pnl = (conviction - 0.7) * 500 + random.gauss(0, 80)
    did = f"SEC{i}_1"
    records.append({"decision_id": did, "security_id": f"SEC{i}", "jev_conviction": conviction,
                     "jev_confidence": confidence, "action": "BUY", "latency_ms": 120,
                     "had_news": random.choice([True, False, None])})
    records.append({"decision_id": did, "type": "outcome_update", "fill_price": 100.0,
                     "realized_pnl": pnl, "net_pnl": pnl, "costs": {}})

with open("demo_audit_trail.jsonl", "w") as f:
    for r in records:
        f.write(json.dumps(r) + "\n")
print("wrote demo_audit_trail.jsonl")
EOF

python scripts/run_calibration_report.py demo_audit_trail.jsonl
```

**What to look for:** with 60 fake trades (above the 30-sample threshold),
the conviction buckets should show a rough upward trend in mean net P&L as
conviction increases -- because the synthetic data was built that way. Try
changing `pnl = (conviction - 0.7) * 500 + ...` to `pnl = random.gauss(0, 80)`
(no relationship to conviction) and rerun -- the buckets should flatten out.
This is exactly what H1 in `docs/INTELLIGENCE_ROADMAP.md` is checking for on
real data.

---

## 2. Backtest a strategy against synthetic price data

Also no credentials needed. Exercises `backtest.py` and `historical_data.py`.

```bash
python3 - <<'EOF'
import csv, random
from datetime import datetime, timedelta

random.seed(7)
base = datetime(2026, 1, 2, 9, 15)
price = 100.0
rows = []
for i in range(500):
    price *= (1 + random.gauss(0, 0.003))
    rows.append([
        (base + timedelta(minutes=i)).isoformat(),
        round(price, 2), round(price * 1.002, 2), round(price * 0.998, 2), round(price, 2), 1000,
    ])

with open("demo_bars.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
    w.writerows(rows)
print("wrote demo_bars.csv")
EOF

python3 - <<'EOF'
from pathlib import Path
from jev_indstocks_trader.historical_data import load_from_csv
from jev_indstocks_trader.backtest import run_backtest

bars = load_from_csv(Path("demo_bars.csv"))

def momentum_strategy(seen):
    if len(seen) < 6:
        return False
    return seen[-1].close > seen[-6].close * 1.005

result = run_backtest(bars, momentum_strategy, qty=10, stop_loss_pct=0.01, target_pct=0.02, max_hold_bars=30)
print(result.summary_text())
for t in result.trades[:5]:
    print(f"  {t.entry_time} -> {t.exit_time}  {t.exit_reason:10s}  gross={t.gross_pnl:8.2f}  net={t.net_pnl:8.2f}")
EOF
```

**What to look for:** compare `total_gross_pnl` to `total_net_pnl` -- the gap
is the "cost drag" line, i.e. what STT/brokerage/GST actually cost this
strategy. Try writing your own `strategy` function (mean-reversion: enter
when price is *below* a recent average instead of above) and compare. Try
`qty=1000` instead of `10` and watch how position size interacts with
cost drag.

---

## 3. Break the risk governor on purpose

No credentials needed -- these are pure unit tests you can read and modify
directly, which is often the fastest way to understand a rule.

```bash
pytest tests/test_risk_governor.py -v
pytest tests/test_portfolio_risk.py -v
pytest tests/test_costs.py -v
```

Then open `tests/test_risk_governor.py` and try:
- Change `test_drawdown_blocks_trade_and_triggers_flatten`'s `realized_pnl`
  from `-3000` to `-100` and rerun -- the test should now fail, because a
  1% drawdown on Rs.1,00,000 equity shouldn't trigger the (default 2%)
  limit. This is a good way to build intuition for exactly where the line
  is.
- In `tests/test_portfolio_risk.py`, change `MAX_CONCURRENT_POSITIONS` from
  `"2"` to `"5"` in `test_blocks_when_max_concurrent_positions_reached` and
  watch the test fail -- you've just disabled the cap the test was checking.

---

## 4. Watch the kill switch cascade

```bash
pytest tests/test_exits.py -v -k gtt
pytest tests/test_reconciliation.py -v
```

Read `test_gtt_cancelled_on_live_exit` in `tests/test_exits.py` alongside
`exits.py::_execute_exit` -- trace through what happens when a position with
a GTT order hits its stop-loss: the client-side SELL fires, *then* the GTT
is cancelled, *then* the audit trail is updated. Try reordering those three
steps in `exits.py` and see which tests catch the bug (they should).

---

## 5. Paper-trade against real INDstocks + Jev credentials

**This needs real credentials but places zero real orders.** Fill in your
real `.env` values:

```
INDSTOCKS_CLIENT_ID=...
INDSTOCKS_MPIN=...
INDSTOCKS_TOTP_SECRET=...
JEV_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_OWNER_CHAT_ID=...
PAPER_TRADING=true        # leave this exactly as-is
```

Generate a token once (this is the one command that touches the real
INDstocks API):
```bash
python scripts/refresh_token.py
```

Run the loop:
```bash
python -m jev_indstocks_trader.main
```

**What to look for:** log lines for each watchlist symbol every tick --
`Skipped <symbol>: <reason>` for most of them (that's normal; most signals
should be rejected), and occasionally `BUY (paper)` if a signal clears every
gate. Let it run for a few minutes, then Ctrl+C and check:

```bash
tail -5 audit_trail.jsonl
```

Every decision -- traded or skipped -- should be there.

---

## 6. Trigger the daily drawdown kill switch (paper mode, safe)

Temporarily set an absurdly tight limit so it trips almost immediately:
```
# in .env:
DAILY_LOSS_LIMIT_PCT=0.0001
```
Run `main.py` again. Even a tiny unrealized/realized loss on the account
should now breach the limit and you should see `KILL SWITCH TRIGGERED` in
the logs. **Remember to set `DAILY_LOSS_LIMIT_PCT` back to something sane
(0.02) afterward** -- this is deliberately testing the alarm, not a setting
to leave this way.

---

## 7. Test the Telegram kill switch and its authorization check

With the bot running (scenario 5), message your bot `/halt` from
`TELEGRAM_OWNER_CHAT_ID` -- you should get "EMERGENCY: Kill-switch
triggered" back and see `flatten_all()` run in the logs. Then have a
**different** Telegram account message the same bot `/halt` -- nothing
should happen at all, not even an error reply (see `telegram_alerts.py`'s
"don't confirm the bot exists to strangers" comment). This is
`test_unauthorized_chat_cannot_trigger_kill_switch` in
`tests/test_telegram_alerts.py`, made real.

---

## 8. Simulate a reconciliation mismatch

With the bot stopped, hand-edit the local position store to disagree with
reality:
```bash
cat ~/.indstocks/open_positions.json
```
Add a fake entry for a `security_id` you know isn't actually held (copy the
JSON shape from an existing entry, or from `positions.py`'s `Position`
fields), save it, then restart `main.py`. Within 60 seconds you should see a
`Reconciliation mismatch` error in the logs and a Telegram critical alert --
and, per `reconciliation.py`'s design, it will **not** auto-correct the
file. You have to fix it yourself, which is the point.

---

## Quick reference: things worth trying, in one line each

- Set `WATCHLIST_SYMBOLS=INFY,WIPRO` and confirm the loop only scores those two.
- Set `MAX_CONCURRENT_POSITIONS=1` and confirm a second signal gets skipped with `max_concurrent_positions_reached` while one position is open.
- Set `STOP_LOSS_PCT=0.10` (very wide) and `TARGET_PCT=0.001` (very tight) and watch paper trades exit almost immediately on `target`.
- Delete `audit_trail.jsonl` and rerun the calibration report -- confirm it says `0 closed trades` instead of erroring.
- Run `scripts/refresh_token.py` twice within 60 seconds -- confirm the second call gets throttled (per `auth.py`'s documented 1-token-per-60-seconds rule).
- Set `NEWS_RSS_FEEDS=` (empty) and confirm the log says Jev is scoring on price data alone.
- Set `PAPER_TRADING=false` **without** real capital you're willing to risk -- don't. If you want to see what changes, read `main.py`'s `if not cfg.risk.paper_trading:` branches instead of flipping the flag.
