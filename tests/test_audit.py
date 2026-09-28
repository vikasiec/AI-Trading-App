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
