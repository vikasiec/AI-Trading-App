"""Time Machine: plan validation, session handling, the replay, the baseline and the CLI."""
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_timemachine as cli  # noqa: E402
from jev_indstocks_trader.costs import CostRates, compute_round_trip_cost  # noqa: E402
from jev_indstocks_trader.historical_data import Bar, save_to_csv  # noqa: E402
from jev_indstocks_trader.session import IST  # noqa: E402
from jev_indstocks_trader.timemachine import (  # noqa: E402
    SESSION_BARS,
    BaselineResult,
    ReplayResult,
    TimeMachineError,
    TradePlan,
    build_pool,
    full_sessions,
    lean,
    run_time_machine,
    simulate,
    split_sessions,
    time_of_day_baseline,
    verdict,
    verdict_notes,
)

PLAN_DAY = date(2026, 11, 12)
FLAT = (1.0, 1.0, 1.0, 1.0)


def make_session(day: date, start: float = 100.0, step: float = 0.0, wick: float = 0.0,
                 bars: int = SESSION_BARS, aware: bool = False) -> list[Bar]:
    """One session of 5-minute bars from 09:15 IST: each close moves by `step`."""
    out = []
    price = start
    for i in range(bars):
        ts = datetime.combine(day, time(9, 15)) + timedelta(minutes=5 * i)
        if aware:
            ts = ts.replace(tzinfo=IST).astimezone(timezone.utc)
        close = price + step
        out.append(Bar(timestamp=ts, open=price, high=max(price, close) + wick,
                       low=min(price, close) - wick, close=close, volume=1000))
        price = close
    return out


def weekdays_before(day: date, n: int) -> list[date]:
    out, d = [], day - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def history(n: int = 25, **kwargs) -> list[Bar]:
    bars: list[Bar] = []
    for d in weekdays_before(PLAN_DAY, n):
        bars += make_session(d, **kwargs)
    return bars


def plan(**overrides) -> TradePlan:
    base = dict(symbol="RELIANCE", day=PLAN_DAY, entry=100.0, stop=99.0, target=102.0,
                entry_time=time(9, 50), qty=0)
    base.update(overrides)
    return TradePlan(**base)


def uniform_pool(ratios: tuple[float, float, float, float]) -> list[list[tuple[float, float, float, float]]]:
    return [[]] + [[ratios] for _ in range(SESSION_BARS - 1)]


# ---------------------------------------------------------------- the plan

def test_plan_knows_its_slot_risk_and_r():
    p = plan()
    assert p.entry_slot == 6            # 09:45 to 09:50 is the seventh bar
    assert plan(entry_time=time(9, 20)).entry_slot == 0
    assert p.risk == pytest.approx(1.0)
    assert p.target_r == pytest.approx(2.0)
    assert p.stop_pct == pytest.approx(0.01)
    assert p.target_pct == pytest.approx(0.02)


@pytest.mark.parametrize("overrides, fragment", [
    ({"stop": 100.0}, "stop < entry < target"),
    ({"target": 99.5}, "stop < entry < target"),
    ({"entry": -1.0}, "positive price"),
    ({"stop": float("nan")}, "positive price"),
    ({"entry": "100"}, "must be a number"),
    ({"symbol": "  "}, "needs a symbol"),
    ({"qty": -5}, "whole number of shares"),
    ({"qty": 1.5}, "whole number of shares"),
    ({"entry_time": time(9, 52)}, "5-minute grid"),
    ({"entry_time": time(9, 15)}, "outside the session"),
    ({"entry_time": time(15, 35)}, "outside the session"),
    ({"entry_time": time(15, 30)}, "leaves no bars to replay"),
    ({"entry_time": time(9, 50, 30)}, "whole minute"),
])
def test_plan_rejects_what_cannot_be_replayed(overrides, fragment):
    with pytest.raises(TimeMachineError, match=fragment):
        plan(**overrides)


# ---------------------------------------------------------------- sessions

def test_split_sessions_takes_naive_times_as_ist_and_converts_aware_ones():
    naive = make_session(date(2026, 11, 11))
    aware = make_session(date(2026, 11, 11), aware=True)
    assert aware[0].timestamp.hour == 3 and aware[0].timestamp.minute == 45      # 09:15 IST is 03:45 UTC
    for bars in (naive, aware):
        days = split_sessions(bars)
        assert list(days) == [date(2026, 11, 11)]
        assert sorted(days[date(2026, 11, 11)]) == list(range(SESSION_BARS))


