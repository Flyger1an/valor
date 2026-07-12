# Valor Evolver — Deploy & Operate

*Updated 2026-07-10.*

A self-improving research stack: it papers a validated edge forward 24/7, hunts the strategy
space on fresh OKX data daily, and surfaces only gate-surviving candidates to your phone for a
one-tap promote. **Self-healing in ops, self-improving in discovery, human-gated in promotion.**
Nothing trades real capital — there are no trading keys anywhere in this stack.

## Prerequisites
- A small cloud VM with Docker + docker-compose (2 GB RAM for the full stack; ~512 MB for the
  shadow+research+bot trio).
- `evolver/.env` present on the box (gitignored; injected at runtime, never baked into the image
  thanks to `.dockerignore`). Needs at minimum:
  ```
  TELEGRAM_BOT_TOKEN=...           # bot
  TELEGRAM_ADMIN_CHAT_IDS=...      # your chat id (admin = can /approve, /kill)
  EVOLVER_HEARTBEAT_URL=...        # healthchecks.io ping URL (external dead-man's-switch)
  OPENAI_API_KEY=...               # used by default: compose sets EVOLVER_USE_LLM=1 +
                                   # STRONG_MODEL=gpt-5-mini (research LLM mutation), and
                                   # shadow-analyst / evolver-loop run gpt-5-mini decisions
                                   # (deterministic fallbacks exist, but LLM is on by default)
  # COINALYZE_API_KEY=...          # optional — enables liq_print_daily deep history
                                   # (~4.1yr daily liquidations-by-side via Coinalyze)
  # OANDA_API_KEY=...              # practice key — FX runners only
  # FRED_API_KEY=...               # fx_carry real-rates cache — FX runners only
  ```

## Deploy
```bash
sudo systemctl enable docker          # containers come back after a host reboot
cd valor
docker compose -f infra/docker-compose.yml up -d --build          # full stack
# …or just the live trio:
# docker compose -f infra/docker-compose.yml up -d --build shadow-runner research-runner evolver-bot
```
First research cycle backfills ~15 mo of OKX hourly data (~2–3 min), then refreshes incrementally.
The shadow runner starts papering immediately.

**Host cron (outside compose):** the droplet's crontab runs a monthly Tardis harvest on the
2nd of each month (`scripts/tardis_free_snapshots.py`) that extends `.tardis_monthly_oi.pkl` —
Tardis.dev serves the 1st of every month free, feeding the options_pin pre-test data.

## Services (17, all `restart: unless-stopped`)
| Service | Role | Key env |
|---|---|---|
| `redis` | signal bus (localhost-only :6379) | — |
| `postgres` | ledger/checkpoint DB (localhost-only :5432) | `POSTGRES_*` |
| `mlflow` | experiment tracking, published on **127.0.0.1:5001** (tunnel to view) | — |
| `evolver-api` | FastAPI ingest (`/ingest` `/kpis` `/health`) on 127.0.0.1:8000 | `REDIS_URL`, `DATABASE_URL` |
| `evolver-loop` | Redis Streams consumer → inner analyst loop (gpt-5-mini + deterministic fallback) | `OPENAI_API_KEY`, `FAST_MODEL` |
| `evolver-bot` | Telegram ops + observer interface (`/candidates`, `/approve`, `/kill`) | `TELEGRAM_*` |
| `dashboard` | Streamlit cockpit on 127.0.0.1:8501 | `EVOLVER_LEDGER` |
| `shadow-runner` | forward PAPER of the liquidation basket on live OKX, hourly. Zero orders. | `EVOLVER_SHADOW`, `EVOLVER_HEARTBEAT_URL` |
| `shadow-analyst` | shadows the analyst loop's live gpt-5-mini decisions vs the sim; the single writer of sim calibration | `OPENAI_API_KEY`, `EVOLVER_SIM_CALIB` |
| `research-runner` | autonomous gated discovery on OKX (12 crypto families), **hourly** (`--loop 3600`; `CONFIRM` gate still requires multi-cycle survival). Proposes only. | `STRONG_MODEL=gpt-5-mini`, `COINALYZE_API_KEY` (optional) |
| `crypto-shadow-runner` | forward track record of promoted OKX candidates. Zero orders. | `EVOLVER_CRYPTO_SHADOW` |
| `gate-research-runner` | second crypto venue (Gate.io) — same engine + families, separate queue/multiplicity | `EVOLVER_VENUE=gate` |
| `gate-shadow-runner` | forward track record of promoted Gate candidates. Zero orders. | `EVOLVER_CRYPTO_SHADOW` (gate state) |
| `fx-research-runner` | FX hunt (4 families: fx_trend / fx_xsection / fx_session / fx_carry), separate queue | `EVOLVER_FAMILIES=fx`, `OANDA_API_KEY`, `FRED_API_KEY` |
| `fx-shadow-runner` | forward track record of promoted FX candidates. Zero orders. | `OANDA_API_KEY`, `EVOLVER_FX_SHADOW` |
| `deribit-research-runner` | Deribit options hunt (vol_premium — rejected on 2.7yr, soaking forward; options_pin — forward-accumulating daily snapshots) | `EVOLVER_FAMILIES=vol` |
| `signal-feed` | live RV signals → Redis → evolver-loop (26 pairs, 30-min emit) | `FEED_*` |

