# Valor

Valor is a private, local-first crypto relative-value and risk-intelligence dashboard for research, backtesting, paper trading, and guarded future execution. The MVP runs on deterministic sample data so it can be inspected without API keys or secrets.

## Quick Start

```bash
npm install
npm run test
npm run dev
```

Open `http://localhost:3000`.

## What Works

- Local Next.js App Router dashboard with Overview, Signals, Risk Intel, Alerts, Analyst, Backtests, Paper Trading, Settings, and Audit views.
- Alerts view with INFO/WATCH/TRADEABLE/CRITICAL/BLACK severities and Telegram/SMS routing preview.
- Analyst copilot view with env-driven LLM API plug, local RAG context, and offline fallback.
- Sample market-data ingestion with connector interfaces for public or paid adapters.
- Relative-value signal engine covering spot/perp basis, funding carry, cross-exchange premium, BTC/ETH regime, pair z-scores, stablecoin depeg watchlist, and volatility filters.
- Market risk state engine with Green/Yellow/Red/Black states, alerts, and restrictions.
- Basis-carry backtester with fees, slippage, funding, sizing, drawdown, Sharpe, Sortino, win rate, exposure, and turnover.
- Paper broker with signal attribution and risk-limit enforcement.
- Live trading guardrail interface that is disabled by default.
- Telegram command authorization, Twilio SMS provider interface, alert dedupe/cooldowns, and persistent kill switch modules.
- Drizzle SQLite-compatible schema plus initial SQL migration.
- Docker Compose VM stack with app, worker, scheduler, Timescale/Postgres, and Redis services.

## What Is Stubbed

- Exchange, news/RSS, chain, reserve, and ETF data adapters use deterministic sample data by default.
- Live trading has no exchange executor. The only implemented live path is a guardrail evaluation that blocks by default.
- SQLite persistence is wired for market snapshots, signals, risk state, backtests, alerts, and runtime metadata. JSON state remains as a secondary mirror.
- Worker runs periodic refresh/recompute cycles via `npm run worker`. Scheduler remains a placeholder until Redis/BullMQ job consumers are added.
- LLM copilot can run in offline mode without keys; API calls require explicit env configuration.

## Documentation

- [Setup](docs/setup.md)
- [Data Sources and Connectors](docs/connectors.md)
- [Signals](docs/signals.md)
- [Risk Controls](docs/risk-controls.md)
- [Alerts and Telegram/SMS](docs/alerts.md)
- [LLM Analyst Copilot](docs/llm-analyst.md)
- [Backtesting](docs/backtesting.md)
- [VM Deployment](docs/deployment-vm.md)
- [Compliance Cautions](docs/compliance.md)
