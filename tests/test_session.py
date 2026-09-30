from datetime import datetime
from zoneinfo import ZoneInfo

from jev_indstocks_trader.session import minutes_until_flatten, session_phase  # holiday coverage below

IST = ZoneInfo("Asia/Kolkata")


def _dt(h, m, weekday=0):
    # 2026-09-21 is a Monday
    day = 21 + weekday
    return datetime(2026, 9, day, h, m, tzinfo=IST)


def test_open_mid_session():
    assert session_phase(_dt(10, 30)) == "open"


def test_flatten_window():
    assert session_phase(_dt(15, 16)) == "flatten"
    assert session_phase(_dt(15, 29)) == "flatten"


def test_closed_after_bell_and_weekend():
    assert session_phase(_dt(15, 30)) == "closed"
    assert session_phase(_dt(8, 0)) == "closed"
    assert session_phase(_dt(11, 0, weekday=5)) == "closed"  # Saturday


def test_weekday_holiday_is_closed():
    gandhi = datetime(2026, 10, 2, 10, 30, tzinfo=IST)
    assert session_phase(gandhi) == "closed"
    assert minutes_until_flatten(gandhi) is None


def test_minutes_until_flatten_midday():
    mins = minutes_until_flatten(_dt(14, 0))
    assert mins is not None
    assert 74.0 < mins < 76.0  # 14:00 → 15:15 = 75 min


def test_minutes_until_flatten_near_close():
    mins = minutes_until_flatten(_dt(15, 0))
    assert mins is not None
    assert 14.0 < mins < 16.0  # 15:00 → 15:15 = 15 min


def test_minutes_until_flatten_past_flatten():
    mins = minutes_until_flatten(_dt(15, 20))
    assert mins is not None
    assert mins < 0  # already past flatten


def test_minutes_until_flatten_weekend():
    assert minutes_until_flatten(_dt(11, 0, weekday=5)) is None
