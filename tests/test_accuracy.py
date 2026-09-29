"""Tests for end-of-day prediction accuracy analysis."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from jev_indstocks_trader.accuracy import (
    DailyAccuracyReport,
    PredictionOutcome,
    compute_accuracy,
    format_accuracy_alert,
    format_trend_summary,
    load_accuracy_history,
    load_daily_decisions,
    save_daily_report,
)


def _write_audit_entries(path: Path, entries: list[dict]) -> None:
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


class TestLoadDailyDecisions:
    def test_filters_by_date(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        _write_audit_entries(audit, [
            {"ts": "2026-09-28T10:00:00", "security_id": "A", "jev_conviction": 0.9,
             "jev_confidence": 0.8, "action": "BUY (paper)"},
            {"ts": "2026-09-27T10:00:00", "security_id": "B", "jev_conviction": 0.85,
             "jev_confidence": 0.7, "action": "BUY (paper)"},
            {"ts": "2026-09-28T11:00:00", "security_id": "C", "jev_conviction": 0.75,
             "jev_confidence": 0.6, "action": "SKIP"},
        ])
        buys, skips = load_daily_decisions(audit, "2026-09-28")
        assert len(buys) == 1
        assert buys[0]["security_id"] == "A"
        assert len(skips) == 1
        assert skips[0]["security_id"] == "C"

    def test_ignores_low_conviction_skips(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        _write_audit_entries(audit, [
            {"ts": "2026-09-28T10:00:00", "security_id": "A", "jev_conviction": 0.3,
             "jev_confidence": 0.4, "action": "SKIP"},
        ])
        buys, skips = load_daily_decisions(audit, "2026-09-28")
        assert len(buys) == 0
        assert len(skips) == 0

    def test_ignores_outcome_updates(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        _write_audit_entries(audit, [
            {"ts": "2026-09-28T10:00:00", "type": "outcome_update",
             "decision_id": "A_123", "fill_price": 100, "realized_pnl": 5},
        ])
        buys, skips = load_daily_decisions(audit, "2026-09-28")
        assert len(buys) == 0
        assert len(skips) == 0

    def test_empty_file(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        audit.touch()
        buys, skips = load_daily_decisions(audit, "2026-09-28")
        assert buys == []
        assert skips == []

    def test_missing_file(self, tmp_path):
        buys, skips = load_daily_decisions(tmp_path / "nonexistent.jsonl", "2026-09-28")
        assert buys == []
        assert skips == []


class TestComputeAccuracy:
    def test_correct_direction(self):
        buys = [{"security_id": "RELIANCE", "symbol": "RELIANCE", "action": "BUY (paper)",
                 "jev_conviction": 0.9, "jev_confidence": 0.8, "scored_at_price": 100.0}]
        prices = {"RELIANCE": 105.0}
        report = compute_accuracy(buys, [], prices, "2026-09-28")
        assert len(report.buy_outcomes) == 1
        assert report.buy_outcomes[0].direction_correct is True
        assert report.buy_outcomes[0].price_move_pct == 5.0
        assert report.buy_accuracy_pct == 100.0

    def test_wrong_direction(self):
        buys = [{"security_id": "TCS", "symbol": "TCS", "action": "BUY (paper)",
                 "jev_conviction": 0.85, "jev_confidence": 0.7, "scored_at_price": 200.0}]
        prices = {"TCS": 195.0}
        report = compute_accuracy(buys, [], prices, "2026-09-28")
        assert report.buy_outcomes[0].direction_correct is False
        assert report.buy_accuracy_pct == 0.0

    def test_mixed_results(self):
        buys = [
            {"security_id": "A", "symbol": "A", "action": "BUY (paper)",
             "jev_conviction": 0.9, "jev_confidence": 0.8, "scored_at_price": 100.0},
            {"security_id": "B", "symbol": "B", "action": "BUY (paper)",
             "jev_conviction": 0.8, "jev_confidence": 0.7, "scored_at_price": 50.0},
        ]
        prices = {"A": 110.0, "B": 48.0}
        report = compute_accuracy(buys, [], prices, "2026-09-28")
        assert report.buy_accuracy_pct == 50.0
        assert report.avg_winner_conviction == 0.9
        assert report.avg_loser_conviction == 0.8

    def test_skip_with_missing_price(self):
        buys = [{"security_id": "X", "symbol": "X", "action": "BUY (paper)",
                 "jev_conviction": 0.9, "jev_confidence": 0.8, "scored_at_price": 100.0}]
        report = compute_accuracy(buys, [], {}, "2026-09-28")
        assert len(report.buy_outcomes) == 0
        assert report.buy_accuracy_pct is None

    def test_high_conviction_skips_tracked(self):
        skips = [{"security_id": "INFY", "symbol": "INFY", "action": "SKIP",
                  "jev_conviction": 0.75, "jev_confidence": 0.6, "scored_at_price": 1500.0}]
        prices = {"INFY": 1550.0}
        report = compute_accuracy([], skips, prices, "2026-09-28")
        assert len(report.high_conviction_skips) == 1
        assert report.high_conviction_skips[0].direction_correct is True
        assert report.missed_winners_count == 1

    def test_no_decisions(self):
        report = compute_accuracy([], [], {}, "2026-09-28")
        assert report.buy_accuracy_pct is None
        assert report.avg_price_move_pct is None
        assert report.missed_winners_count == 0


class TestSaveAndLoadHistory:
    def test_round_trip(self, tmp_path):
        tracking = tmp_path / "tracking.jsonl"
        report = DailyAccuracyReport(
            date="2026-09-28", total_decisions=5,
            buy_decisions=3, skip_decisions=2,
            buy_outcomes=[
                PredictionOutcome("A", "A", "BUY", 0.9, 0.8, 100, 105, 5.0, True),
            ],
        )
        save_daily_report(report, tracking)
        history = load_accuracy_history(tracking)
        assert len(history) == 1
        assert history[0]["date"] == "2026-09-28"
        assert history[0]["buy_accuracy_pct"] == 100.0

    def test_multiple_days(self, tmp_path):
        tracking = tmp_path / "tracking.jsonl"
        for i in range(5):
            report = DailyAccuracyReport(
                date=f"2026-09-{20+i:02d}", total_decisions=3,
                buy_decisions=2, skip_decisions=1,
                buy_outcomes=[
                    PredictionOutcome("A", "A", "BUY", 0.9, 0.8, 100, 105, 5.0, True),
                    PredictionOutcome("B", "B", "BUY", 0.8, 0.7, 50, 48, -4.0, False),
                ],
            )
            save_daily_report(report, tracking)
        history = load_accuracy_history(tracking, last_n=3)
        assert len(history) == 3


class TestFormatAlert:
    def test_format_with_outcomes(self):
        report = DailyAccuracyReport(
            date="2026-09-28", total_decisions=2,
            buy_decisions=2, skip_decisions=0,
            buy_outcomes=[
                PredictionOutcome("A", "RELIANCE", "BUY", 0.9, 0.8, 2500, 2550, 2.0, True),
                PredictionOutcome("B", "TCS", "BUY", 0.85, 0.7, 3500, 3450, -1.43, False),
            ],
        )
        text = format_accuracy_alert(report)
        assert "50%" in text
        assert "RELIANCE" in text
        assert "TCS" in text
        assert "✅" in text
        assert "❌" in text

    def test_format_no_decisions(self):
        report = DailyAccuracyReport(
            date="2026-09-28", total_decisions=0,
            buy_decisions=0, skip_decisions=0,
        )
        text = format_accuracy_alert(report)
        assert "No BUY decisions" in text


class TestSidToSymbolMapping:
    """Regression test: EOD script must use NSE symbols, not numeric security_ids."""

    def test_builds_sid_to_symbol_from_decisions(self):
        decisions = [
            {"security_id": "13147", "symbol": "PVRINOX", "action": "SKIP",
             "jev_conviction": 0.72, "jev_confidence": 0.47},
            {"security_id": "18011", "symbol": "WHIRLPOOL", "action": "SKIP",
             "jev_conviction": 0.56, "jev_confidence": 0.44},
        ]
        sid_to_symbol: dict[str, str] = {}
        for d in decisions:
            sid = d["security_id"]
            sym = d.get("symbol", sid)
            sid_to_symbol.setdefault(sid, sym)

        assert sid_to_symbol == {"13147": "PVRINOX", "18011": "WHIRLPOOL"}
        symbol_to_sid = {sym: sid for sid, sym in sid_to_symbol.items()}
        tickers = [f"{sym}.NS" for sym in symbol_to_sid]
        assert "PVRINOX.NS" in tickers
        assert "WHIRLPOOL.NS" in tickers
        assert "13147.NS" not in tickers

    def test_falls_back_to_sid_when_no_symbol(self):
        decisions = [
            {"security_id": "99999", "action": "SKIP",
             "jev_conviction": 0.80, "jev_confidence": 0.60},
        ]
        sid_to_symbol: dict[str, str] = {}
        for d in decisions:
            sid = d["security_id"]
            sym = d.get("symbol", sid)
            sid_to_symbol.setdefault(sid, sym)

        assert sid_to_symbol == {"99999": "99999"}


class TestTrendSummary:
    def test_insufficient_data(self):
        history = [{"buy_accuracy_pct": 60, "buy_decisions": 2}]
        assert format_trend_summary(history) is None

    def test_trend_with_enough_data(self):
        history = [
            {"buy_accuracy_pct": 60, "buy_decisions": 3, "missed_winners": 1}
            for _ in range(5)
        ]
        trend = format_trend_summary(history)
        assert trend is not None
        assert "60%" in trend
        assert "15" in trend  # 5 * 3 total trades
