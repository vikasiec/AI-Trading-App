from datetime import datetime
from zoneinfo import ZoneInfo

from jev_indstocks_trader.session import session_phase

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
