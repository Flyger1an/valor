# Ubuntu VM Deployment

## Install

1. Provision an Ubuntu VM.
2. Install Docker Engine and the Docker Compose plugin.
3. Copy the repo to the VM.
4. Copy `.env.example` to `.env` and set secrets locally on the VM.
5. Start the stack:

```bash
docker compose up -d --build
```

The Compose stack runs:

- `app`: Next.js dashboard
- `worker`: safe placeholder for alert delivery and ingestion jobs
- `scheduler`: safe placeholder for recurring jobs
- `postgres`: TimescaleDB/Postgres
- `redis`: Redis with append-only persistence

The dashboard binds to `127.0.0.1:3000` by default. Put it behind Tailscale, WireGuard, or an SSH tunnel rather than exposing it publicly.

## Health

```bash
docker compose ps
docker compose logs -f app
docker compose logs -f worker scheduler
```

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

Avoid public dashboard exposure until authentication, rate limits, and hardened secret storage are complete.
