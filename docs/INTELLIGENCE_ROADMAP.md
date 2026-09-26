# Intelligence Roadmap: Data, Hypotheses, Algorithms

Everything in `FEATURES.md` so far is plumbing: it gets an order to the
exchange correctly, safely, and with the right costs and risk limits. None of
it answers the actual question a trading system exists to answer: **does
anything this bot looks at actually predict a profitable trade?**

Right now the honest answer is "unknown." The 0.80 Jev conviction threshold
is a number that was typed into `.env.example` because it looked reasonable,
not because it was measured. This document is the plan for closing that gap
-- in order, because each layer depends on the one before it:

```
DATA  -->  HYPOTHESES  -->  ALGORITHMS
(what the        (falsifiable claims        (code that acts on
 system can        about what predicts        a hypothesis that
 observe)          a profitable trade)        has actually survived
                                               testing)
```

Skipping straight to algorithms without validated hypotheses is how you get
a bot that trades confidently on noise. The backlog below is sequenced to
prevent that.

---

## Layer 1: Data

Everything downstream needs this. A hypothesis can't be tested, and an
algorithm can't be trusted, on data the system doesn't have. Prioritized by
what unblocks the most downstream work per unit of effort.

| # | Data | Why it's needed | Status |
|---|---|---|---|
| D1 | **Audit trail of Jev scores + outcomes** (`audit_trail.jsonl`) | The single most important dataset in the whole system -- every conviction score this bot has ever produced, linked to what actually happened. Without this, "is Jev any good" is unanswerable. | **Already being collected** (`audit.py`), just not yet analyzed |
| D2 | **Historical OHLCV bars** (per symbol, at least daily, ideally intraday) | Needed to backtest ANY price-based hypothesis before risking capital on it. Currently the only price data the system has is the live tick. | **CSV-loading path done (v6)**; `fetch_from_indstocks()` still a placeholder, same caveat as the Instruments Master and WebSocket gaps |
| D3 | **Corporate actions / earnings calendar** | Distinguishes "this stock moved because of news" from "this stock moved because of an earnings surprise" -- needed to interpret both Jev's headline-driven context and any news hypothesis | Not started |
| D4 | **Sector/index classification per symbol** | Needed for sector-rotation, relative strength, and concentration caps | **Static map + cap live** (`sectors.py`, `MAX_SECTOR_CAPITAL_PCT`). Not a living industry feed. |
| D5 | **India VIX / market-wide volatility regime** | Price action means different things in calm vs violent tape | **D5-lite done** (`regime.py` on own-bar realized vol). India VIX / Nifty realized vol not wired. |
| D6 | **Options data (PCR, OI, IV)** | Contrarian index sentiment | Backlog — do not start until H1/H4/H5 have real-data verdicts |
| D7 | **Index weather (Nifty / Bank Nifty LTP + day %)** | Stock longs in a falling index are a different trade | Config stub `INDEX_SCRIP`; quote not yet folded into the live vote |
| D8 | **Spread / tick / ADV liquidity** | Stops trading names you cannot exit | Not started — use a short liquid watchlist until then |
| D9 | **Corporate calendar + results dates** | Block or shrink size into results | Not started (was D3) |
| D10 | **Multi-symbol OHLCV store** | Watchlist backtests and relative strength | **Runner done (v17+):** `scripts/run_watchlist_backtest.py`. You still supply the CSVs. |

**D1 needs nothing new built except an analysis script** -- it's already
flowing in. That's why it's the first thing this roadmap implements (see
`calibration.py` below).

---

## Layer 2: Hypotheses

Each hypothesis is written as a falsifiable claim, with what data it needs
and how it gets killed or confirmed. **None of these are implemented as
trading logic yet** -- that's the point. A hypothesis earns its way into an
algorithm only after it survives a backtest on real history, not before.

