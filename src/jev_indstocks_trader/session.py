"""NSE cash session clock (IST).

Entries only in 09:15–15:15 IST on weekdays. 15:15–15:30 is flatten-only
so INTRADAY square-off is ours, not the broker's 15:20–15:30 scramble.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


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
    if t.weekday() >= 5:
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
    if t.weekday() >= 5:
        return None
    opened = t.replace(hour=open_hhmm[0], minute=open_hhmm[1], second=0, microsecond=0)
    return (t - opened).total_seconds() / 60.0
