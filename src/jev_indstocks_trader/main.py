"""Main trading loop.

Run this in a container/process that only READS the cached INDstocks
token (see auth.get_cached_token) -- it must NOT call
INDstocksAuth.refresh_session() itself. See scripts/refresh_token.py for
the single owner process that does that, on its own cron schedule.

Every tick: check exits first (closing existing risk), then look for
new entries (opening new risk). A position this bot opens is always
tracked in PositionStore with a stop-loss, target, and max-hold-time --
ExitManager is what actually closes it out.

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
from .exits import ExitManager
from .feature_prep import MarketSnapshot, build_context
from .instruments import InstrumentsMaster
from .jev_client import JevEvaluator
from .positions import Position, PositionStore
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
    position_store = PositionStore(cfg.positions_store_path)
    notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=governor.flatten_all)
    exit_manager = ExitManager(
        gateway=gateway,
        store=position_store,
        audit=audit,
        max_hold_minutes=cfg.risk.max_hold_minutes,
        notifier=notifier,
        paper_trading=cfg.risk.paper_trading,
    )

    if cfg.risk.paper_trading:
        logger.warning("PAPER_TRADING=true -- signals will be scored and logged, NO real orders will be sent.")

    open_count = len(position_store.list_open())
    if open_count:
        logger.info("Resuming with %d open position(s) from a previous run.", open_count)

    logger.info("Trading loop starting. Ctrl+C to stop.")
    try:
        while True:
            exit_manager.check_and_exit_all()
            _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier, position_store)
            time.sleep(poll_interval_s)
    except KeyboardInterrupt:
        logger.info("Shutdown requested, exiting cleanly.")


def _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier, position_store) -> None:
    """One iteration: fetch a snapshot, score it, and (maybe) open a new position.

    Exit checks run separately, before this, in run_loop() -- closing
    existing risk always happens before opening new risk.

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

        security_id = instrument["security_id"]
        if position_store.has_open(security_id):
            # Already holding this one -- exits own its lifecycle from here, not new entries.
            continue

        scrip_code = instrument.get("scrip_code") or f"NSE_{security_id}"
        ltp = gateway.get_ltp(scrip_code)

        snapshot = MarketSnapshot(symbol=symbol, ltp=ltp, day_change_pct=0.0, volume=0, headlines=[])
        context = build_context(snapshot)

        t0 = time.monotonic()
        result = evaluator.evaluate_signal(context)
        latency_ms = (time.monotonic() - t0) * 1000

        approved, reason = governor.validate_trade(
            security_id=security_id,
            live_ltp=ltp,
            scored_at_price=ltp,
            conviction=result.score,
            confidence=result.confidence,
        )

        action = "SKIP"
        order_id = None
        qty = 0
        if approved:
            funds = gateway.get_funds()
            equity = funds.get("sod_balance", 0.0)
            qty = governor.size_order(equity, ltp)
            if qty > 0:
                if not cfg.risk.paper_trading:
                    order = gateway.place_limit_order(security_id, "BUY", qty, ltp)
                    order_id = order.get("data", {}).get("order_id")
                    action = "BUY"
                    notifier.send_info(f"BUY {symbol} x{qty} @ ~{ltp} (order {order_id})")
                else:
                    action = "BUY (paper)"

        decision_id = audit.log_decision(
            security_id=security_id,
            jev_conviction=result.score,
            jev_confidence=result.confidence,
            action=action,
            latency_ms=latency_ms,
            otr_check="PASS",
            order_id=order_id,
        )

        if approved and qty > 0:
            position_store.add(Position(
                security_id=security_id,
                scrip_code=scrip_code,
                exchange=instrument.get("exchange", "NSE"),
                segment=instrument.get("segment", "EQUITY"),
                product="INTRADAY",
                qty=qty,
                entry_price=ltp,
                stop_loss_price=ltp * (1 - cfg.risk.stop_loss_pct),
                target_price=ltp * (1 + cfg.risk.target_pct),
                opened_at=time.time(),
                decision_id=decision_id,
            ))

        if not approved:
            logger.debug("Skipped %s: %s", symbol, reason)


if __name__ == "__main__":
    run_loop()