## Operate (Telegram)
| Command | Who | Does |
|---|---|---|
| `/shadow` | observer | forward paper book — equity, open/closed, sharpe |
| `/research` | observer | discovery loop status + recent cycles |
| `/candidates` | admin | gate-surviving genomes → ✅ Promote / ❌ Reject |
| `/status` `/kpis` | observer | inner-loop KPIs |
| `/kill` `/reset` | admin | kill switch (shared flag halts the loop) |

**Promotion flow:** research loop finds something → survives the gate **twice** → pings you →
`/candidates` → Promote (records approval, audit-logged) → *you* add the genome to the shadow
basket (`scripts/shadow_runner.py` `BASKET`). Promotion is human end-to-end, by design.

## Monitoring
- **Heartbeat:** shadow runner sends a daily "alive" Telegram + a catch-up alert if it was down.
- **Dead-man's-switch:** set `EVOLVER_HEARTBEAT_URL` (healthchecks.io, grace 2 h). If the box dies
  entirely, *that* service pages you — the one failure an internal watchdog can't catch.
- `restart: unless-stopped` recovers crashed containers + survives reboots. It does **not** fix a
  container that's alive-but-wedged — that's what the heartbeat/dead-man's-switch are for.

## Common ops
```bash
docker compose -f infra/docker-compose.yml logs -f shadow-runner research-runner
docker compose -f infra/docker-compose.yml pull && \
  docker compose -f infra/docker-compose.yml up -d --build        # update
docker run --rm -v valor_evolver_data:/d -v $PWD:/b alpine \
  tar czf /b/evolver_data_backup.tgz -C /d .                       # back up state/ledgers
```
State (shadow book, research queue, datasets) lives on the `evolver_data` volume — back it up to
keep the forward track record across migrations.

## Safety (load-bearing)
- **Paper only.** No trading keys exist in the running stack; the shadow runners have zero order
  capability and now mark at next-bar open + realistic costs (no optimistic close fills).
- **Demo executor is demo-locked.** `evolver/execution/okx_executor.py` *can* place orders — but
  ONLY against OKX demo (`x-simulated-trading` hard-wired; refuses if tampered). It is **not** wired
  into any service, isn't running, and needs demo keys you create. There is no live-money code path.
- **Human-gated promotion.** The loop can wake you with "I found something" — it cannot act on it.
- **Kill switch** is a shared file; `/kill` halts the inner loop from any process.
- Per `ROADMAP.md`: stop at the human-authorization gate before any real key or capital — not close.
