#!/usr/bin/env python3
"""Single-owner INDstocks token refresh.

Schedule this via cron on ONE machine/process only. Run DAILY (not just
weekdays) so the token survives weekends — a Friday-only refresh expires
by Saturday ~09:00, causing heartbeat failures all weekend and Monday morning.

    # IST servers (daily 08:30, first in the pre-market chain)
    30 8 * * * /usr/bin/python3 /path/to/scripts/refresh_token.py

    # UTC servers (daily 03:00)
    0 3 * * * /usr/bin/python3 /path/to/scripts/refresh_token.py

Do NOT also call this from the main trading loop or the Telegram bot --
they should only read the cached token via jev_indstocks_trader.auth.get_cached_token().
Running this from more than one place will invalidate the token the
other processes are using.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from jev_indstocks_trader.auth import INDstocksAuth  # noqa: E402
from jev_indstocks_trader.config import load_config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

if __name__ == "__main__":
    load_dotenv()
    cfg = load_config()
    auth = INDstocksAuth(cfg.indstocks)
    auth.refresh_session()
    print("Token refreshed successfully.")
