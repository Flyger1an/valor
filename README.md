# Valor

Valor is a private, local-first crypto relative-value and risk-intelligence dashboard for research, backtesting, paper trading, and guarded future execution. The app can run on deterministic sample data for inspection, and can explicitly opt into public crypto market fetches for live paper-mode evidence.

## Quick Start

```bash
npm install
npm run test
npm run dev
```

Open `http://localhost:3000`.

## What Works

- Local Next.js App Router dashboard with Overview, Signals, Risk Intel, Alerts, Analyst, Backtests, Paper Trading, Edge Scoreboard, Settings, and Audit views.
- Alerts view with INFO/WATCH/TRADEABLE/CRITICAL/BLACK severities, trust/edge alerts, and Telegram/SMS routing preview.
- Analyst copilot view with env-driven LLM API plug, local RAG context, and offline fallback.
- Market-data ingestion with deterministic fixtures by default and explicit public live modes for OKX/Binance/CoinGecko spot/perp prices, stablecoin pegs, chain fees, and z-score history reconstruction.
- Relative-value signal engine covering spot/perp basis, funding carry, cross-exchange premium, BTC/ETH regime, pair z-scores, stablecoin depeg watchlist, and volatility filters, with edge estimates net of conservative execution costs and ADF stationarity gating for mean-reversion entries.
- Market risk state engine with Green/Yellow/Red/Black states, alerts, and restrictions.
- Basis-carry backtester with fees, slippage, funding, sizing, drawdown, Sharpe, Sortino, win rate, exposure, and turnover.
- Paper broker with signal attribution, position lifecycle, risk-limit enforcement, and signal-family edge scoreboard.
- Dry-run execution interface that records guarded order intents while live trading remains disabled by default.
- Operational runbook and tiny-live readiness reports that turn evidence into operator procedures and no-go/candidate-review memos.
- Operator Evidence Packet API that exports readiness, runbook, trust, paper, dry-run, and scoreboard evidence as JSON or Markdown.
- Telegram command authorization, Twilio SMS provider interface, alert dedupe/cooldowns, and persistent kill switch modules.
- Drizzle SQLite-compatible schema plus initial SQL migration.
- Docker Compose VM stack with app, worker, scheduler, Timescale/Postgres, and Redis services.
- Existing Evolver research and deployment stack preserved under `evolver/` and `infra/`.

## What Is Stubbed

- News/RSS, reserve, ETF, and exchange-health adapters still use deterministic sample data by default.
- Live trading has no exchange executor. The only implemented live path is a guardrail evaluation that blocks by default.
- Queue-backed worker boundaries are still pending; the scheduler drives the v0.2 evidence loop through the app API.
- LLM copilot can run in offline mode without keys; API calls require explicit env configuration.

## Documentation

- [Setup](docs/setup.md)
- [Data Sources and Connectors](docs/connectors.md)
- [System Trust](docs/system-trust.md)
- [Signals](docs/signals.md)
- [Risk Controls](docs/risk-controls.md)
- [Alerts and Telegram/SMS](docs/alerts.md)
- [Paper Ledger](docs/paper-ledger.md)
- [Edge Scoreboard](docs/edge-scoreboard.md)
- [Dry-Run Execution](docs/dry-run-execution.md)
- [Operational Runbook](docs/operational-runbook.md)
- [Tiny-Live Readiness](docs/tiny-live-readiness.md)
- [Operator Evidence Packet](docs/operator-evidence-packet.md)
- [LLM Analyst Copilot](docs/llm-analyst.md)
- [Backtesting](docs/backtesting.md)
- [Valor v0.2 Roadmap](docs/roadmap-v0.2.md)
- [VM Deployment](docs/deployment-vm.md)
- [Compliance Cautions](docs/compliance.md)