def test_split_sessions_drops_off_grid_and_out_of_session_bars():
    day = date(2026, 11, 11)
    good = make_session(day, bars=3)
    stray = [
        Bar(datetime.combine(day, time(9, 0)), 1, 1, 1, 1, 1),        # pre-open
        Bar(datetime.combine(day, time(9, 17)), 1, 1, 1, 1, 1),       # off the grid
        Bar(datetime.combine(day, time(9, 20, 45)), 1, 1, 1, 1, 1),   # a live tick
        Bar(datetime.combine(day, time(15, 30)), 1, 1, 1, 1, 1),      # the closing print
    ]
    assert sorted(split_sessions(good + stray)[day]) == [0, 1, 2]


def test_full_sessions_leaves_out_short_broken_and_plan_day_sessions():
    d1, d2, d3 = weekdays_before(PLAN_DAY, 3)
    broken = make_session(d3)
    broken[10] = Bar(broken[10].timestamp, 0.0, 0.0, 0.0, 0.0, 0)
    bars = make_session(d1) + make_session(d2, bars=40) + broken \
        + make_session(PLAN_DAY) + make_session(PLAN_DAY + timedelta(days=1))
    full, short = full_sessions(split_sessions(bars), PLAN_DAY)
    assert [d for d, _ in full] == [d1]
    assert short == [d2, d3]


def test_build_pool_stores_each_bar_as_ratios_to_the_previous_close():
    pool = build_pool([(date(2026, 11, 11), make_session(date(2026, 11, 11), start=100.0, step=1.0, wick=0.5))])
    assert pool[0] == []
    o, h, lo, c = pool[1][0]              # bar 1: open 101, high 102.5, low 100.5, close 102; previous close 101
    assert (o, h, lo, c) == pytest.approx((1.0, 102.5 / 101, 100.5 / 101, 102 / 101))
    assert all(len(slot) == 1 for slot in pool[1:])


# ---------------------------------------------------------------- the replay

def test_every_replay_hits_the_target_when_every_bar_rises():
    r = simulate(uniform_pool((1.0, 1.01, 1.0, 1.01)), plan(), last_slot=71, replays=200)
    assert (r.p_target, r.p_stop, r.p_neither, r.p_green) == (1.0, 0.0, 0.0, 1.0)
    assert r.avg_r == pytest.approx(2.0)
    assert r.bars_left == 71 - 6


def test_every_replay_hits_the_stop_when_every_bar_falls():
    r = simulate(uniform_pool((1.0, 1.0, 0.99, 0.99)), plan(), last_slot=71, replays=200)
    assert (r.p_target, r.p_stop, r.p_neither, r.p_green) == (0.0, 1.0, 0.0, 0.0)
    assert r.avg_r == pytest.approx(-1.0)


def test_a_flat_tape_hits_neither_and_scores_zero():
    r = simulate(uniform_pool(FLAT), plan(), last_slot=71, replays=50)
    assert (r.p_target, r.p_stop, r.p_neither, r.p_green) == (0.0, 0.0, 1.0, 0.0)
    assert r.avg_r == 0.0
    assert r.median_price_at_until == 100.0
    assert lean(r) is None
    assert "No replay reached the target or the stop by 15:15" in verdict(r, time(15, 15))


def test_a_bar_that_touches_both_counts_as_the_stop():
    r = simulate(uniform_pool((1.0, 1.05, 0.95, 1.0)), plan(), last_slot=71, replays=50)
    assert r.p_stop == 1.0 and r.p_target == 0.0


def test_same_seed_same_numbers_and_the_seed_matters():
    pool = build_pool([(d, make_session(d, step=0.0, wick=0.0)) for d in weekdays_before(PLAN_DAY, 2)])
    # two different bars per slot, so the draw matters
    for i in range(1, SESSION_BARS):
        pool[i] = [(1.0, 1.004, 0.999, 1.003), (1.0, 1.001, 0.996, 0.997)]
    a = simulate(pool, plan(), last_slot=71, replays=500, seed=19)
    b = simulate(pool, plan(), last_slot=71, replays=500, seed=19)
    c = simulate(pool, plan(), last_slot=71, replays=500, seed=7)
    assert a == b
    assert a != c
    assert a.p_target + a.p_stop + a.p_neither == pytest.approx(1.0, abs=1e-3)
    assert 0 < a.p_target < 1 and 0 < a.p_stop < 1


def test_window_zero_draws_only_from_the_same_slot():
    pool = uniform_pool(FLAT)
    pool[7] = [(1.0, 1.05, 1.0, 1.05)]          # only the first replayed bar can reach the target
    hit = simulate(pool, plan(), last_slot=71, replays=20, window=0)
    assert hit.p_target == 1.0


