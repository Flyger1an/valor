# Valor Evolver — Deploy & Operate

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
  # OPENAI_API_KEY=...             # OPTIONAL — only if you flip the research loop to LLM mutation
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

## Services (all `restart: unless-stopped`)
| Service | Role |
|---|---|
| `shadow-runner` | forward PAPER of the liquidation basket on live OKX, hourly. Zero orders. |
| `research-runner` | autonomous gated discovery, **hourly** (`--loop 3600`; `CONFIRM` gate still requires multi-cycle survival). Proposes only. |
| `evolver-bot` | Telegram ops + observer interface |
| `evolver-api` / `-loop` | signal bus + inner analyst loop (optional for the trio) |
| `dashboard` | Streamlit on :8501 |
| `redis` / `postgres` / `mlflow` | infra |

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
