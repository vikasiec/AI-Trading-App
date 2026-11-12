"""Time Machine: replay a planned intraday long before any capital is at risk.

Given a plan (entry, stop, target, the entry bar's close time) and a symbol's
own recent 5-minute bars, this answers one question with numbers instead of a
feeling: from this entry, how often does the target come before the stop?

Two views of the same plan:

1. Monte Carlo replay. Every past full session is cut into 5-minute bars, each
   stored as ratios to the previous bar's close. One replay starts at the entry
   price and walks the rest of the session, drawing each bar whole from the
   pool of past bars at the same time of day (give or take `window` bars). The
   stop is checked before the target inside every bar, so a bar that touches
   both counts as a loss -- the conservative call.

2. Time-of-day baseline. The same percentage stop and target, entered at the
   same clock time on each of those real past sessions. No signal, no
   selection: it is what the stop/target geometry alone earned at that time of
   day. A plan has to beat this to mean anything.

Costs come from the same engine the live loop and the backtester use
(`costs.compute_round_trip_cost`), so "average net per replay" is net of STT,
exchange and SEBI fees, stamp duty, brokerage and GST.

OFFLINE ANALYSIS ONLY. Nothing in the trading loop imports this module; it
places no orders and changes no sizing, risk or exit rule. A simulation built
from a stock's own recent bars is not a prediction: results, news and halts
can do what those sessions never did.

The replay algorithm is adapted from day-trader-skill's trader-timemachine
(MIT licence, (c) Jake Schincariol, github.com/Jakeschincariol/day-trader-skill),
re-cut here for the NSE session, this app's Bar type and its cost engine.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from typing import Optional

from .costs import CostRates, compute_round_trip_cost
from .historical_data import Bar
from .session import IST

BAR_MINUTES = 5
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
SESSION_BARS = 75                # 09:15 to 15:30 in 5-minute bars
DEFAULT_UNTIL = time(15, 15)     # the loop's own flatten time (session.session_phase)
MIN_SESSIONS = 20
MAX_REPLAYS = 1_000_000
MAX_WINDOW = 10
COIN_FLIP = 0.05                 # verdict thresholds, in share-of-decided-replays points
STRONG = 0.15
MANY_NEITHER = 0.40

BarRatios = tuple[float, float, float, float]      # (open, high, low, close) / previous close
Pool = list[list[BarRatios]]                         # pool[slot] = that slot's bar from every past session
Session = tuple[date, list[Bar]]


class TimeMachineError(ValueError):
    """A plan that cannot be replayed, or too little data. The message is safe to show."""


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


_OPEN_MIN = _minutes(SESSION_OPEN)
_CLOSE_MIN = _minutes(SESSION_CLOSE)


def _slot_of_close(t: time, what: str) -> int:
    """Index of the bar that CLOSES at `t` (09:20 -> 0, 15:30 -> 74)."""
    if t.second or t.microsecond:
        raise TimeMachineError(f"{what} {t.isoformat()} must be a whole minute")
    offset = _minutes(t) - _OPEN_MIN
    if offset % BAR_MINUTES:
        raise TimeMachineError(
            f"{what} {t.strftime('%H:%M')} is not on the {BAR_MINUTES}-minute grid "
            f"(use a bar's close, e.g. 09:50)"
        )
    slot = offset // BAR_MINUTES - 1
    if not 0 <= slot <= SESSION_BARS - 1:
        raise TimeMachineError(
            f"{what} {t.strftime('%H:%M')} is outside the session "
            f"({SESSION_OPEN.strftime('%H:%M')} to {SESSION_CLOSE.strftime('%H:%M')} IST)"
        )
    return slot


@dataclass(frozen=True)
class TradePlan:
    """A planned long. `entry_time` is the IST close of the entry bar."""
    symbol: str
    day: date
    entry: float
    stop: float
    target: float
    entry_time: time
    qty: int = 0   # 0 = no rupee figures, probabilities and R only

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise TimeMachineError("the plan needs a symbol")
        for name in ("entry", "stop", "target"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TimeMachineError(f"{name} must be a number, got {value!r}")
            if not math.isfinite(value) or value <= 0:
                raise TimeMachineError(f"{name} must be a positive price, got {value!r}")
        if not self.stop < self.entry < self.target:
            raise TimeMachineError(
                f"a long needs stop < entry < target "
                f"(got stop {self.stop}, entry {self.entry}, target {self.target})"
            )
        if isinstance(self.qty, bool) or not isinstance(self.qty, int) or self.qty < 0:
            raise TimeMachineError(f"qty must be a whole number of shares, got {self.qty!r}")
        if self.entry_slot > SESSION_BARS - 2:
            raise TimeMachineError(
                f"an entry at {self.entry_time.strftime('%H:%M')} leaves no bars to replay"
            )

    @property
    def entry_slot(self) -> int:
        return _slot_of_close(self.entry_time, "entry_time")

    @property
    def risk(self) -> float:
        return self.entry - self.stop

    @property
    def target_r(self) -> float:
        return (self.target - self.entry) / self.risk

    @property
    def stop_pct(self) -> float:
        return self.risk / self.entry

    @property
    def target_pct(self) -> float:
        return (self.target - self.entry) / self.entry


@dataclass(frozen=True)
class ReplayResult:
    replays: int
    seed: int
    window: int
    bars_left: int
    p_target: float
    p_stop: float
    p_neither: float
    p_green: float                 # target first, or above the entry at `until`
    median_price_at_until: float
    p5_price_at_until: float
    p95_price_at_until: float
    avg_r: float
    r_at_target: float
    avg_gross_pnl: Optional[float]  # rupees per replay for plan.qty shares; None when qty is 0
    avg_net_pnl: Optional[float]    # the same, net of the statutory round-trip cost


@dataclass(frozen=True)
class BaselineResult:
    """The plan's stop/target percentages entered at the same clock time on each past session."""
    sessions: int
    target: int
    stop: int
    neither: int
    avg_r: Optional[float]


