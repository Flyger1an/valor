# Valor Evolver 🧬

Perpetual, self-optimizing **closed-loop** engine for the Valor RV + Risk platform.
Polyglot sibling: **Valor (TS)** stays the signal/risk engine; **Evolver (Python)**
ingests the locked signal contract, paper-trades, evaluates, and (human-gated)
self-optimizes.

```
ingest → analyst → paper_trade → evaluate ─┬─ done                 [inner loop / per signal]
                                           └─ optimize → critic ─┬─ deploy → live   [outer loop]
                                                                 └─ done            (human-gated)
```

## ✅ Verified first cycle (zero installs — pure stdlib core)

```bash
cd evolver
python3 scripts/first_cycle.py     # runs your 5 sample signals through the loop
python3 tests/test_core.py         # 6 safety invariants
```

Observed on the 5 sample signals:
- `rv_001 cointegration` → **long**, +$2,136 (converged)
- `rv_002 funding_arb` → **short**, −$2.55 (thin edge, correct)
- `rv_003 basis_trade` → **neutral** (gated: `high_vol` regime)
- `rv_004 stat_arb_pair` → **neutral** (gated: `momentum_break` regime)
- `rv_005 triangular` → **long**, +$67 (converged)
- KPIs: `sharpe_per_trade 0.74`, `win_rate 0.67`, `rv_convergence_accuracy 1.0`,
  plus a `sample_warning` (annualized metrics omitted on n=3 — honest, not fantasy 48-Sharpe).

## Layout

| Path | Role |
|---|---|
| `evolver/core/` | **dep-light, verified**: `signal` (locked contract), `risk` (AdaptiveRiskManager v2), `sim` (perp paper sim), `kpis` |
| `evolver/agents/` | `analyst` (fast model + deterministic fallback), `critic` (strong model reflection) |
| `evolver/graph/` | LangGraph `state` / `nodes` / `build` (+ `runtime` shared state) |
| `evolver/optimize/` | `backtest` (reuses core sim), `optuna_study` (walk-forward), `promotion` (OOS + significance + risk gate) |
| `evolver/safety.py` | kill-switch, drawdown circuit breaker, RBAC, audit log |
| `evolver/loop.py` | dep-light inner-loop orchestrator (used by API + bus) |
| `evolver/api.py` | FastAPI ingest (`/ingest`, `/kpis`, `/health`) |
| `evolver/bus/` | Redis Streams consumer (TS↔Python) |
| `evolver/telegram/` | Ops + Observer bot (PTB v21) |
| `evolver/dashboard/` | Streamlit cockpit |

## Run the full stack (Docker)

```bash
cp evolver/.env.example evolver/.env     # fill keys
docker compose -f infra/docker-compose.yml up --build
# API :8000  · Streamlit :8501  · MLflow :5000
curl -X POST localhost:8000/ingest -H 'content-type: application/json' \
  -d @shared/sample_signals_one.json     # or POST any signal matching shared/signal.schema.json
```

## AdaptiveRiskManager v2 — safety fixes over the reference

- **leverage hard-capped at `max_leverage` (5.0×)** — reference allowed 5.5–6.0×.
- **risk/trade hard-capped at `base_risk_per_trade` (0.75%)** — reference's 1.4× multiplier
  pushed it to ~1.05%. The multiplier now only scales *down*.
- **equity/drawdown circuit breaker** trips `halt` at `max_dd_kill` (15%) → kill-switch + Telegram.
- regime- and `risk_score`-aware shrink; pure stdlib; deterministic.

## Paper → shadow → live ladder (each human-gated)

1. **Paper / historical** — replay signal history (this repo, today).
2. **Paper / live signals** — consume Valor's live stream.
3. **Shadow** — live signals + live marks, **zero orders**, ≥2–4 weeks.
4. **Micro-live** — tiny size, hard daily-loss kill, **only after `/approve`**. No money skips a rung.

## Safety

Paper-only by default; no real-money path exists in this service. Every optimizer
promotion is `requires_human=True` (Telegram `/approve`). All tweaks/decisions are
audit-logged (`.evolver/audit.jsonl`). The optimizer can change **only** whitelisted
strategy params — never the reward function or hard limits (anti reward-hacking).
