"""Main trading loop.

Run this in a container/process that only READS the cached INDstocks
token (see auth.get_cached_token) -- it must NOT call
INDstocksAuth.refresh_session() itself. See scripts/refresh_token.py for
the single owner process that does that, on its own cron schedule.

Every tick: check exits first (closing existing risk), then look for
new entries (opening new risk) across the configured watchlist. LTP
comes from the WebSocket feed when it has a fresh tick, and falls back
to the REST poll otherwise -- see market_data.py for why the feed is
gated behind WEBSOCKET_ENABLED.

Usage:
    python -m jev_indstocks_trader.main
"""
from __future__ import annotations

import logging
import time

from dotenv import load_dotenv

from . import auth
from .audit import AuditTrail
from .config import load_config, validate_config
from .costs import CostRates
from .execution_gateway import ExecutionGateway
from .exits import ExitManager
from .feature_prep import MarketSnapshot, build_context
from . import gtt_orders
from .instruments import InstrumentsMaster
from .jev_client import JevEvaluator
from .market_data import INDstocksWebSocketFeed, LiveTickCache
from .news_feed import NewsSource
from .portfolio_risk import check_portfolio_risk
from .positions import Position, PositionStore
from .reconciliation import ReconciliationService
from .retry import retry_with_backoff
from .risk_governor import RiskGovernor
from .telegram_alerts import TelegramAlertNotifier
from .watchlist import load_watchlist

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def run_loop(poll_interval_s: float = 1.0) -> None:
    load_dotenv()
    cfg = load_config()
    for warning in validate_config(cfg):
        logger.warning("config: %s", warning)

    def auth_headers_fn():
        return auth.auth_headers(cfg.indstocks)

    gateway = ExecutionGateway(cfg.indstocks, auth_headers_fn, tick_size=cfg.risk.tick_size_inr)
    governor = RiskGovernor(cfg.indstocks, cfg.risk, auth_headers_fn)
    evaluator = JevEvaluator(cfg.jev)
    instruments = InstrumentsMaster(cfg.indstocks, auth_headers_fn)
    audit = AuditTrail(cfg.audit_log_path)
    position_store = PositionStore(cfg.positions_store_path)
    def _kill_switch() -> None:
        """Broker flatten + cancel local GTTs + clear the local store."""
        if not cfg.risk.paper_trading:
            governor.flatten_all()
        for pos in list(position_store.list_open()):
            if pos.gtt_id and not cfg.risk.paper_trading:
                try:
                    gtt_orders.cancel_gtt(cfg.indstocks, auth_headers_fn, pos.gtt_id)
                except Exception:
                    logger.exception("Kill switch could not cancel GTT %s", pos.gtt_id)
            position_store.remove(pos.security_id)
        try:
            notifier.send_critical_alert("Kill switch completed: flatten + local store cleared")
        except Exception:
            logger.exception("Could not send kill-switch confirmation")

    notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=_kill_switch)
    notifier.start_background_polling()
    exit_manager = ExitManager(
        gateway=gateway,
        store=position_store,
        audit=audit,
        max_hold_minutes=cfg.risk.max_hold_minutes,
        notifier=notifier,
        paper_trading=cfg.risk.paper_trading,
        cost_rates=CostRates(brokerage_per_order=cfg.risk.brokerage_per_order_inr),
        indstocks_cfg=cfg.indstocks,
        auth_headers_fn=auth_headers_fn,
    )
    reconciler = ReconciliationService(gateway, position_store, interval_s=60.0, notifier=notifier)

    watchlist = load_watchlist(cfg)
    logger.info("Watchlist: %s", watchlist)

    news_urls = [u.strip() for u in cfg.news_rss_feeds.split(",") if u.strip()]
    news_source = NewsSource(news_urls, cache_ttl_s=cfg.news_cache_ttl_s) if news_urls else None
    if news_source is None:
        logger.warning("No NEWS_RSS_FEEDS configured -- Jev will score on price data alone")

    tick_cache = LiveTickCache()
    ws_feed = None
    if cfg.websocket_enabled:
        try:
            resolved = [instruments.resolve(sym) for sym in watchlist]
            scrip_codes = [r.get("scrip_code") or f"NSE_{r['security_id']}" for r in resolved]
            ws_feed = INDstocksWebSocketFeed(cfg.websocket_url, auth_headers_fn, scrip_codes, tick_cache)
            ws_feed.start()
        except Exception:
            logger.exception("Could not start WebSocket feed -- falling back to REST polling for all symbols")
    else:
        logger.info("WEBSOCKET_ENABLED=false -- using REST polling for LTP")

    if cfg.risk.paper_trading:
        logger.warning("PAPER_TRADING=true -- signals will be scored and logged, NO real orders will be sent.")

    open_count = len(position_store.list_open())
    if open_count:
        logger.info("Resuming with %d open position(s) from a previous run.", open_count)

    logger.info("Trading loop starting. Ctrl+C to stop.")
    try:
        while True:
            if not cfg.risk.paper_trading and reconciler.due():
                reconciler.reconcile()
            exit_manager.check_and_exit_all()
            _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
                  position_store, watchlist, news_source, tick_cache)
            time.sleep(poll_interval_s)
    except KeyboardInterrupt:
        logger.info("Shutdown requested, exiting cleanly.")
    finally:
        if ws_feed is not None:
            ws_feed.stop()


