from jev_indstocks_trader.execution_gateway import ExecutionGateway


def test_parse_quote_entry_returns_none_when_change_missing():
    """Fix #2: missing day_change_pct field should yield None, not 0.0."""
    result = ExecutionGateway._parse_quote_entry({"live_price": 100.0})
    assert result["day_change_pct"] is None


def test_parse_quote_entry_returns_float_when_present():
    result = ExecutionGateway._parse_quote_entry({"live_price": 100.0, "day_change_pct": 1.5})
    assert result["day_change_pct"] == 1.5