def test_rupee_figures_are_net_of_the_cost_engine():
    p = plan(qty=100)
    r = simulate(uniform_pool(FLAT), p, last_slot=71, replays=10)
    cost = compute_round_trip_cost(100.0, 100.0, 100, "INTRADAY", CostRates()).total
    assert r.avg_gross_pnl == 0.0
    assert r.avg_net_pnl == pytest.approx(-cost, abs=0.01)
    assert simulate(uniform_pool(FLAT), plan(), last_slot=71, replays=10).avg_net_pnl is None


def test_costs_note_when_a_gross_gain_nets_to_a_loss():
    thin = ReplayResult(replays=10, seed=19, window=2, bars_left=5, p_target=0.5, p_stop=0.5, p_neither=0.0,
                        p_green=0.5, median_price_at_until=100.0, p5_price_at_until=99.0,
                        p95_price_at_until=101.0, avg_r=0.0, r_at_target=1.0, avg_gross_pnl=4.0, avg_net_pnl=-8.0)
    assert any("Costs turn the average replay" in n for n in verdict_notes(thin, time(15, 15)))


@pytest.mark.parametrize("kwargs, fragment", [
    ({"replays": 0}, "replays must be between"),
    ({"replays": 2_000_000}, "replays must be between"),
    ({"window": 11}, "window must be between"),
    ({"last_slot": 6}, "nothing to replay"),
    ({"last_slot": 80}, "nothing to replay"),
])
def test_simulate_rejects_bad_settings(kwargs, fragment):
    args = dict(last_slot=71, replays=10)
    args.update(kwargs)
    with pytest.raises(TimeMachineError, match=fragment):
        simulate(uniform_pool(FLAT), plan(), **args)


def test_simulate_refuses_an_empty_pool():
    with pytest.raises(TimeMachineError, match="bar pool is empty"):
        simulate([[] for _ in range(SESSION_BARS)], plan(), last_slot=71, replays=10)


# ---------------------------------------------------------------- verdict

def _result(p_target: float, p_stop: float, r: float = 1.0, avg_r: float = 0.0) -> ReplayResult:
    return ReplayResult(replays=1000, seed=19, window=2, bars_left=60, p_target=p_target, p_stop=p_stop,
                        p_neither=round(1 - p_target - p_stop, 4), p_green=p_target,
                        median_price_at_until=100.0, p5_price_at_until=98.0, p95_price_at_until=102.0,
                        avg_r=avg_r, r_at_target=r, avg_gross_pnl=None, avg_net_pnl=None)


@pytest.mark.parametrize("p_target, p_stop, r, head", [
    (0.30, 0.29, 1.0, "Close to break-even"),
    (0.36, 0.28, 1.0, "Leans toward the target"),
    (0.25, 0.35, 1.0, "Leans toward the stop"),
    (0.50, 0.20, 1.0, "Strongly toward the target"),
    (0.10, 0.60, 1.0, "Strongly toward the stop"),
    (0.25, 0.50, 2.0, "Close to break-even"),         # 33% is break-even at 2R
])
def test_verdict_compares_the_targets_share_with_break_even(p_target, p_stop, r, head):
    assert verdict(_result(p_target, p_stop, r), time(15, 15)).startswith(head)


def test_notes_flag_many_undecided_replays_and_an_average_that_disagrees():
    notes = verdict_notes(_result(0.20, 0.10, 1.0, avg_r=-0.2), time(15, 15))
    assert any("hit neither by 15:15" in n for n in notes)
    assert any("Average R is -0.200 all the same" in n for n in notes)


# ---------------------------------------------------------------- baseline

def test_baseline_enters_every_past_session_at_the_plans_clock_time():
    up = [(d, make_session(d, step=0.5)) for d in weekdays_before(PLAN_DAY, 3)]
    down = [(d, make_session(d, step=-0.5)) for d in weekdays_before(PLAN_DAY - timedelta(days=7), 2)]
    flat = [(PLAN_DAY - timedelta(days=21), make_session(PLAN_DAY - timedelta(days=21)))]
    b = time_of_day_baseline(up + down + flat, plan(), last_slot=71)
    assert b == BaselineResult(sessions=6, target=3, stop=2, neither=1, avg_r=pytest.approx((3 * 2 - 2 + 0) / 6, abs=1e-3))


def test_baseline_with_no_sessions_has_no_average():
    assert time_of_day_baseline([], plan(), last_slot=71).avg_r is None


# ---------------------------------------------------------------- the run

