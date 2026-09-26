from importlib.machinery import SourceFileLoader
from pathlib import Path


def test_collect_dir_and_files(tmp_path):
    (tmp_path / "RELIANCE.csv").write_text("timestamp,open,high,low,close,volume\n")
    (tmp_path / "TCS.csv").write_text("timestamp,open,high,low,close,volume\n")
    mod = SourceFileLoader(
        "rwb",
        str(Path(__file__).resolve().parent.parent / "scripts" / "run_watchlist_backtest.py"),
    ).load_module()
    names = sorted(p.name for p in mod._collect([str(tmp_path)]))
    assert names == ["RELIANCE.csv", "TCS.csv"]