| # | Hypothesis | Needs | Test | Kill criteria |
|---|---|---|---|---|
| H1 | **Jev's conviction score is positively correlated with forward net P&L** -- this is the load-bearing assumption of the entire system and hasn't been checked yet | D1 | Bucket every scored decision by conviction (e.g. 0.5-0.6, 0.6-0.7, ...), compare mean net P&L per bucket | If the buckets aren't monotonically improving, the 0.80 threshold is arbitrary and the whole scoring approach needs rethinking before anything else matters |
| H2 | **Jev's confidence field predicts *consistency* of outcome, not direction** -- high confidence should mean low variance in outcome, not necessarily a better mean | D1 | Compare outcome variance across confidence buckets, holding conviction roughly constant | If confidence doesn't reduce variance, it's not adding information and the governor's confidence floor is dead weight |
| H3 | **News-driven entries outperform price-only entries** -- justifies the cost/complexity of `news_feed.py` | D1 (split by whether headlines were non-empty at scoring time) | Compare net P&L for decisions with vs. without matched headlines | If no difference, news ingestion isn't pulling its weight and can be simplified or dropped |
| H4 | **Short-horizon momentum**: a stock up >X% on above-average volume tends to continue for the next N minutes | D2 | Backtest a simple momentum rule against historical intraday bars | Standard backtest kill: doesn't beat a random-entry baseline net of costs (`costs.py`) |
| H5 | **Mean reversion**: a stock that deviates >X% from same-day VWAP reverts before close | D2 | Same backtest harness as H4, opposite direction | Same as H4 |
| H6 | **Jev's usefulness depends on regime** -- it may be predictive in trending markets and noise in choppy ones, or vice versa | D1 + D5 | Split H1's bucket analysis by a volatility-regime label | If true, the risk governor's threshold should be regime-conditional, not a flat constant |
| H7 | **Sector rotation as a leading indicator** -- money flowing into a sector this week predicts individual-stock strength next week | D3, D4 | Backtest sector-relative-strength ranking against forward returns | Standard backtest kill |

**Sequencing:** H1 and H2 use only D1, which already exists -- they should
run first and will likely reshape everything else (there's no point
designing H4-H7 around a conviction threshold that H1 might prove is
miscalibrated). H4/H5/H7 are blocked on D2/D3/D4, which aren't built yet.

---

## Layer 3: Algorithms

Only built once a hypothesis above has actually survived its test. Listed
here so the shape of "what this becomes" is visible, not because any of it
should be started now.

| # | Algorithm | Depends on | Purpose |
|---|---|---|---|
| A1 | **Calibration transform** (e.g. isotonic regression or simple bucket-lookup) mapping Jev's raw score to a measured probability of profit | H1 confirmed | Replaces the flat 0.80 cutoff with a number that means what it claims to mean |
| A2 | **Regime detector** | H6 | **Scaffold live (v9/v17):** `regime.py` + `entry.rule_vote` switches momentum vs mean-revert by label. Not VIX. |
| A3 | **Momentum strategy module** | H4 confirmed on *real* CSVs | **Code exists** (`strategies.momentum_long`). Live only via `ENTRY_MODE=rule` or `jev_and_rule`. Not proven. |
| A4 | **Mean-reversion strategy module** | H5 confirmed on *real* CSVs | Same as A3 (`mean_reversion_long`). |
| A5 | **Sector-relative-strength ranker** | H7 confirmed | Not started. Watchlist is still static. |
| A6 | **Backtest engine** | D2 | **Done.** Single-file + watchlist CSV runner. Does not replay Jev. |
| A7 | **Entry-mode combiner** | — | **Done (v17):** `ENTRY_MODE=jev\|rule\|jev_and_rule`. |

**A6 is the actual next infrastructure priority** once D1-derived hypotheses
(H1, H2, H3) are done -- there is no honest way to test H4/H5/H7 without it.

---

## Full backlog, sequenced

This is the order to actually build in. Each item is small enough to ship
and evaluate on its own before starting the next.

1. **`calibration.py`** -- analyze `audit_trail.jsonl`, test H1 and H2. **Done (v5).**
2. **News-vs-no-news comparison** -- H3. **Done (v6)**: `had_news` is now
   recorded on every decision and `calibration.py` reports a three-way split
   (with news / without news / unknown, for decisions made before any news
   source was configured).