@dataclass(frozen=True)
class TimeMachineReport:
    plan: TradePlan
    until: time
    pool_sessions: int
    pool_from: date
    pool_to: date
    short_sessions: list[date]
    replay: ReplayResult
    baseline: BaselineResult
    verdict: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """JSON-ready: dates and times as ISO strings."""
        plan = asdict(self.plan)
        plan["day"] = self.plan.day.isoformat()
        plan["entry_time"] = self.plan.entry_time.strftime("%H:%M")
        plan["risk_per_share"] = round(self.plan.risk, 4)
        plan["target_r"] = round(self.plan.target_r, 4)
        return {
            "plan": plan,
            "until": self.until.strftime("%H:%M"),
            "pool": {
                "sessions": self.pool_sessions,
                "from": self.pool_from.isoformat(),
                "to": self.pool_to.isoformat(),
                "short_sessions_left_out": [d.isoformat() for d in self.short_sessions],
            },
            "replay": asdict(self.replay),
            "baseline": asdict(self.baseline),
            "verdict": self.verdict,
            "notes": list(self.notes),
        }

    def summary_text(self) -> str:
        p, m, b = self.plan, self.replay, self.baseline
        until = self.until.strftime("%H:%M")
        lines = [
            f"{p.symbol} long, {p.day.isoformat()}: entry {p.entry:.2f} at {p.entry_time.strftime('%H:%M')} IST, "
            f"stop {p.stop:.2f}, target {p.target:.2f} ({p.target_r:g}R, risk {p.risk:.2f} a share).",
            f"{m.replays:,} replays of the rest of the session to {until} ({m.bars_left} five-minute bars), "
            f"seed {m.seed}.",
            f"Each bar is drawn whole from {p.symbol}'s own 5-minute bars at the same time of day "
            f"(+-{m.window} bars) over its last {self.pool_sessions} full sessions "
            f"({self.pool_from.isoformat()} to {self.pool_to.isoformat()}).",
            "",
            f"  target first   {m.p_target:6.1%}",
            f"  stop first     {m.p_stop:6.1%}",
            f"  neither        {m.p_neither:6.1%}   still open at {until}",
            f"  green          {m.p_green:6.1%}   target first, or above the entry at {until}",
            "",
            f"  Price at {until}: median {m.median_price_at_until:.2f}, 90% of replays between "
            f"{m.p5_price_at_until:.2f} and {m.p95_price_at_until:.2f}.",
            f"  Average R: {m.avg_r:+.3f} (+{m.r_at_target:g}R at the target, -1R at the stop).",
        ]
        if m.avg_net_pnl is not None and m.avg_gross_pnl is not None:
            lines.append(
                f"  Average per replay on {p.qty} shares: gross Rs {m.avg_gross_pnl:+,.2f}, "
                f"net of costs Rs {m.avg_net_pnl:+,.2f}."
            )
        lines.append("")
        if b.sessions and b.avg_r is not None:
            lines.append(
                f"Baseline: the same {p.stop_pct:.2%} stop and {p.target_pct:.2%} target entered at "
                f"{p.entry_time.strftime('%H:%M')} on each of those {b.sessions} real sessions, no signal: "
                f"{b.target} target, {b.stop} stop, {b.neither} neither. Average R {b.avg_r:+.3f}."
            )
        else:
            lines.append("Baseline: no past session could be entered at that time.")
        lines += ["", self.verdict]
        lines += self.notes
        if self.short_sessions:
            lines.append(
                f"Left out {len(self.short_sessions)} short session(s) (gaps, halts or partial data): "
                + ", ".join(d.isoformat() for d in self.short_sessions) + "."
            )
        lines.append(
            "A simulation from the stock's own recent bars, not a prediction: results, news and halts "
            f"can do what those sessions never did. Same data and --seed {m.seed} give the same numbers."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------- sessions

def to_ist(ts: datetime) -> datetime:
    """Bar timestamps: aware ones are converted, naive ones are taken as IST already
    (the CSVs this app writes carry IST wall-clock times)."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=IST)
    return ts.astimezone(IST)


def split_sessions(bars: list[Bar]) -> dict[date, dict[int, Bar]]:
    """{day: {slot: bar}} for 5-minute bars inside 09:15 to 15:30 IST. Bars off the
    grid (a live tick) or outside the session are dropped; a later duplicate wins."""
    days: dict[date, dict[int, Bar]] = {}
    for bar in bars:
        local = to_ist(bar.timestamp)
        if local.second or local.microsecond:
            continue
        offset = _minutes(local.time()) - _OPEN_MIN
        if offset < 0 or offset % BAR_MINUTES or offset >= SESSION_BARS * BAR_MINUTES:
            continue
        days.setdefault(local.date(), {})[offset // BAR_MINUTES] = bar
    return days


def full_sessions(days: dict[date, dict[int, Bar]], before: date) -> tuple[list[Session], list[date]]:
    """Sessions strictly before `before` with all 75 bars and sane prices, oldest first,
    plus the dates of the ones left out."""
    full: list[Session] = []
    short: list[date] = []
    for day in sorted(days):
        if day >= before:
            continue
        slots = days[day]
        ordered = [slots[i] for i in range(SESSION_BARS) if i in slots]
        usable = len(ordered) == SESSION_BARS and all(
            math.isfinite(b.close) and b.close > 0 and b.low > 0 and b.high >= b.low for b in ordered
        )
        if usable:
            full.append((day, ordered))
        else:
            short.append(day)
    return full, short


def build_pool(sessions: list[Session]) -> Pool:
    """pool[i] = every past session's bar i as (open, high, low, close) / bar i-1's close.
    pool[0] stays empty: the first bar has no previous close inside the session."""
    pool: Pool = [[] for _ in range(SESSION_BARS)]
    for _, bars in sessions:
        for i in range(1, SESSION_BARS):
            prev = bars[i - 1].close
            cur = bars[i]
            pool[i].append((cur.open / prev, cur.high / prev, cur.low / prev, cur.close / prev))
    return pool


# ---------------------------------------------------------------- the replay

def _percentile(sorted_values: list[float], q: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def simulate(
    pool: Pool,
    plan: TradePlan,
    last_slot: int,
    replays: int = 10_000,
    seed: int = 19,
    window: int = 2,
    cost_rates: CostRates = CostRates(),
) -> ReplayResult:
    """Monte Carlo replays of bars entry_slot+1 .. last_slot. Deterministic for a seed."""
    if not 1 <= replays <= MAX_REPLAYS:
        raise TimeMachineError(f"replays must be between 1 and {MAX_REPLAYS:,}")
    if not 0 <= window <= MAX_WINDOW:
        raise TimeMachineError(f"window must be between 0 and {MAX_WINDOW} bars")
    first = plan.entry_slot + 1
    if not first <= last_slot <= SESSION_BARS - 1:
        raise TimeMachineError("nothing to replay: the end time is not after the entry")
    if any(not pool[i] for i in range(1, SESSION_BARS)):
        raise TimeMachineError("the bar pool is empty: no full past sessions to draw from")

    rng = random.Random(seed)
    outcomes: list[str] = []
    finals: list[float] = []
    for _ in range(replays):
        price = plan.entry
        outcome: Optional[str] = None
        for i in range(first, last_slot + 1):
            slot = i if window == 0 else min(SESSION_BARS - 1, max(1, i + rng.randint(-window, window)))
            _open, high, low, close = rng.choice(pool[slot])
            if outcome is None:
                if price * low <= plan.stop:        # both inside one bar: count the loss
                    outcome = "stop"
                elif price * high >= plan.target:
                    outcome = "target"
            price *= close
        outcomes.append(outcome or "neither")
        finals.append(price)

    counts = {name: outcomes.count(name) for name in ("target", "stop", "neither")}
    green = sum(1 for o, p in zip(outcomes, finals) if o == "target" or (o == "neither" and p > plan.entry))
    r_target = plan.target_r
    rs = [
        r_target if o == "target" else -1.0 if o == "stop" else (p - plan.entry) / plan.risk
        for o, p in zip(outcomes, finals)
    ]

    avg_gross: Optional[float] = None
    avg_net: Optional[float] = None
    if plan.qty > 0:
        gross_total = 0.0
        net_total = 0.0
        for o, p in zip(outcomes, finals):
            exit_price = plan.target if o == "target" else plan.stop if o == "stop" else p
            gross = (exit_price - plan.entry) * plan.qty
            cost = compute_round_trip_cost(
                buy_price=plan.entry, sell_price=exit_price, qty=plan.qty,
                product="INTRADAY", rates=cost_rates,
            )
            gross_total += gross
            net_total += gross - cost.total
        avg_gross = round(gross_total / replays, 2)
        avg_net = round(net_total / replays, 2)

    ordered = sorted(finals)
    return ReplayResult(
        replays=replays, seed=seed, window=window, bars_left=last_slot - plan.entry_slot,
        p_target=round(counts["target"] / replays, 4),
        p_stop=round(counts["stop"] / replays, 4),
        p_neither=round(counts["neither"] / replays, 4),
        p_green=round(green / replays, 4),
        median_price_at_until=round(_percentile(ordered, 0.5), 2),
        p5_price_at_until=round(_percentile(ordered, 0.05), 2),
        p95_price_at_until=round(_percentile(ordered, 0.95), 2),
        avg_r=round(statistics.fmean(rs), 3),
        r_at_target=round(r_target, 4),
        avg_gross_pnl=avg_gross,
        avg_net_pnl=avg_net,
    )


def time_of_day_baseline(sessions: list[Session], plan: TradePlan, last_slot: int) -> BaselineResult:
    """Enter each past session at the plan's clock time (that bar's close) with the plan's
    stop and target as percentages of the entry; stop first, then target, bar by bar."""
    k = plan.entry_slot
    target = stop = neither = 0
    rs: list[float] = []
    for _, bars in sessions:
        entry = bars[k].close
        stop_price = entry * (1 - plan.stop_pct)
        target_price = entry * (1 + plan.target_pct)
        risk = entry - stop_price
        outcome: Optional[str] = None
        for bar in bars[k + 1:last_slot + 1]:
            if bar.low <= stop_price:
                outcome = "stop"
                break
            if bar.high >= target_price:
                outcome = "target"
                break
        if outcome == "target":
            target += 1
            rs.append(plan.target_r)
        elif outcome == "stop":
            stop += 1
            rs.append(-1.0)
        else:
            neither += 1
            rs.append((bars[last_slot].close - entry) / risk)
    return BaselineResult(
        sessions=len(sessions), target=target, stop=stop, neither=neither,
        avg_r=round(statistics.fmean(rs), 3) if rs else None,
    )


# ---------------------------------------------------------------- the verdict

def lean(replay: ReplayResult) -> Optional[tuple[float, float, float]]:
    """(gap, share, break_even): the target's share of the replays that hit one side,
    minus the share that breaks even at the target's R. None when nothing hit a side."""
    decided = replay.p_target + replay.p_stop
    if decided <= 0:
        return None
    share = replay.p_target / decided
    even = 1.0 / (1.0 + replay.r_at_target)
    return share - even, share, even


