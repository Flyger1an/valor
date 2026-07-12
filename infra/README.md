# Infra & cost

*Updated 2026-07-10.*

## Local
```bash
cp evolver/.env.example evolver/.env   # fill keys
docker compose -f infra/docker-compose.yml up --build
```
API :8000 · Streamlit :8501 · MLflow :5001 · Redis :6379 · Postgres :5432 (all bound to 127.0.0.1)

## Cloud — cheapest scalable (target < $70/mo)

| Component | Service | ~Cost/mo |
|---|---|---|
| Always-on api + loop | **ECS Fargate SPOT** 0.25 vCPU / 0.5 GB | ~$6–12 |
| Signal bus | **SQS** (or Upstash Redis free) | ~$0–1 |
| Postgres (ledger + checkpoints) | **Neon free** / RDS `db.t4g.micro` | $0–15 |
| Redis | **Upstash free** / ElastiCache `t4g.micro` | $0–12 |
| Artifacts / MLflow store | **S3** | ~$1–3 |
| Strong-model optimizer | **scheduled Lambda / one-shot Fargate** (rare) | ~$1–5 + tokens |
| **Total** | | **~$35–65** + LLM tokens |

Set an **AWS Budgets** alarm at $70 (in `main.tf`). Keep the always-on footprint to
ONE tiny Fargate-spot task; the optimizer is event/scheduled, never always-on.

### Zero-ops first 2 weeks
Deploy `evolver-api` + `dashboard` to **Railway** or **Render** (~$20/mo, managed
Postgres/Redis add-ons). Move to the Terraform/Fargate setup once the loop proves out.

### LLM cost control
Fast model (gpt-5-mini/Haiku/Grok) on the inner loop, temp/cache tight; strong model
(gpt-5.5/Claude) only on the outer loop (rare). Cap tokens; batch optimization daily.