3. **Historical data ingestion** (D2). **Done (v6)**, via a route that avoids
   the unconfirmed-API problem entirely: `historical_data.py`'s primary path
   is `load_from_csv()`, which needs no INDstocks API contract at all --
   point it at any OHLCV export (NSE's own, a broker's, or INDstocks'
   Historical Data endpoint downloaded by hand). A `fetch_from_indstocks()`
   placeholder exists for later automation but nothing depends on it being
   correct yet.
4. **Backtest engine** (A6). **Done (v6)**: `backtest.py` reuses the exact
   same `exits.determine_exit_reason()` and `costs.compute_round_trip_cost()`
   that live trading uses, strategy-agnostic (takes any `bars -> enter_long?`
   function). First real result: a naive momentum rule (price up >0.5% over
   5 bars) backtested on synthetic data looked marginally profitable gross
   (+Rs.46 over 500 bars) but was **net negative after real costs**
   (-Rs.148) -- exactly H4's kill criteria, and exactly why "gross P&L on a
   backtest" is not a number to trust without this engine.
5. **Momentum and mean-reversion hypothesis tests** (H4, H5). **Harness
   done (v9):** `strategies.py` + `hypothesis.evaluate_vs_baseline` +
   `scripts/run_hypothesis_tests.py`. A hypothesis survives only if it
   beats a cadence baseline *net of costs* on the CSV you pass in.
   Point the script at a real NSE/broker export to actually test H4/H5;
   unit tests use constructed series to lock the rules, not to claim
   the hypotheses are true.
6. **Regime detector** (A2) -- only once H6 shows regime actually matters for Jev's calibration.
7. **Corporate actions + sector classification** (D3, D4) -- unlocks H7 and a sector-concentration portfolio-risk check.
8. **Momentum/mean-reversion strategy modules** (A3, A4) and **sector rotation** (A5) -- only for whichever hypotheses above actually survived.

Everything past item 5 is explicitly conditional -- this backlog does not
promise A3-A5 will get built, because the hypotheses behind them might not
survive contact with real data. That's the intended outcome of doing this in
hypothesis-first order rather than algorithm-first order.

---

## What's built so far from this plan

### `calibration.py` -- Jev score vs. outcome analysis (H1, H2, H3)

Reads `audit_trail.jsonl`, joins each `log_decision` record to its
`outcome_update` by `decision_id`, and buckets closed trades by Jev
conviction score (and separately by confidence) to report mean and median
net P&L per bucket, win rate per bucket, and sample size per bucket. Also
splits closed trades by whether matching news headlines were present at
scoring time (H3), reported as a three-way with-news / without-news /
unknown comparison. This is the first honest look at whether Jev's score
means anything, using data the system was already collecting.

**Reading the output responsibly:** with only a handful of paper trades
logged so far, every bucket will have a tiny sample size and the results are
not yet meaningful -- the report says so explicitly rather than implying
false confidence. This becomes useful once enough decisions have accumulated
(see the sample-size warning the tool prints). Treat early runs as "is the
plumbing correct," not "is Jev good."

### `historical_data.py` -- OHLCV bar loading (D2)

`load_from_csv()` is the primary, always-working path: standard
timestamp/open/high/low/close/volume columns, no API dependency, so
everything built on top of it (the backtest engine, future H4/H5 tests) is
never blocked on an unconfirmed broker contract. `fetch_from_indstocks()` is
a best-effort placeholder for later automation, following the same
gated-and-clearly-labeled pattern as `market_data.py`'s WebSocket feed.

### `backtest.py` -- event-driven backtest engine (A6)

Strategy-agnostic: takes any function `bars_seen_so_far -> enter_long?` (no
lookahead -- only bars up to and including the current one are visible when
deciding). Fills a signal at the *next* bar's open, not the deciding bar's
close, to avoid same-bar lookahead bias. Reuses the identical exit logic
(`exits.determine_exit_reason`) and cost engine (`costs.compute_round_trip_cost`)
that live trading uses, so a backtest result means the same thing a live
result would under the same rules. Reports gross P&L, net P&L, cost drag,
win rate, and per-trade detail including which exit rule fired.

---

# Trader Intelligent One

Working name for what this repo is supposed to become: **one trader**,
not a pile of indicators. Jev is a voter. Rules are voters. The
**risk governor owns money**. Nothing else may size, halt, or flatten.

