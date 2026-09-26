"""Numeric features from recent bars + the live quote."""
from __future__ import annotations

from dataclasses import dataclass

from .historical_data import Bar
from .regime import classify_regime, realized_vol


@dataclass(frozen=True)
class FeatureSet:
    ret_20: float
    vwap_dist_pct: float
    atr_pct: float
    realized_vol: float | None
    regime: str
    index_day_change_pct: float
    or_high: float | None = None
    or_low: float | None = None
    gap_pct: float | None = None


def _vwap(bars: list[Bar]) -> float | None:
    if not bars:
        return None
    pv = sum(b.close * max(b.volume, 1) for b in bars)
    vol = sum(max(b.volume, 1) for b in bars)
    if vol <= 0:
        return None
    return pv / vol


def _atr_pct(bars: list[Bar]) -> float:
    if len(bars) < 2:
        return 0.0
    ranges = []
    prev = bars[0].close
    for b in bars[1:]:
        tr = max(b.high - b.low, abs(b.high - prev), abs(b.low - prev))
        if b.close > 0:
            ranges.append(tr / b.close)
        prev = b.close
    return sum(ranges) / len(ranges) if ranges else 0.0


def compute_features(
    bars: list[Bar],
    index_day_change_pct: float = 0.0,
    or_high: float | None = None,
    or_low: float | None = None,
    gap_pct: float | None = None,
) -> FeatureSet:
    look = bars[-21:] if len(bars) >= 2 else bars
    ret = 0.0
    if len(look) >= 2 and look[0].close > 0:
        ret = (look[-1].close - look[0].close) / look[0].close
    vwap = _vwap(bars[-20:] if len(bars) >= 5 else bars)
    last = bars[-1].close if bars else 0.0
    dist = ((last - vwap) / vwap) if vwap and vwap > 0 else 0.0
    return FeatureSet(
        ret_20=ret,
        vwap_dist_pct=dist,
        atr_pct=_atr_pct(bars[-15:] if len(bars) > 2 else bars),
        realized_vol=realized_vol(bars[-20:] if len(bars) >= 3 else bars),
        regime=classify_regime(bars),
        index_day_change_pct=index_day_change_pct,
        or_high=or_high,
        or_low=or_low,
        gap_pct=gap_pct,
    )


def features_block(feat: FeatureSet) -> str:
    vol = "n/a" if feat.realized_vol is None else f"{feat.realized_vol:.4f}"
    return (
        f"Ret20: {feat.ret_20 * 100:.2f}%\n"
        f"VWAP distance: {feat.vwap_dist_pct * 100:.2f}%\n"
        f"ATR%: {feat.atr_pct * 100:.2f}%\n"
        f"Realized vol: {vol}\n"
        f"Regime: {feat.regime}\n"
        f"Index day change: {feat.index_day_change_pct:.2f}%\n"
        f"OR high: {feat.or_high if feat.or_high is not None else 'n/a'}\n"
        f"OR low: {feat.or_low if feat.or_low is not None else 'n/a'}\n"
        f"Gap vs prior close: {feat.gap_pct if feat.gap_pct is not None else 'n/a'}%"
    )
