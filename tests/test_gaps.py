from jev_indstocks_trader.gaps import GapBook


def test_gap_freezes_first_print():
    g = GapBook()
    g.roll_day("2026-09-26")
    g.observe("RELIANCE", 1.2)
    g.observe("RELIANCE", 2.8)
    assert g.get("reliance") == 1.2
    g.roll_day("2026-09-29")
    assert g.get("RELIANCE") is None
