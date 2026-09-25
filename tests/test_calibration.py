import json

from jev_indstocks_trader.calibration import load_closed_trades, run_calibration_report


def _write_jsonl(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_empty_log_returns_empty_report(tmp_path):
    path = tmp_path / "audit.jsonl"
    report = run_calibration_report(path)
    assert report.total_decisions == 0
    assert report.closed_trades == 0


def test_missing_file_does_not_raise(tmp_path):
    path = tmp_path / "does_not_exist.jsonl"
    report = run_calibration_report(path)
    assert report.total_decisions == 0
    assert report.closed_trades == 0


def test_open_decision_with_no_outcome_excluded(tmp_path):
    path = tmp_path / "audit.jsonl"
    _write_jsonl(path, [
        {"decision_id": "A_1", "security_id": "A", "jev_conviction": 0.85,
         "jev_confidence": 0.9, "action": "BUY", "latency_ms": 100},
    ])
    trades = load_closed_trades(path)
    assert trades == []


def test_closed_trade_joined_to_outcome(tmp_path):
    path = tmp_path / "audit.jsonl"
    _write_jsonl(path, [
        {"decision_id": "A_1", "security_id": "A", "jev_conviction": 0.85,
         "jev_confidence": 0.9, "action": "BUY", "latency_ms": 100},
        {"decision_id": "A_1", "type": "outcome_update", "fill_price": 105.0,
         "realized_pnl": 500.0, "net_pnl": 480.0, "costs": {}},
    ])
    trades = load_closed_trades(path)
    assert len(trades) == 1
    assert trades[0].jev_conviction == 0.85
    assert trades[0].net_pnl == 480.0


def test_bucketing_groups_by_conviction_range(tmp_path):
    path = tmp_path / "audit.jsonl"
    records = []
    for i, (conviction, pnl) in enumerate([
        (0.55, 10.0), (0.58, -5.0),   # 0.5-0.6 bucket
        (0.85, 100.0), (0.88, 200.0),  # 0.8-0.9 bucket
    ]):
        did = f"SEC{i}_1"
        records.append({"decision_id": did, "security_id": f"SEC{i}", "jev_conviction": conviction,
                         "jev_confidence": 0.7, "action": "BUY", "latency_ms": 100})
        records.append({"decision_id": did, "type": "outcome_update", "fill_price": 100.0,
                         "realized_pnl": pnl, "net_pnl": pnl, "costs": {}})
    _write_jsonl(path, records)

    report = run_calibration_report(path)
    assert report.closed_trades == 4

    bucket_55 = next(b for b in report.conviction_buckets if b.bucket_label == "0.5-0.6")
    assert bucket_55.n == 2
    assert bucket_55.mean_net_pnl == 2.5  # (10 + -5) / 2

    bucket_85 = next(b for b in report.conviction_buckets if b.bucket_label == "0.8-0.9")
    assert bucket_85.n == 2
    assert bucket_85.mean_net_pnl == 150.0
    assert bucket_85.win_rate == 1.0


def test_win_rate_computed_correctly(tmp_path):
    path = tmp_path / "audit.jsonl"
    records = []
    for i, pnl in enumerate([100.0, -50.0, 30.0]):
        did = f"SEC{i}_1"
        records.append({"decision_id": did, "security_id": f"SEC{i}", "jev_conviction": 0.65,
                         "jev_confidence": 0.7, "action": "BUY", "latency_ms": 100})
        records.append({"decision_id": did, "type": "outcome_update", "fill_price": 100.0,
                         "realized_pnl": pnl, "net_pnl": pnl, "costs": {}})
    _write_jsonl(path, records)

    report = run_calibration_report(path)
    bucket = next(b for b in report.conviction_buckets if b.bucket_label == "0.6-0.7")
    assert bucket.n == 3
    assert round(bucket.win_rate, 4) == round(2 / 3, 4)


def test_small_sample_flagged(tmp_path):
    path = tmp_path / "audit.jsonl"
    _write_jsonl(path, [
        {"decision_id": "A_1", "security_id": "A", "jev_conviction": 0.85,
         "jev_confidence": 0.9, "action": "BUY", "latency_ms": 100},
        {"decision_id": "A_1", "type": "outcome_update", "fill_price": 105.0,
         "realized_pnl": 50.0, "net_pnl": 40.0, "costs": {}},
    ])
    report = run_calibration_report(path)
    assert "SAMPLE TOO SMALL" in report.summary_text() or "too small" in report.summary_text().lower()


def test_summary_text_does_not_raise_on_empty(tmp_path):
    path = tmp_path / "audit.jsonl"
    report = run_calibration_report(path)
    text = report.summary_text()
    assert "0 closed trades" in text.lower() or "closed trades: 0" in text.lower() or "closed trades" in text.lower()


def test_news_comparison_splits_by_had_news(tmp_path):
    path = tmp_path / "audit.jsonl"
    records = []
    for i, (had_news, pnl) in enumerate([
        (True, 50.0), (True, 70.0),
        (False, -10.0), (False, 5.0),
        (None, 20.0),
    ]):
        did = f"SEC{i}_1"
        records.append({"decision_id": did, "security_id": f"SEC{i}", "jev_conviction": 0.7,
                         "jev_confidence": 0.7, "action": "BUY", "latency_ms": 100, "had_news": had_news})
        records.append({"decision_id": did, "type": "outcome_update", "fill_price": 100.0,
                         "realized_pnl": pnl, "net_pnl": pnl, "costs": {}})
    _write_jsonl(path, records)

    report = run_calibration_report(path)
    assert report.news_comparison is not None
    assert report.news_comparison.with_news.n == 2
    assert report.news_comparison.without_news.n == 2
    assert report.news_comparison.unknown.n == 1
    assert report.news_comparison.with_news.mean_net_pnl == 60.0
    assert "H3" in report.summary_text()
