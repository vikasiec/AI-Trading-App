"""F5: first N minutes high/low per symbol per session date."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OpeningRange:
    high: float
    low: float
    samples: int


class OpeningRangeBook:
    def __init__(self):
        self._day: str | None = None
        self._ranges: dict[str, OpeningRange] = {}

    def roll_day(self, day: str) -> None:
        if day != self._day:
            self._day = day
            self._ranges.clear()

    def update(self, symbol: str, ltp: float) -> None:
        if ltp <= 0:
            return
        key = symbol.upper()
        cur = self._ranges.get(key)
        if cur is None:
            self._ranges[key] = OpeningRange(high=ltp, low=ltp, samples=1)
        else:
            cur.high = max(cur.high, ltp)
            cur.low = min(cur.low, ltp)
            cur.samples += 1

    def get(self, symbol: str) -> OpeningRange | None:
        return self._ranges.get(symbol.upper())
