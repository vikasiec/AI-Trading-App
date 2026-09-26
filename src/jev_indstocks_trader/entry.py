"""Entry permission: Jev vote, rule vote, or both.

Rules are conservative on purpose. Too few bars → no rule yes.
"""
from __future__ import annotations

from dataclasses import dataclass

from .features import FeatureSet
from .historical_data import Bar
from .strategies import mean_reversion_long, momentum_long


@dataclass(frozen=True)
class RuleVote:
    allow: bool
    reason: str


def rule_vote(bars: list[Bar], feat: FeatureSet) -> RuleVote:
    if len(bars) < 8:
        return RuleVote(False, "too_few_bars")

    last = bars[-1].close if bars else 0.0
    if feat.or_high and last >= feat.or_high and feat.regime != "calm":
        return RuleVote(True, "orb_hold")

    if feat.regime == "violent":
        if momentum_long(lookback=5, min_return_pct=0.004, volume_multiple=1.1)(bars):
            return RuleVote(True, "momentum_violent")
        return RuleVote(False, "violent_no_momentum")

    if feat.regime == "calm":
        if mean_reversion_long(lookback=min(20, len(bars)), deviation_pct=0.008)(bars):
            return RuleVote(True, "mean_revert_calm")
        return RuleVote(False, "calm_no_dip")

    # normal: either rule may fire
    if momentum_long(lookback=5, min_return_pct=0.006, volume_multiple=1.15)(bars):
        return RuleVote(True, "momentum_normal")
    if mean_reversion_long(lookback=min(20, len(bars)), deviation_pct=0.012)(bars):
        return RuleVote(True, "mean_revert_normal")
    return RuleVote(False, "no_rule")


def index_veto(index_day_change_pct: float | None, threshold_pct: float) -> RuleVote:
    """V7: block new longs when the index is down more than threshold (e.g. -0.8)."""
    if index_day_change_pct is None:
        return RuleVote(True, "index_unknown")
    if index_day_change_pct <= threshold_pct:
        return RuleVote(False, f"index_dump_{index_day_change_pct:.2f}")
    return RuleVote(True, "index_ok")


def combine_votes(mode: str, jev_ok: bool, rule: RuleVote) -> tuple[bool, str]:
    mode = (mode or "jev").lower()
    if mode == "rule":
        return rule.allow, rule.reason if rule.allow else f"rule_{rule.reason}"
    if mode == "jev_and_rule":
        if not rule.allow:
            return False, f"rule_{rule.reason}"
        if not jev_ok:
            return False, "jev_below_threshold"
        return True, f"jev_and_{rule.reason}"
    # jev only
    if not jev_ok:
        return False, "jev_below_threshold"
    return True, "jev_ok"
