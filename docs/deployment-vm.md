# Ubuntu VM Deployment

## Install

1. Provision an Ubuntu VM.
2. Install Docker Engine and the Docker Compose plugin.
3. Copy the repo to the VM.
4. Copy `.env.example` to `.env` and set secrets locally on the VM.
   Set `VALOR_OPS_SECRET`, `VALOR_SESSION_SECRET`, and
   `VALOR_ADMIN_PASSWORD_HASH` before starting the app; production browser and
   ops APIs fail closed when they are missing.
5. Start the stack:

```bash
docker compose up -d --build
```

The Compose stack runs:

- `app`: Next.js dashboard
- `worker`: safe placeholder for alert delivery and ingestion jobs
- `scheduler`: evidence-loop driver that calls the app's scheduler API on an interval
- `postgres`: TimescaleDB/Postgres
- `redis`: Redis with append-only persistence

The dashboard binds to `127.0.0.1:3000` by default. Browser access uses the
`/login` page and a signed HttpOnly session cookie. Keep the dashboard behind
Tailscale, WireGuard, or an SSH tunnel unless a reverse proxy/WAF and global
rate limits are also in place.

Scheduler and soak containers read `VALOR_OPS_SECRET` from `.env` and send it
as `X-Valor-Ops-Secret`; browser-triggered operator actions use the session
cookie after login.

## Health

```bash
docker compose ps
docker compose logs -f app
docker compose logs -f worker scheduler
curl -s -H "X-Valor-Ops-Secret: $VALOR_OPS_SECRET" http://127.0.0.1:3000/api/ops/health
```

The scheduler defaults to a five-minute cadence and dry-run alert posture. Keep `SCHEDULER_SEND_ALERTS=false` until Telegram/Twilio credentials, destinations, and dry-run behavior have been manually verified.

`GET /api/ops/health` is read-only. It does not refresh data or run scheduler cycles. It reports persisted market data, data quality, scheduler lease health, scheduler errors, paper ledger integrity, system trust, evidence trail presence, and v0.2 live-guardrail posture.

Run a short post-deploy soak before trusting unattended operation:

```bash
docker compose exec app sh -lc 'SOAK_CYCLES=3 SOAK_DELAY_MS=2000 npm run soak:scheduler'
```

This command intentionally advances the scheduler/paper evidence loop and then checks `/api/ops/health`.

After any VM restart or container rebuild, check:

```bash
curl -s -H "X-Valor-Ops-Secret: $VALOR_OPS_SECRET" http://127.0.0.1:3000/api/ops/health
curl -s -H "X-Valor-Ops-Secret: $VALOR_OPS_SECRET" "http://127.0.0.1:3000/api/ops/evidence-packet?format=markdown"
```

The local test suite includes restart-recovery coverage for SQLite-backed state, so the droplet smoke should confirm the same persisted evidence is visible through the running app.

## Backups

Create a Postgres backup:

```bash
docker compose exec postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > valor-backup.sql
```

Restore:

```bash
docker compose exec -T postgres psql -U "$POSTGRES_USER" "$POSTGRES_DB" < valor-backup.sql
```

Back up `.env`, `.valor/kill-switch.json`, and Postgres volume snapshots separately. Do not store unencrypted secrets in cloud buckets.

## Private Access

Recommended options:

- Tailscale private tailnet
- WireGuard
- SSH local port forwarding

Avoid public dashboard exposure until global reverse-proxy rate limits, TLS,
and hardened secret storage are complete. Route handlers now enforce browser
session or ops-secret auth plus local rate limits, but Tailscale, WireGuard, or
SSH tunneling remains the preferred first deployment posture.
