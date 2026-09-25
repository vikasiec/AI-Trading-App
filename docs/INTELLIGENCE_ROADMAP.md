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
| D4 | **Sector/index classification per symbol** | Needed for any sector-rotation or relative-strength hypothesis, and for a smarter portfolio-risk check (the current `portfolio_risk.py` caps position *count* and *capital*, not sector concentration) | Not started |
| D5 | **India VIX / market-wide volatility regime** | A single symbol's price action means something different in a calm market vs. a violent one; several hypotheses below need to condition on regime | Not started |
| D6 | **Options data (PCR, OI, IV)** for index-level sentiment | Lower priority -- useful for a contrarian-sentiment hypothesis, but adds real complexity for something not yet proven to matter | Backlog, not near-term |

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
| A2 | **Regime detector** (volatility-band or ATR-based classifier) | H6 | Lets the risk governor and any strategy condition behavior on market state instead of using one static rule all the time |
| A3 | **Momentum strategy module** | H4 confirmed | A second, independent signal source alongside Jev -- reduces single-point-of-failure risk on one model |
| A4 | **Mean-reversion strategy module** | H5 confirmed | Same rationale as A3, opposite regime |
| A5 | **Sector-relative-strength ranker** | H7 confirmed | Feeds `watchlist.py` a ranked, rotating list instead of a static one |
| A6 | **Backtest engine** proper -- event-driven replay of historical bars through the *same* `risk_governor.py` / `exits.py` / `costs.py` code paths the live loop uses, not a separate simplified simulation | D2 | The only trustworthy way to test H4/H5/H7 and any future price-based hypothesis without risking capital; also the only way to validate that a code change to risk/exits logic hasn't silently changed behavior |

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
