from datetime import datetime, timezone

from jev_indstocks_trader.entry import combine_votes, rule_vote
from jev_indstocks_trader.features import compute_features
from jev_indstocks_trader.historical_data import Bar


def _bars(closes, vols=None):
    vols = vols or [1000] * len(closes)
    out = []
    for i, c in enumerate(closes):
        out.append(Bar(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            open=c, high=c, low=c, close=c, volume=vols[i],
        ))
    return out


def test_combine_jev_and_rule():
    from jev_indstocks_trader.entry import RuleVote
    yes = RuleVote(True, "momentum_normal")
    no = RuleVote(False, "no_rule")
    assert combine_votes("jev", True, no)[0] is True
    assert combine_votes("jev_and_rule", True, no)[0] is False
    assert combine_votes("jev_and_rule", True, yes)[0] is True
    assert combine_votes("rule", False, yes)[0] is True
    assert combine_votes("rule", True, no)[0] is False


def test_rule_too_few_bars():
    bars = _bars([100, 101])
    feat = compute_features(bars)
    assert rule_vote(bars, feat).allow is False


def test_features_block_includes_regime():
    from jev_indstocks_trader.features import features_block
    feat = compute_features(_bars([100 + i * 0.5 for i in range(25)]))
    text = features_block(feat)
    assert "Regime:" in text
    assert "VWAP" in text
