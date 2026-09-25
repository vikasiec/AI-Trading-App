#!/usr/bin/env python3
"""Run the Jev calibration report against a real audit trail.

Usage:
    python scripts/run_calibration_report.py [path/to/audit_trail.jsonl]

Defaults to AUDIT_LOG_PATH from .env if not given a path argument.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from jev_indstocks_trader.calibration import run_calibration_report  # noqa: E402
from jev_indstocks_trader.config import load_config  # noqa: E402

if __name__ == "__main__":
    load_dotenv()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        path = load_config().audit_log_path

    report = run_calibration_report(path)
    print(report.summary_text())