def verdict(replay: ReplayResult, until: time) -> str:
    """One line from the numbers. Under 5 points from break-even is a coin flip; 5 to 15
    leans; 15 or more is strong. It describes the replays, it does not recommend a trade."""
    at = until.strftime("%H:%M")
    measured = lean(replay)
    if measured is None:
        return f"No replay reached the target or the stop by {at}: the price at {at} decides every one."
    gap, share, even = measured
    if abs(gap) < COIN_FLIP:
        head = "Close to break-even"
    elif abs(gap) < STRONG:
        head = "Leans toward the target" if gap > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if gap > 0 else "Strongly toward the stop"
    return (
        f"{head}: at {replay.r_at_target:g}R the target has to come first in {even:.0%} of the replays "
        f"that hit one side; it did in {share:.0%} "
        f"(target first {replay.p_target:.1%} vs stop first {replay.p_stop:.1%})."
    )


def verdict_notes(replay: ReplayResult, until: time) -> list[str]:
    """Lines under the verdict so it never reads one-sided."""
    at = until.strftime("%H:%M")
    notes: list[str] = []
    measured = lean(replay)
    if measured is not None and replay.p_neither > MANY_NEITHER:
        notes.append(f"{replay.p_neither:.1%} of the replays hit neither by {at}; the price at {at} decides those.")
    if measured is not None and abs(measured[0]) >= COIN_FLIP and replay.avg_r != 0 \
            and (replay.avg_r > 0) != (measured[0] > 0):
        notes.append(
            f"Average R is {replay.avg_r:+.3f} all the same: the replays that hit neither pull it the other way."
        )
    if replay.avg_net_pnl is not None and replay.avg_gross_pnl is not None \
            and replay.avg_gross_pnl > 0 >= replay.avg_net_pnl:
        notes.append("Costs turn the average replay from a gain into a loss at this size.")
    return notes