def test_run_reports_the_pool_and_never_reads_the_plan_day():
    bars = history(25) + make_session(PLAN_DAY, step=5.0)          # a wild plan day must change nothing
    with_day = run_time_machine(bars, plan(qty=10), replays=300)
    without = run_time_machine(history(25), plan(qty=10), replays=300)
    assert with_day.replay == without.replay
    assert with_day.pool_sessions == 25
    assert with_day.pool_to < PLAN_DAY
    assert with_day.until == time(15, 15)
    assert with_day.replay.bars_left == 71 - 6
    doc = json.loads(json.dumps(with_day.to_dict()))
    assert doc["plan"]["entry_time"] == "09:50" and doc["plan"]["day"] == "2026-11-12"
    assert doc["pool"]["sessions"] == 25 and doc["until"] == "15:15"
    text = with_day.summary_text()
    assert "RELIANCE long, 2026-11-12: entry 100.00 at 09:50 IST" in text
    assert "net of costs Rs" in text
    assert "Baseline: the same 1.00% stop and 2.00% target entered at 09:50" in text
    assert "not a prediction" in text


def test_run_needs_enough_full_sessions():
    with pytest.raises(TimeMachineError, match="only 5 full session"):
        run_time_machine(history(5), plan(), replays=10)


def test_run_lists_short_sessions_it_left_out():
    short_day = weekdays_before(PLAN_DAY, 30)[0]
    report = run_time_machine(make_session(short_day, bars=20) + history(22), plan(), replays=10)
    assert report.short_sessions == [short_day]
    assert "Left out 1 short session" in report.summary_text()


@pytest.mark.parametrize("until, fragment", [
    (time(9, 50), "leaves nothing to replay"),
    (time(9, 30), "leaves nothing to replay"),
    (time(15, 32), "5-minute grid"),
    (time(16, 0), "outside the session"),
])
def test_run_rejects_an_end_time_that_is_not_after_the_entry(until, fragment):
    with pytest.raises(TimeMachineError, match=fragment):
        run_time_machine(history(25), plan(), replays=10, until=until)


def test_the_trading_loop_does_not_import_the_time_machine():
    """Offline analysis only: wiring it into order flow is a separate, explicit decision."""
    src = Path(__file__).resolve().parent.parent / "src" / "jev_indstocks_trader"
    for module in ("main.py", "entry.py", "exits.py", "risk_governor.py", "execution_gateway.py"):
        assert "timemachine" not in (src / module).read_text(encoding="utf-8"), module


# ---------------------------------------------------------------- CLI

PLAN_FLAGS = ["RELIANCE", "--entry", "100", "--stop", "99", "--target", "102", "--entry-time", "09:50",
              "--date", "2026-11-12", "--replays", "200"]


@pytest.fixture
def bars_csv(tmp_path):
    path = tmp_path / "RELIANCE_5m.csv"
    save_to_csv(history(25, step=0.02, wick=0.3), path)
    return path


def test_cli_prints_the_report(bars_csv, capsys):
    assert cli.main(PLAN_FLAGS + ["--csv", str(bars_csv), "--qty", "50"]) == 0
    out = capsys.readouterr().out
    assert "200 replays of the rest of the session to 15:15" in out
    assert "net of costs Rs" in out
    assert "nothing was placed" in out


def test_cli_json_is_machine_readable(bars_csv, capsys):
    assert cli.main(PLAN_FLAGS + ["--csv", str(bars_csv), "--json", "--until", "14:00"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["until"] == "14:00"
    assert doc["replay"]["replays"] == 200
    assert doc["pool"]["sessions"] == 25


def test_cli_missing_bars_is_a_runtime_error(tmp_path, capsys):
    assert cli.main(PLAN_FLAGS + ["--csv", str(tmp_path / "nope.csv")]) == 1
    assert "pass --fetch" in capsys.readouterr().err


def test_cli_too_few_sessions_is_a_runtime_error(tmp_path, capsys):
    path = tmp_path / "few.csv"
    save_to_csv(history(3), path)
    assert cli.main(PLAN_FLAGS + ["--csv", str(path)]) == 1
    assert "needs at least 20" in capsys.readouterr().err


def test_cli_corrupt_csv_is_a_runtime_error(tmp_path, capsys):
    path = tmp_path / "bad.csv"
    path.write_text("timestamp,open\nnot-a-time,1\n")
    assert cli.main(PLAN_FLAGS + ["--csv", str(path)]) == 1
    assert "could not read" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [
    ["RELIANCE", "--entry", "100", "--stop", "101", "--target", "102", "--entry-time", "09:50"],   # stop above entry
    ["RELIANCE", "--entry", "100", "--stop", "99", "--target", "102", "--entry-time", "9.50"],     # not a time
    ["RELIANCE", "--entry", "100", "--stop", "99", "--target", "102", "--entry-time", "09:52"],    # off the grid
    ["RELIANCE", "--entry", "100", "--stop", "99", "--target", "102"],                              # no entry time
    ["RELIANCE", "--entry", "100", "--stop", "99", "--target", "102", "--entry-time", "09:50", "--replays", "0"],
    ["RELIANCE", "--entry", "100", "--stop", "99", "--target", "102", "--entry-time", "09:50", "--date", "12-11"],
])
def test_cli_usage_errors_exit_2(flags, capsys):
    assert cli.main(flags) == 2
    assert capsys.readouterr().err


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def yahoo_body(stamps, closes):
    return {"chart": {"result": [{"timestamp": stamps, "indicators": {"quote": [{
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1000 if c is not None else None for c in closes]}]}}], "error": None}}