```
DATA  →  FEATURES  →  VOTES  →  COMBINER  →  GOVERNOR  →  EXITS
                              (Jev, rules,
                               regime, clock)
```

If a layer cannot be turned off with one env flag, it does not ship.

---

## Design rules for this trader

1. **Long-only NSE cash** until a short/F&O hypothesis survives its own
   test. No "just add Bank Nifty options."
2. **One new vote at a time.** Paper that vote for a week. Then AND it
   with Jev. Never OR two weak votes first.
3. **Same exits for every vote** (1% stop, 2% target, time, 15:15
   flatten) until an exit hypothesis is tested separately.
4. **Net of `costs.py` or it did not happen.**
5. **Kill list is allowed to win.** Most ideas below should die. That
   is the product.

---

## Feature catalog (what the brain can see)

Build these as numbers on `FeatureSet` / Jev `state`, not as new
brokers. Grouped by how much they buy you per unit of work.

### F-price (do next — cheap, already half-built)

| ID | Feature | Use | Status |
|---|---|---|---|
| F1 | Last 20-bar return | Momentum / Jev context | **Live in context (v17)** |
| F2 | Distance to session/lookback VWAP | Mean-revert vote + Jev | **Live in context (v17)** |
| F3 | ATR% / realized vol | Regime, size shrink | **Live in context (v17)** |
| F4 | Regime label calm/normal/violent | Switches which rule may fire | **Live (v17)** |
| F5 | Opening-range high/low (first 15 min) | Breakout vs "no trade in open" | **Live:** built during skip window, frozen after; Jev sees ORH/ORL |
| F6 | Day high / day low / location in range | Breakout vs fade | Not started |
| F7 | Volume vs 20-bar average | Confirm H4 | Partial (rule uses window volume) |
| F8 | Gap from prior close | Gap-and-go vs fade-the-gap | Needs prior daily bar |

### F-market (index weather)

| ID | Feature | Use | Status |
|---|---|---|---|
| F9 | Nifty / Bank Nifty day % | Veto stock longs when index is dumping | **Live V7:** `INDEX_VETO_*`; quote miss = no veto |
| F10 | Nifty vs stock relative strength (stock − index, N days) | Trade leaders, not laggards in a rally | Needs D2 on index + stock |
| F11 | India VIX level + 5-day change | True regime, not own-bar vol | Not started |
| F12 | Advance/decline or breadth proxy | Risk-on/off | Not started |

### F-event (block size, don't invent alpha)

| ID | Feature | Use | Status |
|---|---|---|---|
| F13 | Results date ±1 session | Size → 0 or skip | Not started |
| F14 | Board / dividend / split | Skip | Not started |
| F15 | Headline count + tone bucket | H3 enrichment for Jev | Headlines yes, tone no |

### F-microstructure (live quality)

| ID | Feature | Use | Status |
|---|---|---|---|
| F16 | Spread vs tick | Do not enter wide names | Not started |
| F17 | Time-of-day bucket win-rate prior | Size 0 / 0.5 / 1.0 | Needs D1 after paper weeks |
| F18 | Seconds since last print | Stale quote veto | Tick cache age exists (5s) |

Do **not** add RSI, MACD, Bollinger, Ichimoku, Supertrend as separate
"algos." If you want a classic oscillator, it is one number on
`FeatureSet` and one hypothesis. Default is no.

---

## Algorithm catalog (votes that can coexist)

Each algorithm is a **vote**: `allow` + `reason`. The combiner already
exists (`entry.combine_votes`). New algos plug in there.

### Core (keep)

| ID | Algo | Idea | Live hook |
|---|---|---|---|
| V0 | **Governor** | Drawdown, slippage, lot, caps, flatten | Always on. Not optional. |
| V1 | **Jev long score** | External model on compact state | `ENTRY_MODE=jev` (default) |
| V2 | **H4 momentum** | Up + volume | `rule` / `jev_and_rule` |
| V3 | **H5 mean revert** | Below VWAP in calm tape | same |
| V4 | **Regime router** | Violent→V2, calm→V3 | Inside `rule_vote` |

### Next to design (only after paper D1 or real CSV)

