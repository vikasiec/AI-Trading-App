# Deployment

Unit tests (133) mock the broker. They do **not** prove a host will stay up
through an NSE session. This file is the missing ops layer.

## What is tested vs not

| Layer | Covered by pytest | Not covered |
|---|---|---|
| Risk, fills, session clock, Telegram auth | yes | — |
| Live INDstocks / Jev / Telegram | no | first paper day |
| Process crash + restart with open positions | no | systemd/compose `restart` |
| Token expiry mid-session | no | 09:00 timer + 24h token |
| Host sleep / laptop lid | no | use a VPS |
| Static IP whitelist | no | INDstocks dashboard |
| Disk full on positions JSON | no | monitor `~/.indstocks` |

## Recommended shape

One Linux VPS in India (or any always-on box), static egress IP, **not**
your laptop.

1. Create user `trader` with no login.
2. Clone to `/opt/jev-trader`, venv, `pip install -e .`.
3. `.env` mode `0600`, owned by `trader`. `PAPER_TRADING=true`.
4. Copy `deploy/jev-*.service` and `jev-token.timer` to `/etc/systemd/system/`.
5. `systemctl enable --now jev-token.timer jev-trader.service`.
6. Run `jev-token.service` once by hand before the first session.
7. `curl -sS http://127.0.0.1:8080/healthz` from the box only.

Docker alternative: `docker compose up -d`. Compose runs token **once** at
start, then the trader. Add a host cron `docker compose run --rm token` at
09:00 IST weekdays — compose does not schedule the timer by itself.

## Hard rules

- Only **one** token refresher. Two = both processes 401.
- `HEALTH_HOST=127.0.0.1`. Public bind needs `HEALTH_TOKEN`.
- Do not set `PAPER_TRADING=false` from compose env “just to see”.
- Whitelist the VPS IP on INDstocks **before** live orders (read-only
  quotes work without it).
- `/halt CONFIRM` from your Telegram user. Test that the day you deploy,
  before you need it.
- Architecture §11 mentions a `/user/profile` heartbeat that flattens on
  prolonged outage. **That heartbeat is not implemented.** systemd restart
  is the current backstop.

## Day-1 paper checklist

- [ ] Token file exists and is `0600`
- [ ] `journalctl -u jev-trader -f` shows watchlist resolve, not KeyError
- [ ] healthz 200 on loopback
- [ ] Telegram `/status` replies
- [ ] After 15:15 IST, flatten log line appears (or “closed” idle on weekend)
- [ ] Next morning token timer ran
