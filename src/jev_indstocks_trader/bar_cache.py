"""In-process tick → coarse bars for rules and Jev context.

Not a substitute for real OHLCV. Each accepted LTP updates the current
minute bucket so H4/H5-style votes have something to chew on in paper.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone

from .historical_data import Bar
from .session import IST

_OR_START = time(9, 15)
_OR_END = time(9, 25)


@dataclass
class _VwapState:
    sum_cv: float = 0.0
    sum_v: float = 0.0
    started: bool = False

    @property
    def vwap(self) -> float | None:
        if self.sum_v == 0.0:
            return None
        return self.sum_cv / self.sum_v


@dataclass
class _OrState:
    low: float | None = None
    finalized: bool = False
    bar_count: int = 0


class BarCache:
    def __init__(self, max_bars: int = 80):
        self.max_bars = max_bars
        self._bars: dict[str, list[Bar]] = {}
        self._last_cum_vol: dict[str, int] = {}
        self._day: str | None = None
        self._vwap: dict[str, _VwapState] = {}
        self._or: dict[str, _OrState] = {}

    def roll_day(self, day: str) -> None:
        """Drop yesterday's minute bars so the open is not scored on them."""
        if self._day == day:
            return
        self._day = day
        self._bars.clear()
        self._last_cum_vol.clear()
        self._vwap.clear()
        self._or.clear()

    def _fold_completed_bar(self, key: str, bar: Bar, next_bar_ts: datetime | None = None) -> None:
        """Fold a just-completed bar into VWAP and OR-low accumulators."""
        try:
            bar_ist = bar.timestamp.astimezone(IST)
        except Exception:
            return
        bar_time = bar_ist.time()

        if bar_time >= _OR_START:
            vs = self._vwap.setdefault(key, _VwapState())
            vs.started = True
            w = max(bar.volume, 1)
            vs.sum_cv += bar.close * w
            vs.sum_v += w

        if bar_time >= _OR_START and bar_time < _OR_END:
            ors = self._or.setdefault(key, _OrState())
            if not ors.finalized:
                if ors.low is None:
                    ors.low = bar.low
                else:
                    ors.low = min(ors.low, bar.low)
                ors.bar_count += 1

        if next_bar_ts is not None:
            try:
                next_ist = next_bar_ts.astimezone(IST).time()
            except Exception:
                next_ist = None
            if next_ist is not None and next_ist >= _OR_END:
                ors = self._or.setdefault(key, _OrState())
                if not ors.finalized:
                    ors.finalized = True

    def update(self, symbol: str, ltp: float, volume: int = 0, ts: datetime | None = None) -> None:
        if ltp <= 0:
            return
        ts = ts or datetime.now(timezone.utc)
        minute = ts.replace(second=0, microsecond=0)
        key = symbol.upper()
        prev_cum = self._last_cum_vol.get(key, 0)
        delta = max(0, volume - prev_cum) if volume >= prev_cum else max(0, volume)
        self._last_cum_vol[key] = max(volume, prev_cum)
        series = self._bars.setdefault(key, [])
        if series and series[-1].timestamp == minute:
            last = series[-1]
            series[-1] = Bar(
                timestamp=minute,
                open=last.open,
                high=max(last.high, ltp),
                low=min(last.low, ltp),
                close=ltp,
                volume=last.volume + delta,
            )
        else:
            if series:
                self._fold_completed_bar(key, series[-1], next_bar_ts=minute)
            series.append(Bar(timestamp=minute, open=ltp, high=ltp, low=ltp, close=ltp, volume=delta))
            if len(series) > self.max_bars:
                del series[: len(series) - self.max_bars]

    def bars(self, symbol: str) -> list[Bar]:
        return list(self._bars.get(symbol.upper(), []))

    def session_vwap(self, symbol: str) -> float | None:
        """Running VWAP from completed bars only (close-weighted)."""
        vs = self._vwap.get(symbol.upper())
        if vs is None or not vs.started:
            return None
        return vs.vwap

    def or_low(self, symbol: str) -> float | None:
        """Opening range low from completed 09:15-09:24 bars. None until finalized."""
        ors = self._or.get(symbol.upper())
        if ors is None or not ors.finalized:
            return None
        return ors.low

    def last_completed_close(self, symbol: str) -> float | None:
        """Close of the last completed bar (second-to-last in the list)."""
        series = self._bars.get(symbol.upper())
        if series is None or len(series) < 2:
            return None
        return series[-2].close
