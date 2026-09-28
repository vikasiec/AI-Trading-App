from jev_indstocks_trader.gaps import GapBook


def test_gap_freezes_first_print():
    g = GapBook()
    g.roll_day("2026-09-26")
    g.observe("RELIANCE", 1.2)
    g.observe("RELIANCE", 2.8)
    assert g.get("reliance") == 1.2
    g.roll_day("2026-09-29")
    assert g.get("RELIANCE") is None


def test_gap_skips_none_does_not_lock_zero():
    """Fix #2: None day_change_pct must not store a gap."""
    g = GapBook()
    g.roll_day("2026-09-26")
    g.observe("RELIANCE", None)
    assert g.get("RELIANCE") is None
    g.observe("RELIANCE", 1.5)
    assert g.get("RELIANCE") == 1.5
