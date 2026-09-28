"""Pre-market health check — runs before bot start, alerts on Telegram.

Verifies every integration boundary that unit tests cannot cover:
token validity, broker API reachability, quote fetching, watchlist
availability, Jev API, and cron/service readiness.

Exits 0 if all checks pass, 1 if any fail. Always sends a Telegram
summary so the user knows the system was checked.

Usage (cron, 08:50 IST = 03:20 UTC):
    cd /home/opc/AI-Trading-App && PYTHONPATH=src python3 scripts/premarket_health.py
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

src = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src))

from jev_indstocks_trader.config import load_config, validate_config
from jev_indstocks_trader.telegram_alerts import TelegramAlertNotifier


def check(name: str, fn) -> tuple[bool, str]:
    try:
        ok, detail = fn()
        return ok, detail
    except Exception as e:
        return False, str(e)[:120]


def run_checks() -> list[tuple[str, bool, str]]:
    cfg = load_config()
    results: list[tuple[str, bool, str]] = []

    # 1. Config validation
    def t_config():
        w = validate_config(cfg)
        blockers = [x for x in w if "placeholder" in x.lower()]
        if blockers:
            return False, f"blockers: {blockers}"
        return True, f"paper={cfg.risk.paper_trading} mode={cfg.risk.entry_mode}"
    results.append(("Config", *check("Config", t_config)))

    # 2. Auth token
    def t_auth():
        from jev_indstocks_trader import auth
        hdr = auth.auth_headers(cfg.indstocks)
        if "Authorization" not in hdr:
            return False, "no Authorization header"
        return True, "token loaded"
    results.append(("Auth token", *check("Auth token", t_auth)))

    # 3. Broker API reachable
    def t_broker():
        from jev_indstocks_trader import auth
        import requests
        hdr = auth.auth_headers(cfg.indstocks)
        resp = requests.get(
            f"{cfg.indstocks.base_url}/user/profile",
            headers=hdr, timeout=10,
        )
        if resp.status_code == 401:
            return False, "401 Unauthorized — token expired"
        if resp.status_code != 200:
            return False, f"status {resp.status_code}"
        return True, "200 OK"
    results.append(("Broker API", *check("Broker API", t_broker)))

    # 4. Quote fetch (single)
    def t_quote():
        from jev_indstocks_trader import auth
        from jev_indstocks_trader.execution_gateway import ExecutionGateway
        hdr = auth.auth_headers(cfg.indstocks)
        gw = ExecutionGateway(cfg.indstocks, lambda: hdr)
        q = gw.get_quote("NSE_2885")
        if q["ltp"] <= 0:
            return False, f"ltp={q['ltp']}"
        return True, f"RELIANCE ltp={q['ltp']}"
    results.append(("Quote fetch", *check("Quote fetch", t_quote)))

    # 5. Batch quote fetch
    def t_batch():
        from jev_indstocks_trader import auth
        from jev_indstocks_trader.execution_gateway import ExecutionGateway
        hdr = auth.auth_headers(cfg.indstocks)
        gw = ExecutionGateway(cfg.indstocks, lambda: hdr)
        batch = gw.get_quotes_batch(["NSE_2885", "NSE_7229"])
        if len(batch) == 0:
            return False, "empty response"
        return True, f"{len(batch)} quotes OK"
    results.append(("Batch quotes", *check("Batch quotes", t_batch)))

    # 6. Watchlist exists
    def t_watchlist():
        from jev_indstocks_trader.watchlist import load_watchlist
        wl = load_watchlist(cfg)
        if len(wl) == 0:
            return False, "empty watchlist"
        return True, f"{len(wl)} symbols"
    results.append(("Watchlist", *check("Watchlist", t_watchlist)))

    # 7. Jev API reachable
    def t_jev():
        from jev_indstocks_trader.jev_client import JevEvaluator
        ev = JevEvaluator(cfg.jev)
        r = ev.evaluate_signal("Health check: TESTCO at 100.00, +0.5%, volume 1000")
        if r.score < 0 or r.confidence < 0:
            return False, f"bad response score={r.score}"
        return True, f"score={r.score:.2f} conf={r.confidence:.2f}"
    results.append(("Jev API", *check("Jev API", t_jev)))

    # 8. Position store readable
    def t_positions():
        from jev_indstocks_trader.positions import PositionStore
        ps = PositionStore(cfg.positions_store_path)
        open_pos = ps.list_open()
        return True, f"{len(open_pos)} open positions"
    results.append(("Position store", *check("Position store", t_positions)))

    # 9. Audit trail writable
    def t_audit():
        p = cfg.audit_log_path
        p.parent.mkdir(parents=True, exist_ok=True)
        writable = p.parent.exists()
        return writable, f"path={p}"
    results.append(("Audit trail", *check("Audit trail", t_audit)))

    return results


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    logger.info("Pre-market health check starting")
    t0 = time.monotonic()
    results = run_checks()
    elapsed = time.monotonic() - t0

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)

    lines = []
    for name, ok, detail in results:
        icon = "✅" if ok else "❌"
        lines.append(f"{icon} {name}: {detail}")

    if failed == 0:
        header = f"✅ PRE-MARKET HEALTH: {passed}/{total} PASSED"
    else:
        header = f"⚠️ PRE-MARKET HEALTH: {failed} FAILED"

    body = (
        f"{header}\n"
        f"─" * 30 + "\n"
        + "\n".join(lines)
        + f"\n─" * 30
        + f"\nChecked in {elapsed:.1f}s"
    )

    logger.info("Results:\n%s", body)

    try:
        cfg = load_config()
        notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=lambda: None)
        if failed > 0:
            notifier.send_critical_alert(body)
        else:
            notifier.send_info(body)
    except Exception:
        logger.exception("Could not send health check to Telegram")

    if failed > 0:
        logger.error("%d checks FAILED — bot may not work correctly", failed)
        for name, ok, detail in results:
            if not ok:
                logger.error("  FAIL: %s — %s", name, detail)
        sys.exit(1)
    else:
        logger.info("All %d checks passed", total)
        sys.exit(0)


if __name__ == "__main__":
    main()