def _get_quote(gateway: ExecutionGateway, tick_cache: LiveTickCache, scrip_code: str) -> dict:
    """WebSocket tick if fresh, else a retried REST quote (ltp/change/vol)."""
    live = tick_cache.get_fresh(scrip_code)
    if live is not None:
        return {"ltp": live, "day_change_pct": 0.0, "volume": 0}
    return retry_with_backoff(
        lambda: gateway.get_quote(scrip_code), max_retries=3, base_delay_s=0.5,
    )


def _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          position_store, watchlist, news_source, tick_cache) -> None:
    """One iteration: fetch a snapshot per watchlist symbol, score it, and
    (maybe) open a new position. Exit checks run separately, before this,
    in run_loop() -- closing existing risk always happens before opening
    new risk.
    """
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

        try:
            quote = _get_quote(gateway, tick_cache, scrip_code)
        except Exception:
            logger.exception("Could not get quote for %s after retries, skipping this tick", symbol)
            continue

        scored_at_price = quote["ltp"]
        headlines = news_source.headlines_for(symbol, instrument.get("name")) if news_source else []
        snapshot = MarketSnapshot(
            symbol=symbol,
            ltp=scored_at_price,
            day_change_pct=quote.get("day_change_pct") or 0.0,
            volume=quote.get("volume") or 0,
            headlines=headlines,
        )
        context = build_context(snapshot)

        t0 = time.monotonic()
        try:
            result = retry_with_backoff(lambda: evaluator.evaluate_signal(context), max_retries=2, base_delay_s=0.5)
        except Exception:
            logger.exception("Jev scoring failed for %s after retries, skipping this tick", symbol)
            continue
        latency_ms = (time.monotonic() - t0) * 1000

        try:
            live_quote = _get_quote(gateway, tick_cache, scrip_code)
            live_ltp = live_quote["ltp"]
        except Exception:
            logger.exception("Could not refresh LTP for %s after Jev, skipping", symbol)
            continue

        approved, reason = governor.validate_trade(
            security_id=security_id,
            live_ltp=live_ltp,
            scored_at_price=scored_at_price,
            conviction=result.score,
            confidence=result.confidence,
        )

        action = "SKIP"
        order_id = None
        qty = 0
        if approved:
            funds = gateway.get_funds()
            equity = gateway.equity_from_funds(funds)
            qty = governor.size_order(equity, live_ltp)
            if qty > 0:
                portfolio_check = check_portfolio_risk(
                    position_store, cfg.risk, equity=equity,
                    candidate_capital=qty * live_ltp, candidate_symbol=symbol,
                )
                if not portfolio_check.approved:
                    logger.debug("Portfolio risk blocked %s: %s", symbol, portfolio_check.reason)
                    qty = 0
                    reason = portfolio_check.reason
                    approved = False
                elif not cfg.risk.paper_trading:
                    try:
                        order = gateway.place_limit_order(security_id, "BUY", qty, live_ltp)
                        order_id = order.get("data", {}).get("order_id")
                        action = "BUY"
                        governor.mark_executed(security_id)
                        notifier.send_info(f"BUY {symbol} x{qty} @ ~{live_ltp} (order {order_id})")
                    except Exception:
                        logger.exception("Order placement failed for %s -- not marking executed", symbol)
                        approved = False
                        qty = 0
                        action = "SKIP"
                        reason = "order_placement_failed"
                else:
                    action = "BUY (paper)"
                    governor.mark_executed(security_id)

        decision_id = audit.log_decision(
            security_id=security_id,
            jev_conviction=result.score,
            jev_confidence=result.confidence,
            action=action,
            latency_ms=latency_ms,
            otr_check="PASS" if approved else "SKIP",
            order_id=order_id,
            had_news=(len(headlines) > 0) if news_source is not None else None,
        )

        if approved and qty > 0:
            stop_loss_price = live_ltp * (1 - cfg.risk.stop_loss_pct)
            target_price = live_ltp * (1 + cfg.risk.target_pct)

            gtt_id = None
            if cfg.risk.gtt_enabled and not cfg.risk.paper_trading:
                try:
                    gtt_id = gtt_orders.place_gtt_oco(
                        cfg.indstocks, gateway.auth_headers_fn,
                        security_id=security_id, exchange=instrument.get("exchange", "NSE"),
                        segment=instrument.get("segment", "EQUITY"), qty=qty,
                        stop_loss_trigger=stop_loss_price, target_trigger=target_price,
                        product="INTRADAY",
                    )
                except Exception:
                    logger.exception(
                        "GTT placement failed for %s -- position is still protected by the "
                        "client-side exit logic, just without the exchange-side backup", symbol,
                    )

            position_store.add(Position(
                security_id=security_id,
                scrip_code=scrip_code,
                exchange=instrument.get("exchange", "NSE"),
                segment=instrument.get("segment", "EQUITY"),
                product="INTRADAY",
                qty=qty,
                entry_price=live_ltp,
                stop_loss_price=stop_loss_price,
                target_price=target_price,
                opened_at=time.time(),
                decision_id=decision_id,
                gtt_id=gtt_id,
                symbol=symbol,
            ))

        if not approved:
            logger.debug("Skipped %s: %s", symbol, reason)


if __name__ == "__main__":
    run_loop()
