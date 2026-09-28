import json

from jev_indstocks_trader.audit import AuditTrail


def test_log_skip_writes_skip_row(tmp_path):
    """Fix #10: log_skip must produce a SKIP action row with the reason."""
    trail = AuditTrail(tmp_path / "audit.jsonl")
    trail.log_skip("NSE_2885", "index_veto")

    with open(tmp_path / "audit.jsonl") as f:
        row = json.loads(f.readline())

    assert row["action"] == "SKIP"
    assert row["skip_reason"] == "index_veto"
    assert row["security_id"] == "NSE_2885"
    assert "ts" in row


def test_log_skip_keeps_one_row_per_reason_per_day(tmp_path):
    trail = AuditTrail(tmp_path / "audit.jsonl")
    trail.log_skip("NSE_2885", "jev_rate_limit", symbol="RELIANCE")
    trail.log_skip("NSE_2885", "jev_rate_limit", symbol="RELIANCE")
    trail.log_skip("NSE_2885", "gap_veto", symbol="RELIANCE")

    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["symbol"] == "RELIANCE"
    assert json.loads(lines[1])["skip_reason"] == "gap_veto"


def test_log_decision_keeps_the_reason_and_prices(tmp_path):
    trail = AuditTrail(tmp_path / "audit.jsonl")
    trail.log_decision(
        security_id="NSE_2885",
        jev_conviction=0.9,
        jev_confidence=0.7,
        action="SKIP",
        latency_ms=12.0,
        reason="slippage_0.0200_exceeds_collar",
        symbol="RELIANCE",
        detail={"scored_at_price": 100.0, "live_ltp": 103.0, "qty": 0, "paper": True},
    )
    trail.update_outcome(
        "NSE_2885_ignored", fill_price=101.0, realized_pnl=10.0,
        net_pnl=8.0, exit_reason="stop_loss",
    )

    rows = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert rows[0]["reason"] == "slippage_0.0200_exceeds_collar"
    assert rows[0]["detail"]["live_ltp"] == 103.0
    assert rows[1]["exit_reason"] == "stop_loss"
    assert rows[1]["net_pnl"] == 8.0
