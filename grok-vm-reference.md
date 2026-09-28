# VM Reference for Grok

This is a snapshot of the live Oracle Cloud VM as of 2026-09-28 22:57 IST.
The repo's `deploy/` templates differ from what is actually installed.
When reviewing, use this document as ground truth for the server state.

## System

- **Host:** Oracle Cloud VM, Oracle Linux 9 (EL9), kernel 6.12
- **Timezone:** Asia/Kolkata (IST, UTC+5:30) -- all cron times are IST
- **Python:** 3.9.25
- **Access:** SSH as `opc` user (key not stored in repo)

## Installed service vs deploy template

The live service file (`/etc/systemd/system/jev-trader.service`) was
installed manually and differs from `deploy/jev-trader.service`:

| Setting | deploy/ template | Live installed |
|---------|-----------------|----------------|
| User | trader | opc |
| WorkingDirectory | /opt/jev-trader | /home/opc/AI-Trading-App |
| RestartSec | 5 | 30 |
| StartLimitBurst | 8 | not set (systemd default) |
| StartLimitIntervalSec | 300 | not set (systemd default) |
| Security hardening | NoNewPrivileges, PrivateTmp, etc. | not set |
| MemoryMax | 512M | not set |
| EnvironmentFile | /opt/jev-trader/.env | not used; .env loaded by python-dotenv |
| PYTHONPATH | not set | /home/opc/AI-Trading-App/src |

The `deploy/jev-token.timer` (systemd timer for 09:00 token refresh) is
**not installed**. Token refresh runs via the opc user's crontab instead.

### Live service file

```ini
[Unit]
Description=Jev AI Trading Bot (Paper Trading)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=opc
WorkingDirectory=/home/opc/AI-Trading-App
Environment=PATH=/home/opc/.local/bin:/usr/local/bin:/usr/bin
Environment=PYTHONPATH=/home/opc/AI-Trading-App/src
ExecStart=/usr/bin/python3 -m jev_indstocks_trader.main
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

## Crontab (opc user)

All times are IST. crond reads the system clock which is IST.

| Time IST | Day | Job | Log file |
|----------|-----|-----|----------|
| 08:45 | Mon-Fri | `scan_premarket.py` -- builds today's watchlist | ~/logs/watchlist_scan.log |
| 08:50 | Mon-Fri | `premarket_health.py` -- 9 checks, Telegram summary | ~/logs/health_check.log |
| 09:00 | Mon-Fri | `refresh_token.py` -- broker token, silent on success | ~/logs/token_refresh.log |
| 09:14 | Mon-Fri | `systemctl start jev-trader` -- safety net, no-op if running | (none) |
| Every 30m 09:00-15:30 | Mon-Fri | `watchdog.py` -- alerts if service is down during market | ~/logs/watchdog.log |
| 15:45 | Mon-Fri | `eod_accuracy.py` -- prediction vs outcome, Telegram report | ~/logs/eod_accuracy.log |
| 16:00 | Friday | `run_calibration_report.py` -- weekly H1/H2/H3 report, Telegram | ~/logs/calibration.log |
| 00:00 | Sunday | Log rotation -- archives ~/logs/*.log to ~/logs/archive/ | (none) |

## Watchlist wiring

- `WATCHLIST_SYMBOLS` is **commented out** in .env
- `WATCHLIST_FILE` points to `/home/opc/.indstocks/watchlist_200ma.json`
- The scanner writes this file at 08:45; the bot re-reads it on every tick
- Scanner logic: 200MA crossover + RS top 50, padded to minimum 10 with RS ranking
- Current watchlist (10 stocks): WHIRLPOOL, DIVISLAB, NAUKRI, PVRINOX, APLAPOLLO, COFORGE, SUNDRMFAST, SONACOMS, GNFC, GLAXO

## File layout

```
/home/opc/
  AI-Trading-App/           # git repo, branch main
    .env                     # live config (DO NOT print values)
    audit_trail.jsonl        # append-only decision log
    src/                     # Python package
    scripts/                 # cron scripts
    tests/                   # 237 tests, all passing
    deploy/                  # templates -- NOT what's installed
  logs/                      # cron job output
    archive/                 # weekly rotation target
  .indstocks/
    session_token.json       # broker auth token (refreshed at 09:00)
    instruments_master.csv   # broker instrument list
    watchlist_200ma.json     # scanner output -> bot reads this
    watchlist_rs.json        # full RS ranking (reference only)
    jev_daily_counter.json   # Jev API call counter
```

## Bot runtime config (env keys, no values)

These are the env vars the bot reads. Values are secret and must not be displayed.

**Trading parameters:**
PAPER_TRADING, ENTRY_MODE, STOP_LOSS_PCT (default 0.01), TARGET_PCT (default 0.02),
OPEN_SKIP_MINUTES (default 15), MAX_HOLD_MINUTES, MAX_CONCURRENT_POSITIONS,
MAX_POSITION_CAPITAL_INR, MAX_DEPLOYED_CAPITAL_PCT, MAX_SLIPPAGE_PCT,
DAILY_LOSS_LIMIT_PCT, GAP_SKIP_ABS_PCT

**Jev AI:**
JEV_API_KEY, JEV_BASE_URL, JEV_MODEL, JEV_CONVICTION_THRESHOLD,
JEV_CONFIDENCE_THRESHOLD, JEV_NOISE_THRESHOLD, JEV_NOISE_VETO,
JEV_RESCORE_S, JEV_DAILY_CALL_CAP, JEV_TIMEOUT_S

**Broker:**
INDSTOCKS_CLIENT_ID, INDSTOCKS_MPIN, INDSTOCKS_TOTP_SECRET,
INDSTOCKS_BASE_URL, INDSTOCKS_WEBSOCKET_URL, INDSTOCKS_TOKEN_CACHE

**Telegram:**
TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_CHAT_ID, TELEGRAM_OWNER_USER_ID

**Other:**
ATR_STOPS_ENABLED, ATR_STOP_MULTIPLIER, ATR_STOP_FLOOR_PCT, ATR_STOP_CAP_PCT,
ATR_TARGET_MULTIPLIER, INDEX_VETO_ENABLED, INDEX_VETO_PCT, GTT_ENABLED,
HEARTBEAT_INTERVAL_S, HEARTBEAT_FAILS_BEFORE_FLATTEN, HEARTBEAT_FLATTEN,
WEBSOCKET_ENABLED, LOG_JSON, AUDIT_LOG_PATH, POSITIONS_STORE_PATH

## Current state (2026-09-28 22:57 IST)

- Service: **active**, PID 98927, started 21:41 IST
- Mode: **PAPER_TRADING=true**, ENTRY_MODE=jev_and_rule
- Watchlist: 10 stocks from scanner
- Open positions: 0
- Audit trail: 1 decision (1 SKIP, 0 trades)
- Tests: 237 passed
- Git: clean, on main at a5819af

## How to verify claims about this VM

If Grok or any reviewer says "the service file says X" or "the crontab does Y",
ask them whether they are reading the repo's deploy/ templates or this document.
The deploy/ templates were a starting point and have not been kept in sync with
the live installation. This document reflects the actual running state.

To re-capture this snapshot, SSH in and run:
```
timedatectl | grep 'Time zone'
cat /etc/systemd/system/jev-trader.service
crontab -l
systemctl status jev-trader
cat /home/opc/.indstocks/watchlist_200ma.json
```
