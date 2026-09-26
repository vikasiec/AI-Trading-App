from jev_indstocks_trader.opening_range import OpeningRangeBook


def test_opening_range_tracks_high_low_and_rolls_day():
    book = OpeningRangeBook()
    book.roll_day("2026-09-26")
    book.update("RELIANCE", 100)
    book.update("RELIANCE", 104)
    book.update("RELIANCE", 99)
    rng = book.get("reliance")
    assert rng is not None
    assert rng.high == 104
    assert rng.low == 99
    assert rng.samples == 3
    book.roll_day("2026-09-29")
    assert book.get("RELIANCE") is None
