"""Jev calibration analysis -- tests H1 and H2 from docs/INTELLIGENCE_ROADMAP.md.

H1: Jev's conviction score is positively correlated with forward net P&L.
H2: Jev's confidence field predicts consistency of outcome (lower variance),
    not necessarily a better mean.

Reads the audit trail (audit.py's JSONL format), joins each decision to its
outcome (if the position has closed), buckets by conviction and separately
by confidence, and reports mean/median net P&L, win rate, and sample size
per bucket.

This is deliberately NOT a trading algorithm -- it produces a report for a
human to read, not a signal the bot acts on. The 0.80 conviction threshold
in RiskConfig stays a config constant until this analysis (run on a real
sample, not a handful of paper trades) says otherwise.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MIN_SAMPLE_SIZE_FOR_CONFIDENCE = 30  # below this, a bucket's numbers are noise, not signal


@dataclass
class ClosedTrade:
    decision_id: str
    security_id: str
    jev_conviction: float
    jev_confidence: float
    net_pnl: float
    realized_pnl: float


@dataclass
class BucketStats:
    bucket_label: str
    n: int
    mean_net_pnl: Optional[float]
    median_net_pnl: Optional[float]
    win_rate: Optional[float]  # fraction of trades with net_pnl > 0

    @property
    def sample_too_small(self) -> bool:
        return self.n < MIN_SAMPLE_SIZE_FOR_CONFIDENCE


@dataclass
class CalibrationReport:
    total_decisions: int
    closed_trades: int
    conviction_buckets: list[BucketStats] = field(default_factory=list)
    confidence_buckets: list[BucketStats] = field(default_factory=list)

    def summary_text(self) -> str:
        lines = [
            f"Decisions logged: {self.total_decisions}  |  Closed trades with outcomes: {self.closed_trades}",
            "",
            "-- H1: net P&L by Jev conviction bucket --",
        ]
        lines.extend(_format_buckets(self.conviction_buckets))
        lines.append("")
        lines.append("-- H2: net P&L by Jev confidence bucket --")
        lines.extend(_format_buckets(self.confidence_buckets))
        if self.closed_trades < MIN_SAMPLE_SIZE_FOR_CONFIDENCE:
            lines.append("")
            lines.append(
                f"NOTE: only {self.closed_trades} closed trades total -- every bucket below is "
                f"too small to draw conclusions from (need >= {MIN_SAMPLE_SIZE_FOR_CONFIDENCE} per "
                f"bucket). This run checks the plumbing works, not whether Jev is calibrated."
            )
        return "\n".join(lines)


def _format_buckets(buckets: list[BucketStats]) -> list[str]:
    if not buckets:
        return ["  (no closed trades to bucket)"]
    out = []
    for b in buckets:
        flag = " [SAMPLE TOO SMALL]" if b.sample_too_small else ""
        if b.n == 0:
            out.append(f"  {b.bucket_label}: n=0{flag}")
            continue
        out.append(
            f"  {b.bucket_label}: n={b.n}, mean_net_pnl={b.mean_net_pnl:.2f}, "
            f"median_net_pnl={b.median_net_pnl:.2f}, win_rate={b.win_rate:.1%}{flag}"
        )
    return out


def load_closed_trades(audit_log_path: Path) -> list[ClosedTrade]:
    """Reads the JSONL audit trail and joins log_decision records to their
    outcome_update by decision_id. Decisions with no outcome yet (still
    open, or never traded) are excluded.
    """
    decisions: dict[str, dict] = {}
    outcomes: dict[str, dict] = {}

    if not audit_log_path.exists():
        return []

    with open(audit_log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("type") == "outcome_update":
                outcomes[record["decision_id"]] = record
            elif "jev_conviction" in record:
                decisions[record["decision_id"]] = record

    trades = []
    for decision_id, decision in decisions.items():
        outcome = outcomes.get(decision_id)
        if outcome is None or outcome.get("net_pnl") is None:
            continue
        trades.append(ClosedTrade(
            decision_id=decision_id,
            security_id=decision["security_id"],
            jev_conviction=decision["jev_conviction"],
            jev_confidence=decision["jev_confidence"],
            net_pnl=outcome["net_pnl"],
            realized_pnl=outcome["realized_pnl"],
        ))
    return trades


def _bucket_by(trades: list[ClosedTrade], key_fn, edges: list[float]) -> list[BucketStats]:
    """edges like [0.5, 0.6, 0.7, 0.8, 0.9, 1.0] -> buckets [0.5-0.6), [0.6-0.7), ..."""
    buckets = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        label = f"{lo:.1f}-{hi:.1f}"
        in_bucket = [t for t in trades if lo <= key_fn(t) < hi]
        n = len(in_bucket)
        if n == 0:
            buckets.append(BucketStats(label, 0, None, None, None))
            continue
        pnls = [t.net_pnl for t in in_bucket]
        wins = sum(1 for p in pnls if p > 0)
        buckets.append(BucketStats(
            bucket_label=label, n=n,
            mean_net_pnl=statistics.mean(pnls),
            median_net_pnl=statistics.median(pnls),
            win_rate=wins / n,
        ))
    return buckets


def run_calibration_report(
    audit_log_path: Path,
    total_decisions: Optional[int] = None,
    bucket_edges: tuple[float, ...] = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
) -> CalibrationReport:
    trades = load_closed_trades(audit_log_path)

    if total_decisions is None:
        # Count every log_decision line, including ones with no outcome yet.
        total_decisions = 0
        if audit_log_path.exists():
            with open(audit_log_path) as f:
                for line in f:
                    line = line.strip()
                    if line and "jev_conviction" in json.loads(line):
                        total_decisions += 1

    return CalibrationReport(
        total_decisions=total_decisions,
        closed_trades=len(trades),
        conviction_buckets=_bucket_by(trades, lambda t: t.jev_conviction, list(bucket_edges)),
        confidence_buckets=_bucket_by(trades, lambda t: t.jev_confidence, list(bucket_edges)),
    )


if __name__ == "__main__":
    import sys

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./audit_trail.jsonl")
    report = run_calibration_report(path)
    print(report.summary_text())