def test_fetch_parses_bars_in_ist_and_skips_empty_rows(monkeypatch):
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen.update(url=url, params=params, timeout=timeout)
        # 2026-11-12 03:45 UTC = 09:15 IST, then 09:20 (no price), then 09:25
        return FakeResponse(yahoo_body([1794455100, 1794455400, 1794455700], [100.0, None, 101.456]))

    monkeypatch.setattr(cli.requests, "get", fake_get)
    bars = cli.fetch_yahoo_5m("m&m")
    assert seen["url"].endswith("/M%26M.NS")
    assert seen["params"] == {"range": "60d", "interval": "5m"}
    assert seen["timeout"] == cli.FETCH_TIMEOUT_S
    assert [(b.timestamp.astimezone(IST).strftime("%Y-%m-%d %H:%M"), b.close) for b in bars] == [
        ("2026-11-12 09:15", 100.0), ("2026-11-12 09:25", 101.46)]
    assert all(b.timestamp.utcoffset() == timedelta(hours=5, minutes=30) for b in bars)


@pytest.mark.parametrize("response, fragment", [
    (FakeResponse({}, status=429), "did not answer"),
    (FakeResponse(ValueError("no json")), "not JSON"),
    (FakeResponse({"chart": {"result": None, "error": {"description": "No data found"}}}), "no 5-minute data"),
    (FakeResponse({"chart": {"result": [{"timestamp": [1], "indicators": {}}]}}), "not the shape"),
    (FakeResponse(yahoo_body([1794455100], [None])), "no usable 5-minute bars"),
    (FakeResponse(["unexpected"]), "no 5-minute data"),
])
def test_fetch_failures_are_plain_errors(monkeypatch, response, fragment):
    monkeypatch.setattr(cli.requests, "get", lambda *a, **k: response)
    with pytest.raises(cli.FetchError, match=fragment):
        cli.fetch_yahoo_5m("RELIANCE")


def test_fetch_wraps_a_dropped_connection(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("connection reset")
    monkeypatch.setattr(cli.requests, "get", boom)
    with pytest.raises(cli.FetchError, match="did not answer"):
        cli.fetch_yahoo_5m("RELIANCE")


def test_cli_fetch_saves_the_bars_then_runs(tmp_path, monkeypatch, capsys):
    path = tmp_path / "minute" / "RELIANCE_5m.csv"
    aware = []
    for d in weekdays_before(PLAN_DAY, 22):
        aware += make_session(d, step=0.02, wick=0.3, aware=True)
    monkeypatch.setattr(cli, "fetch_yahoo_5m", lambda symbol: aware)
    assert cli.main(PLAN_FLAGS + ["--csv", str(path), "--fetch"]) == 0
    assert path.exists()
    assert "Fetched 1,650 five-minute bars" in capsys.readouterr().out
    assert cli.main(PLAN_FLAGS + ["--csv", str(path)]) == 0      # the saved file reads back


def test_cli_fetch_failure_is_a_runtime_error(tmp_path, monkeypatch, capsys):
    def down(symbol):
        raise cli.FetchError("Yahoo did not answer for RELIANCE: timed out")
    monkeypatch.setattr(cli, "fetch_yahoo_5m", down)
    assert cli.main(PLAN_FLAGS + ["--csv", str(tmp_path / "x.csv"), "--fetch"]) == 1
    assert "did not answer" in capsys.readouterr().err


def test_default_csv_path_is_per_symbol(tmp_path):
    assert cli.default_csv("reliance", tmp_path) == tmp_path / "RELIANCE_5m.csv"