# ---------------------------------------------------------------- the run

def run_time_machine(
    bars: list[Bar],
    plan: TradePlan,
    replays: int = 10_000,
    seed: int = 19,
    window: int = 2,
    until: time = DEFAULT_UNTIL,
    cost_rates: CostRates = CostRates(),
    min_sessions: int = MIN_SESSIONS,
) -> TimeMachineReport:
    """5-minute bars for one symbol + a plan -> the report. Only sessions strictly
    before plan.day feed the pool, so the plan's own day is never looked at."""
    last_slot = _slot_of_close(until, "until")
    if last_slot <= plan.entry_slot:
        raise TimeMachineError(
            f"until {until.strftime('%H:%M')} leaves nothing to replay after the "
            f"{plan.entry_time.strftime('%H:%M')} entry"
        )
    sessions, short = full_sessions(split_sessions(bars), plan.day)
    if len(sessions) < min_sessions:
        raise TimeMachineError(
            f"only {len(sessions)} full session(s) of {plan.symbol} before {plan.day.isoformat()} in the data; "
            f"the time machine needs at least {min_sessions}"
        )
    replay = simulate(build_pool(sessions), plan, last_slot, replays, seed, window, cost_rates)
    return TimeMachineReport(
        plan=plan, until=until, pool_sessions=len(sessions),
        pool_from=sessions[0][0], pool_to=sessions[-1][0], short_sessions=short,
        replay=replay, baseline=time_of_day_baseline(sessions, plan, last_slot),
        verdict=verdict(replay, until), notes=verdict_notes(replay, until),
    )
