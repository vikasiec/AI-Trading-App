"""End-of-day prediction accuracy analysis.

Reads the audit trail for a given date, fetches actual closing prices,
and computes how accurate the bot's decisions were. Each BUY decision
is checked: did the stock actually move in the predicted direction by
close? SKIP decisions with high conviction are also tracked as
"missed opportunities".

Results are appended to a JSONL tracking file so accuracy trends can
be analysed over time and fed back into strategy refinement.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _price_from_decision(decision: dict, *keys: str) -> float:
    """Prices live on the row or under detail. Outcome rows carry fill_price."""
    detail = decision.get("detail") if isinstance(decision.get("detail"), dict) else {}
    for key in keys:
        for source in (decision, detail):
            raw = source.get(key)
            if raw in (None, "", 0, 0.0):
                continue
            try:
                price = float(raw)
            except (TypeError, ValueError):
                continue
            if price > 0:
                return price
    return 0.0


@dataclass
class PredictionOutcome:
    security_id: str
    symbol: str
    action: str
    jev_conviction: float
    jev_confidence: float
    entry_price: float
    close_price: float
    price_move_pct: float
    direction_correct: bool
    had_news: bool | None = None


@dataclass
class DailyAccuracyReport:
    date: str
    total_decisions: int
    buy_decisions: int
    skip_decisions: int
    buy_outcomes: list[PredictionOutcome] = field(default_factory=list)
    high_conviction_skips: list[PredictionOutcome] = field(default_factory=list)

    @property
    def buy_accuracy_pct(self) -> float | None:
        if not self.buy_outcomes:
            return None
        correct = sum(1 for o in self.buy_outcomes if o.direction_correct)
        return (correct / len(self.buy_outcomes)) * 100

    @property
    def avg_winner_conviction(self) -> float | None:
        winners = [o for o in self.buy_outcomes if o.direction_correct]
        if not winners:
            return None
        return sum(o.jev_conviction for o in winners) / len(winners)

    @property
    def avg_loser_conviction(self) -> float | None:
        losers = [o for o in self.buy_outcomes if not o.direction_correct]
        if not losers:
            return None
        return sum(o.jev_conviction for o in losers) / len(losers)

    @property
    def avg_price_move_pct(self) -> float | None:
        if not self.buy_outcomes:
            return None
        return sum(o.price_move_pct for o in self.buy_outcomes) / len(self.buy_outcomes)

    @property
    def missed_winners_count(self) -> int:
        return sum(1 for o in self.high_conviction_skips if o.direction_correct)


def load_daily_decisions(
    audit_log_path: Path, target_date: str,
) -> tuple[list[dict], list[dict]]:
    """Extract BUY and high-conviction SKIP decisions for a given date.

    Returns (buy_decisions, skip_decisions).
    """
    buys: list[dict] = []
    skips: list[dict] = []

    if not audit_log_path.exists():
        return buys, skips

    with open(audit_log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("type") == "outcome_update":
                continue
            if "jev_conviction" not in record:
                continue
            ts = record.get("ts", "")
            if not ts.startswith(target_date):
                continue

            action = record.get("action", "")
            if action.startswith("BUY"):
                buys.append(record)
            elif action == "SKIP" and record.get("jev_conviction", 0) >= 0.7:
                skips.append(record)

    return buys, skips


def compute_accuracy(
    buy_decisions: list[dict],
    skip_decisions: list[dict],
    closing_prices: dict[str, float],
    target_date: str,
) -> DailyAccuracyReport:
    """Compare decisions against actual closing prices."""
    buy_outcomes: list[PredictionOutcome] = []
    for decision in buy_decisions:
        sid = decision["security_id"]
        close = closing_prices.get(sid)
        if close is None:
            continue
        entry = _price_from_decision(decision, "fill_price", "scored_at_price")
        if not entry or entry <= 0:
            continue
        move_pct = ((close - entry) / entry) * 100
        buy_outcomes.append(PredictionOutcome(
            security_id=sid,
            symbol=decision.get("symbol", sid),
            action=decision["action"],
            jev_conviction=decision["jev_conviction"],
            jev_confidence=decision["jev_confidence"],
            entry_price=entry,
            close_price=close,
            price_move_pct=round(move_pct, 3),
            direction_correct=move_pct > 0,
            had_news=decision.get("had_news"),
        ))

    skip_outcomes: list[PredictionOutcome] = []
    for decision in skip_decisions:
        sid = decision["security_id"]
        close = closing_prices.get(sid)
        if close is None:
            continue
        scored_price = _price_from_decision(decision, "scored_at_price", "fill_price")
        if not scored_price or scored_price <= 0:
            continue
        move_pct = ((close - scored_price) / scored_price) * 100
        skip_outcomes.append(PredictionOutcome(
            security_id=sid,
            symbol=decision.get("symbol", sid),
            action="SKIP",
            jev_conviction=decision["jev_conviction"],
            jev_confidence=decision["jev_confidence"],
            entry_price=scored_price,
            close_price=close,
            price_move_pct=round(move_pct, 3),
            direction_correct=move_pct > 0,
            had_news=decision.get("had_news"),
        ))

    return DailyAccuracyReport(
        date=target_date,
        total_decisions=len(buy_decisions) + len(skip_decisions),
        buy_decisions=len(buy_decisions),
        skip_decisions=len(skip_decisions),
        buy_outcomes=buy_outcomes,
        high_conviction_skips=skip_outcomes,
    )


def save_daily_report(report: DailyAccuracyReport, tracking_path: Path) -> None:
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "date": report.date,
        "total_decisions": report.total_decisions,
        "buy_decisions": report.buy_decisions,
        "skip_decisions": report.skip_decisions,
        "buy_accuracy_pct": report.buy_accuracy_pct,
        "avg_winner_conviction": report.avg_winner_conviction,
        "avg_loser_conviction": report.avg_loser_conviction,
        "avg_price_move_pct": report.avg_price_move_pct,
        "missed_winners": report.missed_winners_count,
        "outcomes": [asdict(o) for o in report.buy_outcomes],
        "high_conviction_skips": [asdict(o) for o in report.high_conviction_skips],
    }
    with open(tracking_path, "a") as f:
        f.write(json.dumps(summary) + "\n")


def format_accuracy_alert(report: DailyAccuracyReport) -> str:
    sections = [f"📊 EOD Accuracy Report — {report.date}\n━━━━━━━━━━━━━━━━━━━━━"]

    if report.buy_outcomes:
        acc = report.buy_accuracy_pct
        sections.append(
            f"\n🎯 BUY Accuracy: {acc:.0f}% "
            f"({sum(1 for o in report.buy_outcomes if o.direction_correct)}"
            f"/{len(report.buy_outcomes)})"
        )
        sections.append(f"Avg move: {report.avg_price_move_pct:+.2f}%")

        if report.avg_winner_conviction is not None:
            sections.append(f"Winner avg conviction: {report.avg_winner_conviction:.2f}")
        if report.avg_loser_conviction is not None:
            sections.append(f"Loser avg conviction: {report.avg_loser_conviction:.2f}")

        sections.append("\nTrade details:")
        for o in sorted(report.buy_outcomes, key=lambda x: x.price_move_pct, reverse=True):
            icon = "✅" if o.direction_correct else "❌"
            sections.append(
                f"  {icon} {o.symbol}: {o.price_move_pct:+.2f}% "
                f"(conv {o.jev_conviction:.2f}, ₹{o.entry_price:,.1f}→₹{o.close_price:,.1f})"
            )
    else:
        sections.append("\nNo BUY decisions today.")

    if report.high_conviction_skips:
        missed = report.missed_winners_count
        sections.append(
            f"\n🔍 High-conviction SKIPs: {len(report.high_conviction_skips)} "
            f"({missed} would have been winners)"
        )
        top_misses = sorted(
            [o for o in report.high_conviction_skips if o.direction_correct],
            key=lambda x: x.price_move_pct,
            reverse=True,
        )[:5]
        if top_misses:
            sections.append("Top missed opportunities:")
            for o in top_misses:
                sections.append(
                    f"  💡 {o.symbol}: {o.price_move_pct:+.2f}% "
                    f"(conv {o.jev_conviction:.2f})"
                )

    sections.append(f"\n━━━━━━━━━━━━━━━━━━━━━\nTotal scored: {report.total_decisions}")
    return "\n".join(sections)


def load_accuracy_history(tracking_path: Path, last_n: int = 30) -> list[dict]:
    if not tracking_path.exists():
        return []
    records = []
    with open(tracking_path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records[-last_n:]


def format_trend_summary(history: list[dict]) -> str | None:
    days_with_buys = [d for d in history if d.get("buy_accuracy_pct") is not None]
    if len(days_with_buys) < 3:
        return None

    avg_accuracy = sum(d["buy_accuracy_pct"] for d in days_with_buys) / len(days_with_buys)
    recent = days_with_buys[-5:]
    recent_avg = sum(d["buy_accuracy_pct"] for d in recent) / len(recent)

    total_buys = sum(d.get("buy_decisions", 0) for d in history)
    total_missed = sum(d.get("missed_winners", 0) for d in history)

    lines = [
        f"📈 Accuracy Trend ({len(history)} days)",
        f"  Overall: {avg_accuracy:.0f}%",
        f"  Last {len(recent)} days: {recent_avg:.0f}%",
        f"  Total trades: {total_buys}",
        f"  Total missed winners: {total_missed}",
    ]

    if len(days_with_buys) >= 5:
        trend = recent_avg - avg_accuracy
        arrow = "↗" if trend > 2 else "↘" if trend < -2 else "→"
        lines.append(f"  Trend: {arrow} ({trend:+.1f}%)")

    return "\n".join(lines)
