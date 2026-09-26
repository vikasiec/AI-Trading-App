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
- Heartbeat: `GET /user/profile` every `HEARTBEAT_INTERVAL_S`. Failures
  alert Telegram. Live + `HEARTBEAT_FLATTEN=true` flattens after
  `HEARTBEAT_FAILS_BEFORE_FLATTEN` misses. Paper never auto-flattens.

## What I cannot do from the repo

Fill `.env`, buy the VPS, whitelist the IP, push with your GitHub login,
or flip `PAPER_TRADING=false`. Those stay on you. See the split in the
last working notes: you run paper; I patch from logs you send.

## Day-1 paper checklist

- [ ] Token file exists and is `0600`
- [ ] `journalctl -u jev-trader -f` shows watchlist resolve, not KeyError
- [ ] healthz 200 on loopback
- [ ] Telegram `/status` replies
- [ ] After 15:15 IST, flatten log line appears (or “closed” idle on weekend)
- [ ] Next morning token timer ran

## Infra cost for 24×7 (cloud)

The bot is one Python loop (~150–300 MB RAM, a few MB/day of quotes +
Telegram + Jev). It does **not** need Kubernetes, a load balancer, a
managed DB, or a GPU. Jev inference is an HTTP call.

Run it **24×7** anyway: nights and weekends it idles, but Monday 09:15
and the 15:15 IST flatten only work if the box is up. A laptop lid or a
scale-to-zero function will miss both.

Prices below are public list prices as of late September 2026, USD,
excluding GST/VAT and FX. Treat them as planning numbers, not invoices.

### Budget

| Setup | What you get | Approx / month | Year |
|---|---|---|---|
| Minimum that works | 1 vCPU, 512 MB–1 GB, static IPv4 | $4–7 (~₹350–600) | $50–85 |
| Comfortable (recommended) | 1–2 vCPU, 1–2 GB, weekly snapshots | $6–12 (~₹500–1,000) | $70–145 |
| Cloud theatre | ECS/EKS + NAT + LB + CloudWatch + RDS | $40–150+ | waste |

If the infra bill is above ~₹1,000/month, you overbuilt.

### Concrete options

| Provider | Plan | Notes |
|---|---|---|
| DigitalOcean | Basic 512 MB **$4/mo**, 1 GB **$6/mo** | IP included. Bangalore / Singapore regions exist. |
| AWS Lightsail Mumbai | 512 MB + public IPv4 **$5/mo**, 1 GB **$7/mo** | Mumbai transfer allowance is half the global bundle. IPv6-only ($3.50) is a bad fit — INDstocks whitelist wants stable IPv4. |
| Hetzner CX22 | ~$5–6/mo for 2 vCPU / 4 GB | Overkill for this process. No India DC (EU / US / Singapore). Extra latency to NSE APIs. |
| India VPS (Noida etc.) | often ₹400–800/mo | GST invoice, low IST latency. |

Raw EC2 + Elastic IP + NAT + CloudWatch looks cheap on the calculator
and is not. Lightsail is the sane AWS path if you already live there.

### Do not pay for

| Item | Why skip |
|---|---|
| Load balancer | One process, healthz on loopback |
| Managed Kubernetes | Ops cost > the VM |
| Managed database | Positions are a JSON file |
| GPU / big RAM | No local model |
| Multi-AZ HA | One bot; two boxes also means two token refreshers (forbidden) |
| Scale-to-zero / Lambda | Cold start misses the open and the flatten |

Egress is tiny. Included transfer on any $5 plan is enough.

### Extra that does cost

- Snapshots / backups: ~20% of a DigitalOcean droplet, or about $1.
- GST 18% on Indian invoices; USD cards pick up bank FX.
- **Jev / TypeSafe usage and INDstocks brokerage are not infra.** Live
  trading will dwarf the VPS bill.
- A second “HA” box doubles infra and breaks single-owner token refresh.

### Recommendation

**$6/mo DigitalOcean 1 GB (Bangalore) or Lightsail 1 GB (Mumbai).**
`PAPER_TRADING=true`, systemd units above, weekly snapshots.

Keep a dedicated static IPv4 and whitelist it on the INDstocks dashboard
before any live order. Quotes work without that; `/order` does not.