| ID | Algo | Idea | Kill if |
|---|---|---|---|
| V5 | **Opening-range breakout** | After skip window, rule may fire `orb_hold` if last >= OR high (not in calm) | Still unproven on real CSVs — paper only |
| V6 | **VWAP reclaim** | Dip under VWAP, reclaim, then long (not "just below VWAP") | Same as H5; if H5 dies this often dies with it |
| V7 | **Index veto** | No new stock longs if Nifty ≤ −0.8% on the day | If stock winners cluster on down-Nifty days, veto is harmful |
| V8 | **Relative strength long** | Stock 5-day return > index 5-day return *and* V2 | Rank is noise on 4 names |
| V9 | **News impulse** | First 30 min after a *named* headline, only if V1 high | H3 already tests "any headline"; this is stricter |
| V10 | **Two-question Jev** | Q1 edge, Q2 "is this noise?" — trade only Q1 high and Q2 low | If Q2 never moves, drop it |
| V11 | **Calibration sizer** | Map Jev bucket → 0 / 0.5× / 1× size | If buckets flat, size 0 and demote V1 to logger |
| V12 | **Time-of-day prior** | After ~100 outcomes, mute 09:30–10:00 or 14:45–15:15 if those buckets lose | Needs D1 |
| V13 | **Earnings blackout** | Flat into results | Process, not alpha |
| V14 | **Watchlist ranker** | Rank 15 liquid names by RS + ADV; trade top 5 | H7 |

### Explicitly out of scope until cash is boring

| ID | Why not |
|---|---|
| Options selling / buying | Different margin, expiry, assignment. New product. |
| Intraday shorts | Separate borrow/F&O path. |
| Multi-timeframe ML / LSTM / RL | Will overfit 4 tickers before D1 exists. |
| Second LLM "debating" Jev | Cost and correlated error. Use V10 on the *same* model first. |
| Social / Twitter sentiment | Garbage in, latency, policy. |
| Grid / martingale / averaging down | Conflicts with governor. Never. |
| Copy-trade / tip-channel parser | Not this trader. |

---

## Combiner policy (how votes become a trade)

Recommended paper ladder — change **one** thing per week:

| Week | `ENTRY_MODE` | Extra flags | Question |
|---|---|---|---|
| 1 | `jev` | `OPEN_SKIP_MINUTES=15` | Does Jev fire at all? Audit fills? |
| 2 | `jev` | same | H1/H2/H3 on that week's file |
| 3 | `rule` | same | Do V2/V3 fire on *live* minute bars? |
| 4 | `jev_and_rule` | same | Does AND cut losers more than winners? |
| 5+ | `jev_and_rule` | add V7 index veto when coded | One veto at a time |

Never enable V5–V14 on live. Paper first. Promote only if
`evaluate_vs_baseline` or calibration says **survive**.

---

## Build sequence (this is the actual todo)

Update this list when something ships. Do not jump.

1. **You:** 5–10 paper sessions, `ENTRY_MODE=jev`, keep `audit_trail.jsonl`.
2. **You:** dump OHLCV CSVs for the watchlist + Nifty into `data/bars/`.
3. **Run** `run_calibration_report.py` (H1–H3) and
   `run_watchlist_backtest.py data/bars/` (H4/H5 per name).
4. **Then code, in order:**
   1. F5 opening-range high/low stored on the session object
   2. F9 / V7 index veto (one quote, one bool)
   3. F8 prior-close gap
   4. V10 second Jev question
   5. V11 size-by-bucket once N is not a joke (≥100 closed paper trades)
   6. V5 ORB as a `StrategyFn` + backtest, *then* optional live vote
   7. V13 earnings calendar skip
   8. V14 ranked watchlist
5. **Only if cash paper is net-positive after costs:** discuss F&O.
   Not before.

---

## What "intelligent" means here

Not "the model is smart." Intelligent means:

- it **knows when not to trade** (open skip, regime, index veto, earnings)
- it **measures its own votes** (audit + calibration + watchlist backtest)
- it **does not let a voter touch size or flatten**
- it **can fire a voter without rewriting the loop** (`ENTRY_MODE`, `RuleVote`)

That is the whole product. Extra indicators without a kill test make it
dumber.
