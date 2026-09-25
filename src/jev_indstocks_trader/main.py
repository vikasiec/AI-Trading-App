"""Main trading loop.

Run this in a container/process that only READS the cached INDstocks
token (see auth.get_cached_token) -- it must NOT call
INDstocksAuth.refresh_session() itself. See scripts/refresh_token.py for
the single owner process that does that, on its own cron schedule.

Usage:
    python -m jev_indstocks_trader.main
"""
from __future__ import annotations

import logging
import time

from dotenv import load_dotenv

from . import auth
from .audit import AuditTrail
from .config import load_config
from .execution_gateway import ExecutionGateway
from .feature_prep import MarketSnapshot, build_context
from .instruments import InstrumentsMaster
from .jev_client import JevEvaluator
from .risk_governor import RiskGovernor
from .telegram_alerts import TelegramAlertNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def run_loop(poll_interval_s: float = 1.0) -> None:
    load_dotenv()
    cfg = load_config()

    def auth_headers_fn():
        return auth.auth_headers(cfg.indstocks)

    gateway = ExecutionGateway(cfg.indstocks, auth_headers_fn, tick_size=cfg.risk.tick_size_inr)
    governor = RiskGovernor(cfg.indstocks, cfg.risk, auth_headers_fn)
    evaluator = JevEvaluator(cfg.jev)
    instruments = InstrumentsMaster(cfg.indstocks, auth_headers_fn)
    audit = AuditTrail(cfg.audit_log_path)
    notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=governor.flatten_all)

    if cfg.risk.paper_trading:
        logger.warning("PAPER_TRADING=true -- signals will be scored and logged, NO orders will be sent.")

    logger.info("Trading loop starting. Ctrl+C to stop.")
    try:
        while True:
            _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier)
            time.sleep(poll_interval_s)
    except KeyboardInterrupt:
        logger.info("Shutdown requested, exiting cleanly.")


def _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier) -> None:
    """One iteration: fetch a snapshot, score it, and (maybe) trade.

    This is a stub wiring point -- plug in your real market-data and
    watchlist source instead of the placeholder snapshot below.
    """
    watchlist = ["RELIANCE"]  # TODO: replace with your real watchlist source

    for symbol in watchlist:
        try:
            instrument = instruments.resolve(symbol)
        except KeyError:
            logger.warning("Symbol %s not found in instruments master, skipping", symbol)
            continue

        scrip_code = instrument.get("scrip_code") or f"NSE_{instrument.get('security_id')}"
        ltp = gateway.get_ltp(scrip_code)

        snapshot = MarketSnapshot(symbol=symbol, ltp=ltp, day_change_pct=0.0, volume=0, headlines=[])
        context = build_context(snapshot)

        t0 = time.monotonic()
        result = evaluator.evaluate_signal(context)
        latency_ms = (time.monotonic() - t0) * 1000

        approved, reason = governor.validate_trade(
            security_id=instrument["security_id"],
            live_ltp=ltp,
            scored_at_price=ltp,
            conviction=result.score,
            confidence=result.confidence,
        )

        action = "SKIP"
        order_id = None
        if approved and not cfg.risk.paper_trading:
            funds = gateway.get_funds()
            equity = funds.get("sod_balance", 0.0)
            qty = governor.size_order(equity, ltp)
            if qty > 0:
                order = gateway.place_limit_order(instrument["security_id"], "BUY", qty, ltp)
                order_id = order.get("data", {}).get("order_id")
                action = "BUY"
                notifier.send_info(f"BUY {symbol} x{qty} @ ~{ltp} (order {order_id})")
        elif approved and cfg.risk.paper_trading:
            action = "BUY (paper)"

        audit.log_decision(
            security_id=instrument["security_id"],
            jev_conviction=result.score,
            jev_confidence=result.confidence,
            action=action,
            latency_ms=latency_ms,
            otr_check="PASS",
            order_id=order_id,
        )

        if not approved:
            logger.debug("Skipped %s: %s", symbol, reason)


if __name__ == "__main__":
    run_loop()
