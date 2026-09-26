"""F8: session gap vs prior close, inferred from first day_change_pct print."""
from __future__ import annotations


class GapBook:
    def __init__(self):
        self._day: str | None = None
        self._gap: dict[str, float] = {}

    def roll_day(self, day: str) -> None:
        if day != self._day:
            self._day = day
            self._gap.clear()

    def observe(self, symbol: str, day_change_pct: float | None) -> None:
        if day_change_pct is None:
            return
        key = symbol.upper()
        if key not in self._gap:
            self._gap[key] = float(day_change_pct)

    def get(self, symbol: str) -> float | None:
        return self._gap.get(symbol.upper())
