"""In-process tick → coarse bars for rules and Jev context.

Not a substitute for real OHLCV. Each accepted LTP updates the current
minute bucket so H4/H5-style votes have something to chew on in paper.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .historical_data import Bar


class BarCache:
    def __init__(self, max_bars: int = 80):
        self.max_bars = max_bars
        self._bars: dict[str, list[Bar]] = {}

    def update(self, symbol: str, ltp: float, volume: int = 0, ts: datetime | None = None) -> None:
        if ltp <= 0:
            return
        ts = ts or datetime.now(timezone.utc)
        minute = ts.replace(second=0, microsecond=0)
        series = self._bars.setdefault(symbol.upper(), [])
        if series and series[-1].timestamp == minute:
            last = series[-1]
            series[-1] = Bar(
                timestamp=minute,
                open=last.open,
                high=max(last.high, ltp),
                low=min(last.low, ltp),
                close=ltp,
                volume=last.volume + max(0, volume),
            )
        else:
            series.append(Bar(timestamp=minute, open=ltp, high=ltp, low=ltp, close=ltp, volume=max(0, volume)))
            if len(series) > self.max_bars:
                del series[: len(series) - self.max_bars]

    def bars(self, symbol: str) -> list[Bar]:
        return list(self._bars.get(symbol.upper(), []))
