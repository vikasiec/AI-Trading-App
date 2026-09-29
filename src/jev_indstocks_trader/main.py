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
from .health import build_payload, start_health_server
from .heartbeat import BrokerHeartbeat
from .session import minutes_since_open, minutes_until_flatten, now_ist, session_phase
from .bar_cache import BarCache
from .entry import combine_votes, gap_veto, index_veto, rule_vote
from .gaps import GapBook
from .jev_counter import JevDailyCounter
from .opening_range import OpeningRangeBook
from .features import compute_atr_stops, compute_features, features_block
from .jev_client import ConvictionResult
from .slog import configure_logging
from .costs import CostRates
from .execution_gateway import ExecutionGateway, extract_order_id
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

logger = logging.getLogger(__name__)


def run_loop(poll_interval_s: float = 1.0) -> None:
    load_dotenv()
    cfg = load_config()
    configure_logging(json_mode=cfg.log_json)
    warnings = validate_config(cfg)
    blockers = [w for w in warnings if "placeholder" in w.lower()]
    for warning in warnings:
        logger.warning("config: %s", warning)
    if blockers:
        raise RuntimeError(
            "Refusing to start with unimplemented features enabled: " + "; ".join(blockers)
        )

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
        flatten_ok = True
        if not cfg.risk.paper_trading:
            try:
                governor.flatten_all()
            except Exception:
                logger.exception("Kill switch flatten failed — keeping local positions tracked")
                flatten_ok = False
        if flatten_ok or cfg.risk.paper_trading:
            for pos in list(position_store.list_open()):
                if pos.gtt_id and not cfg.risk.paper_trading:
                    try:
                        gtt_orders.cancel_gtt(cfg.indstocks, auth_headers_fn, pos.gtt_id)
                    except Exception:
                        logger.exception("Kill switch could not cancel GTT %s", pos.gtt_id)
                position_store.remove(pos.security_id)
        try:
            msg = "Kill switch completed: flatten + local store cleared"
            if not flatten_ok:
                msg = "Kill switch INCOMPLETE: broker flatten failed, local positions kept for exit logic"
            notifier.send_critical_alert(msg)
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
    heartbeat = BrokerHeartbeat(
        cfg.indstocks,
        auth_headers_fn,
        interval_s=cfg.heartbeat_interval_s,
        fails_before_flatten=cfg.heartbeat_fails_before_flatten,
        flatten_on_outage=cfg.heartbeat_flatten and not cfg.risk.paper_trading,
        flatten_fn=_kill_switch,
        notifier=notifier,
    )

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

    started_at = time.time()
    try:
        start_health_server(
            lambda: build_payload(
                paper_trading=cfg.risk.paper_trading,
                open_positions=len(position_store.list_open()),
                watchlist_n=len(watchlist),
                started_at=started_at,
            ),
            host=cfg.health_host,
            port=cfg.health_port,
            token=cfg.health_token,
        )
    except OSError:
        logger.exception("Could not bind healthz on %s:%s", cfg.health_host, cfg.health_port)

    logger.info("Trading loop starting. Ctrl+C to stop. ENTRY_MODE=%s", cfg.risk.entry_mode)
    mode_label = "PAPER" if cfg.risk.paper_trading else "LIVE"
    try:
        notifier.send_info(
            f"🟢 Bot started ({mode_label})\n"
            f"Watchlist: {', '.join(watchlist)}\n"
            f"Entry mode: {cfg.risk.entry_mode}\n"
            f"Open positions: {len(position_store.list_open())}"
        )
    except Exception:
        logger.exception("Could not send startup alert to Telegram")
    flattened_on: str | None = None
    bar_cache = BarCache()
    or_book = OpeningRangeBook()
    gap_book = GapBook()
    jev_last_called: dict[str, float] = {}
    jev_counter = JevDailyCounter()
    jev_daily_count = [jev_counter.get(now_ist().date().isoformat())]
    jev_daily_date = now_ist().date().isoformat()
    sweep_pending: set[str] = set()
    sweep_last_refilled: float = 0.0
    try:
        while True:
            if heartbeat.due():
                heartbeat.ping()
            phase = session_phase() if cfg.respect_session else "open"
            today = now_ist().date().isoformat()
            if phase == "closed":
                logger.debug("Market closed (%s IST) — idle", now_ist().strftime("%H:%M"))
                time.sleep(max(poll_interval_s, 15.0))
                continue
            if phase == "flatten":
                if flattened_on != today:
                    logger.warning("Session flatten window — closing all open positions")
                    exit_manager.force_exit_all("session_close")
                    if not cfg.risk.paper_trading:
                        try:
                            governor.flatten_all()
                        except Exception:
                            logger.exception("Broker flatten after session close failed")
                    if not position_store.list_open():
                        notifier.send_critical_alert(f"Session flatten completed ({today})")
                        flattened_on = today
                    else:
                        logger.warning("Session flatten incomplete — %d positions still open, retrying next tick",
                                       len(position_store.list_open()))
                time.sleep(max(poll_interval_s, 5.0))
                continue
            if not cfg.risk.paper_trading and reconciler.due():
                reconciler.reconcile()
            exit_manager.check_and_exit_all()
            or_book.roll_day(today)
            gap_book.roll_day(today)
            if today != jev_daily_date:
                jev_counter.reset_if_new_day(today)
                jev_daily_count[0] = 0
                jev_daily_date = today
                jev_last_called.clear()
            mins_open = minutes_since_open() if cfg.respect_session else None
            forming = mins_open is not None and mins_open < cfg.risk.open_skip_minutes
            mins_to_flatten = minutes_until_flatten() if cfg.respect_session else None
            closing_soon = (
                mins_to_flatten is not None
                and mins_to_flatten <= cfg.risk.last_entry_minutes_before_close
            )
            # Sweep refill: every sweep_interval_min, reload the full watchlist
            # for log-only Jev scoring of rule-gated symbols.
            now_mono = time.monotonic()
            if (
                cfg.risk.sweep_enabled
                and not forming
                and not closing_soon
                and cfg.risk.entry_mode == "jev_and_rule"
                and (now_mono - sweep_last_refilled) >= cfg.risk.sweep_interval_min * 60
            ):
                sweep_pending = set(watchlist)
                sweep_last_refilled = now_mono
                logger.info("Sweep refilled: %d symbols queued", len(sweep_pending))

            index_chg = _read_index_change(gateway, tick_cache, cfg.risk.index_scrip)
            try:
                _tick(
                    cfg, gateway, governor, evaluator, instruments, audit, notifier,
                    position_store, watchlist, news_source, tick_cache, bar_cache,
                    or_book=or_book,
                    gap_book=gap_book,
                    form_opening_range=forming,
                    allow_entries=not forming and not closing_soon,
                    entry_block_reason="forming_or" if forming else "closing_soon" if closing_soon else None,
                    index_day_change_pct=index_chg,
                    jev_last_called=jev_last_called,
                    jev_daily_count=jev_daily_count,
                    jev_counter=jev_counter,
                    kill_switch_fn=_kill_switch,
                    sweep_pending=sweep_pending if cfg.risk.sweep_enabled else None,
                )
            except Exception:
                logger.exception("Tick failed — exits still running, skipping new entries this iteration")
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
        return {"ltp": live, "day_change_pct": None, "volume": 0}
    return retry_with_backoff(
        lambda: gateway.get_quote(scrip_code), max_retries=3, base_delay_s=0.5,
    )


