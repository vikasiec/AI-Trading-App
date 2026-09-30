"""NSE cash session clock (IST).

Entries only in 09:15–15:15 IST on weekdays. 15:15–15:30 is flatten-only
so INTRADAY square-off is ours, not the broker's 15:20–15:30 scramble.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE capital-market weekday holidays for 2026 (circular NSE/CMTR/71775,
# plus the 15 Jan 2026 Maharashtra municipal election closure).
# Weekend holidays are already closed by weekday(). Muhurat on 8 Nov 2026
# is a Sunday and is not treated as a normal cash session.
NSE_CASH_HOLIDAYS_2026 = frozenset({
    date(2026, 1, 15),
    date(2026, 1, 26),
    date(2026, 3, 3),
    date(2026, 3, 26),
    date(2026, 3, 31),
    date(2026, 4, 3),
    date(2026, 4, 14),
    date(2026, 5, 1),
    date(2026, 5, 28),
    date(2026, 6, 26),
    date(2026, 9, 14),
    date(2026, 10, 2),
    date(2026, 10, 20),
    date(2026, 11, 10),
    date(2026, 11, 24),
    date(2026, 12, 25),
})


def now_ist(now: datetime | None = None) -> datetime:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(IST)


def session_phase(
    now: datetime | None = None,
    *,
    open_hhmm: tuple[int, int] = (9, 15),
    flatten_hhmm: tuple[int, int] = (15, 15),
    close_hhmm: tuple[int, int] = (15, 30),
) -> str:
    """Return 'open', 'flatten', or 'closed'."""
    t = now_ist(now)
    if t.weekday() >= 5 or t.date() in NSE_CASH_HOLIDAYS_2026:
        return "closed"
    current = t.time()
    open_t = time(*open_hhmm)
    flatten_t = time(*flatten_hhmm)
    close_t = time(*close_hhmm)
    if current < open_t or current >= close_t:
        return "closed"
    if current >= flatten_t:
        return "flatten"
    return "open"


def minutes_since_open(now=None, open_hhmm: tuple[int, int] = (9, 15)) -> float | None:
    t = now_ist(now)
    if t.weekday() >= 5 or t.date() in NSE_CASH_HOLIDAYS_2026:
        return None
    opened = t.replace(hour=open_hhmm[0], minute=open_hhmm[1], second=0, microsecond=0)
    return (t - opened).total_seconds() / 60.0


def minutes_until_flatten(
    now=None, flatten_hhmm: tuple[int, int] = (15, 15),
) -> float | None:
    t = now_ist(now)
    if t.weekday() >= 5 or t.date() in NSE_CASH_HOLIDAYS_2026:
        return None
    flatten_t = t.replace(hour=flatten_hhmm[0], minute=flatten_hhmm[1], second=0, microsecond=0)
    return (flatten_t - t).total_seconds() / 60.0