def _read_index_change(gateway, tick_cache, scrip: str) -> float | None:
    if not scrip:
        return None
    try:
        q = _get_quote(gateway, tick_cache, scrip)
        chg = q.get("day_change_pct")
        return float(chg) if chg is not None else None
    except Exception:
        logger.debug("Index quote unavailable for %s", scrip, exc_info=True)
        return None


def _tick(cfg, gateway, governor, evaluator, instruments, audit, notifier,
          position_store, watchlist, news_source, tick_cache, bar_cache=None,
          or_book=None, gap_book=None, form_opening_range: bool = False, allow_entries: bool = True,
          entry_block_reason: str | None = None,
          index_day_change_pct: float | None = None,
          jev_last_called: dict | None = None, jev_daily_count: list | None = None,
          jev_counter: JevDailyCounter | None = None,
          kill_switch_fn=None,
          sweep_pending: set | None = None) -> None:
    """One iteration: fetch a snapshot per watchlist symbol, score it, and
    (maybe) open a new position. Exit checks run separately, before this,
    in run_loop() -- closing existing risk always happens before opening
    new risk.
    """
    # Pre-resolve instruments and batch-fetch quotes to avoid per-symbol API calls.
    resolved_symbols: list[tuple[str, dict, str]] = []
    rest_codes: list[str] = []
    for symbol in watchlist:
        try:
            instrument = instruments.resolve(symbol)
        except KeyError:
            logger.warning("Symbol %s not found in instruments master, skipping", symbol)
            continue
        security_id = instrument["security_id"]
        if position_store.has_open(security_id):
            continue
        scrip_code = instrument.get("scrip_code") or f"NSE_{security_id}"
        ws_tick = tick_cache.get_fresh(scrip_code) if tick_cache else None
        if ws_tick is None:
            rest_codes.append(scrip_code)
        resolved_symbols.append((symbol, instrument, scrip_code))

    batch_quotes: dict[str, dict] = {}
    if rest_codes:
        try:
            batch_quotes = retry_with_backoff(
                lambda: gateway.get_quotes_batch(rest_codes),
                max_retries=2, base_delay_s=1.0,
            )
        except Exception:
            logger.exception("Batch quote fetch failed for %d symbols, falling back to individual", len(rest_codes))

    tick_skip_reasons: dict[str, int] = {}
    tick_jev_calls = 0
    tick_entries = 0
    tick_sweep_calls = 0
    kill_switch_fired = False
    sweep_cap = int(cfg.jev.daily_call_cap * cfg.risk.sweep_cap_pct) if sweep_pending is not None else 0

    for symbol, instrument, scrip_code in resolved_symbols:
        security_id = instrument["security_id"]

        ws_tick = tick_cache.get_fresh(scrip_code) if tick_cache else None
        if ws_tick is not None:
            quote = {"ltp": ws_tick, "day_change_pct": None, "volume": 0}
        elif scrip_code in batch_quotes:
            quote = batch_quotes[scrip_code]
        else:
            try:
                quote = retry_with_backoff(
                    lambda: gateway.get_quote(scrip_code), max_retries=3, base_delay_s=0.5,
                )
            except Exception:
                logger.exception("Could not get quote for %s after retries, skipping this tick", symbol)
                tick_skip_reasons["quote_failed"] = tick_skip_reasons.get("quote_failed", 0) + 1
                continue

        scored_at_price = quote["ltp"]
        if bar_cache is not None:
            bar_cache.update(symbol, scored_at_price, int(quote.get("volume") or 0))
        if or_book is not None and form_opening_range:
            or_book.update(symbol, scored_at_price)
        if gap_book is not None:
            gap_book.observe(symbol, quote.get("day_change_pct"))
        bars = bar_cache.bars(symbol) if bar_cache is not None else []
        rng = or_book.get(symbol) if or_book is not None else None
        feat = compute_features(
            bars,
            index_day_change_pct=index_day_change_pct,
            or_high=rng.high if rng else None,
            or_low=rng.low if rng else None,
            gap_pct=gap_book.get(symbol) if gap_book is not None else None,
        )
        vote = rule_vote(bars, feat)
        if not allow_entries:
            reason = entry_block_reason or "forming_or"
            tick_skip_reasons[reason] = tick_skip_reasons.get(reason, 0) + 1
            continue
        headlines = news_source.headlines_for(symbol, instrument.get("name")) if news_source else []
        snapshot = MarketSnapshot(
            symbol=symbol,
            ltp=scored_at_price,
            day_change_pct=quote.get("day_change_pct"),
            volume=quote.get("volume") or 0,
            headlines=headlines,
        )
        context = build_context(snapshot, extra=features_block(feat))

        mode = getattr(cfg.risk, "entry_mode", "jev")

        # --- Pre-Jev vetoes: skip the API call when the outcome is already decided ---
        if getattr(cfg.risk, "index_veto_enabled", False):
            iv = index_veto(index_day_change_pct, cfg.risk.index_veto_pct)
            if not iv.allow:
                logger.debug("Pre-Jev skip %s: %s", symbol, iv.reason)
                tick_skip_reasons[iv.reason] = tick_skip_reasons.get(iv.reason, 0) + 1
                audit.log_skip(security_id, iv.reason, symbol=symbol)
                continue
        gv = gap_veto(feat.gap_pct, getattr(cfg.risk, "gap_skip_abs_pct", 0.0))
        if not gv.allow:
            logger.debug("Pre-Jev skip %s: %s", symbol, gv.reason)
            tick_skip_reasons[gv.reason] = tick_skip_reasons.get(gv.reason, 0) + 1
            audit.log_skip(security_id, gv.reason, symbol=symbol)
            continue
        if mode == "jev_and_rule" and not vote.allow:
            logger.debug("Pre-Jev skip %s: rule_%s", symbol, vote.reason)
            tick_skip_reasons[f"rule_{vote.reason}"] = tick_skip_reasons.get(f"rule_{vote.reason}", 0) + 1
            audit.log_skip(security_id, f"rule_{vote.reason}", symbol=symbol)
            # --- Sweep: log-only Jev call on rule-gated symbols ---
            sweep_rate_ok = True
            if jev_last_called is not None and symbol in jev_last_called:
                elapsed = time.monotonic() - jev_last_called[symbol]
                if elapsed < cfg.jev.rescore_interval_s:
                    sweep_rate_ok = False
            if (
                sweep_rate_ok
                and sweep_pending is not None
                and symbol in sweep_pending
                and tick_sweep_calls < cfg.risk.sweep_per_tick_cap
                and jev_daily_count is not None
                and jev_daily_count[0] < sweep_cap
            ):
                sweep_pending.discard(symbol)
                tick_sweep_calls += 1
                headlines = news_source.headlines_for(symbol, instrument.get("name")) if news_source else []
                snapshot = MarketSnapshot(
                    symbol=symbol, ltp=scored_at_price,
                    day_change_pct=quote.get("day_change_pct"),
                    volume=quote.get("volume") or 0, headlines=headlines,
                )
                ctx = build_context(snapshot, extra=features_block(feat))
                try:
                    sweep_result = retry_with_backoff(
                        lambda: evaluator.evaluate_signal(ctx), max_retries=1, base_delay_s=0.5,
                    )
                except Exception:
                    logger.exception("Sweep Jev call failed for %s", symbol)
                else:
                    would_pass = (
                        sweep_result.score >= cfg.risk.min_conviction
                        and sweep_result.confidence >= cfg.risk.min_confidence
                        and not (
                            getattr(cfg.jev, "noise_veto", False)
                            and sweep_result.noise_score >= cfg.jev.noise_threshold
                        )
                    )
                    audit.log_decision(
                        security_id=security_id,
                        jev_conviction=sweep_result.score,
                        jev_confidence=sweep_result.confidence,
                        action="SWEEP",
                        latency_ms=0.0,
                        otr_check="SWEEP",
                        had_news=(len(headlines) > 0) if news_source is not None else None,
                        reason=f"rule_{vote.reason}",
                        symbol=symbol,
                        detail={
                            "rule_reason": vote.reason,
                            "regime": feat.regime,
                            "scored_at_price": scored_at_price,
                            "noise_score": sweep_result.noise_score,
                            "would_pass": would_pass,
                            "paper": cfg.risk.paper_trading,
                        },
                    )
                    logger.info(
                        "SWEEP %s: conviction=%.2f confidence=%.2f would_pass=%s rule=%s",
                        symbol, sweep_result.score, sweep_result.confidence, would_pass, vote.reason,
                    )
                finally:
                    if jev_daily_count is not None:
                        jev_daily_count[0] += 1
                    if jev_counter is not None:
                        jev_counter.increment(now_ist().date().isoformat())
            continue

        result = ConvictionResult(score=0.0, confidence=0.0, raw={})
        latency_ms = 0.0
        jev_ok = False
        if mode != "rule":
            # Rate-limit: at most one Jev call per symbol per rescore interval
            now_mono = time.monotonic()
            if jev_last_called is not None:
                last = jev_last_called.get(symbol, 0.0)
                if (now_mono - last) < cfg.jev.rescore_interval_s:
                    logger.debug("Jev rate-limit skip %s (%.0fs remaining)", symbol,
                                 cfg.jev.rescore_interval_s - (now_mono - last))
                    tick_skip_reasons["jev_rate_limit"] = tick_skip_reasons.get("jev_rate_limit", 0) + 1
                    audit.log_skip(security_id, "jev_rate_limit", symbol=symbol)
                    continue
            # Daily cap
            if jev_daily_count is not None and jev_daily_count[0] >= cfg.jev.daily_call_cap:
                logger.debug("Jev daily cap reached (%d), skipping %s", jev_daily_count[0], symbol)
                tick_skip_reasons["jev_daily_cap"] = tick_skip_reasons.get("jev_daily_cap", 0) + 1
                audit.log_skip(security_id, "jev_daily_cap", symbol=symbol)
                continue
            tick_jev_calls += 1
            t0 = time.monotonic()
            try:
                result = retry_with_backoff(lambda: evaluator.evaluate_signal(context), max_retries=1, base_delay_s=0.5)
                jev_ok = (
                    result.score >= cfg.risk.min_conviction
                    and result.confidence >= cfg.risk.min_confidence
                )
                if getattr(cfg.jev, "noise_veto", False) and result.noise_score >= cfg.jev.noise_threshold:
                    jev_ok = False
            except Exception:
                logger.exception("Jev scoring failed for %s, skipping this tick", symbol)
                audit.log_skip(security_id, "jev_error", symbol=symbol)
                continue
            finally:
                if jev_last_called is not None:
                    jev_last_called[symbol] = time.monotonic()
                if jev_daily_count is not None:
                    jev_daily_count[0] += 1
                if jev_counter is not None:
                    jev_counter.increment(now_ist().date().isoformat())
                if jev_daily_count is not None and jev_daily_count[0] == cfg.jev.daily_call_cap:
                    logger.warning("Jev daily call cap reached: %d", cfg.jev.daily_call_cap)
                    notifier.send_info(f"⚠️ Jev daily call cap reached ({cfg.jev.daily_call_cap}). No more Jev scoring today.")
            latency_ms = (time.monotonic() - t0) * 1000
        else:
            result = ConvictionResult(score=1.0, confidence=1.0, raw={"mode": "rule"})
            jev_ok = True

        gate_ok, gate_reason = combine_votes(mode, jev_ok, vote)
        if not gate_ok:
            audit.log_decision(
                security_id=security_id,
                jev_conviction=result.score,
                jev_confidence=result.confidence,
                action="SKIP",
                latency_ms=latency_ms,
                otr_check=gate_reason,
                order_id=None,
                had_news=(len(headlines) > 0) if news_source is not None else None,
                reason=gate_reason,
                symbol=symbol,
                detail={"entry_mode": mode, "scored_at_price": scored_at_price, "paper": cfg.risk.paper_trading},
            )
            continue

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

        if reason == "daily_drawdown_limit_hit" and kill_switch_fn is not None and not kill_switch_fired:
            logger.warning("Daily drawdown limit hit — triggering kill switch")
            kill_switch_fn()
            kill_switch_fired = True
            break

        action = "SKIP"
        order_id = None
        qty = 0
        fill_price = live_ltp
        if approved:
            funds = gateway.get_funds()
            equity = gateway.equity_from_funds(funds)
            qty = governor.size_order(equity, live_ltp, lot_size=int(instrument.get("lot_size") or 1))
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
                        order_id = extract_order_id(order)
                        if not order_id:
                            logger.error("Place-order response for %s had no order id: %s", symbol, order)
                            approved = False
                            qty = 0
                            action = "SKIP"
                            reason = "order_id_missing"
                        else:
                            fill = gateway.wait_for_fill(order_id, timeout_s=3.0, requested_qty=qty)
                        if order_id and fill.status in ("FILLED", "PARTIAL"):
                            fill_price = fill.avg_price or live_ltp
                            qty = fill.filled_qty
                            if fill.status == "PARTIAL":
                                try:
                                    gateway.cancel_order(order_id)
                                except Exception:
                                    logger.exception("Could not cancel residual after partial BUY %s", order_id)
                                notifier.send_critical_alert(
                                    f"PARTIAL FILL BUY {symbol}: booked {qty} of requested, "
                                    f"cancelled residual order {order_id} — verify book"
                                )
                            action = "BUY" if fill.status == "FILLED" else "BUY (partial)"
                            governor.mark_executed(security_id)
                            notifier.send_info(
                                f"BUY {symbol} x{qty} @ {fill_price} (order {order_id}, {fill.status})"
                            )
                        elif order_id and fill.status in ("REJECTED", "CANCELLED"):
                            logger.warning("Order %s for %s was %s -- not opening a position", order_id, symbol, fill.status)
                            approved = False
                            qty = 0
                            action = "SKIP"
                            reason = f"order_{fill.status.lower()}"
                        elif order_id:
                            # TIMEOUT / NOT_FOUND: ambiguous. Check if any qty filled.
                            try:
                                gateway.cancel_order(order_id)
                            except Exception:
                                logger.exception("Best-effort cancel of ambiguous order %s failed", order_id)
                            if fill.filled_qty and fill.filled_qty > 0:
                                fill_price = fill.avg_price or live_ltp
                                qty = fill.filled_qty
                                action = "BUY (timeout-partial)"
                                governor.mark_executed(security_id)
                                notifier.send_critical_alert(
                                    f"TIMEOUT PARTIAL BUY {symbol}: {qty} filled @ {fill_price}, "
                                    f"cancelled residual order {order_id} — verify book"
                                )
                            else:
                                governor.mark_executed(security_id)
                                notifier.send_critical_alert(
                                    f"AMBIGUOUS FILL: {symbol} order {order_id} status={fill.status} "
                                    f"-- cancelled, verify manually against broker order book"
                                )
                                approved = False
                                qty = 0
                                action = "SKIP"
                                reason = "order_fill_unresolved"
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
            reason=reason,
            symbol=symbol,
            detail={
                "entry_mode": mode,
                "scored_at_price": scored_at_price,
                "live_ltp": live_ltp,
                "qty": qty,
                "noise_score": result.noise_score,
                "paper": cfg.risk.paper_trading,
                "rule": vote.reason,
            },
        )

        if approved and qty > 0:
            if cfg.risk.atr_stops_enabled and feat.atr_pct > 0:
                sl_pct, tp_pct = compute_atr_stops(
                    feat.atr_pct, cfg.risk.atr_stop_multiplier,
                    cfg.risk.atr_target_multiplier,
                    cfg.risk.atr_stop_floor_pct, cfg.risk.atr_stop_cap_pct,
                )
                logger.info("ATR stops for %s: ATR=%.3f%% SL=%.2f%% TP=%.2f%%",
                            symbol, feat.atr_pct * 100, sl_pct * 100, tp_pct * 100)
            else:
                sl_pct = cfg.risk.stop_loss_pct
                tp_pct = cfg.risk.target_pct
            stop_loss_price = fill_price * (1 - sl_pct)
            target_price = fill_price * (1 + tp_pct)

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
                entry_price=fill_price,
                stop_loss_price=stop_loss_price,
                target_price=target_price,
                opened_at=time.time(),
                decision_id=decision_id,
                gtt_id=gtt_id,
                symbol=symbol,
            ))

        if approved and qty > 0:
            tick_entries += 1
        if not approved:
            logger.debug("Skipped %s: %s", symbol, reason)

    n_checked = len(resolved_symbols)
    if n_checked > 0:
        skips_summary = ", ".join(f"{v} {k}" for k, v in sorted(tick_skip_reasons.items(), key=lambda x: -x[1]))
        logger.info(
            "Tick: %d symbols | %d entries | %d jev calls | %d sweeps | skips: %s",
            n_checked, tick_entries, tick_jev_calls, tick_sweep_calls,
            skips_summary or "none",
        )


if __name__ == "__main__":
    run_loop()
