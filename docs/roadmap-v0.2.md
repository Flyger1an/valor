# Valor v0.2 Roadmap: Evidence Loop

Valor v0.2 is the path from expensive theatre to something that can earn belief. The goal is not live execution. The goal is a continuous evidence loop that ingests data, records decisions, simulates positions over time, measures realized outcomes, and blocks itself when the evidence or operating conditions degrade.

"Printer" means an audited, repeatable, risk-controlled process with measured edge. It does not mean guaranteed profit, leverage, unmanaged automation, or skipping legal, tax, venue, custody, or compliance obligations.

## North Star

By the end of v0.2, Valor should be able to answer four questions without hand-waving:

- Can the system trust its current data?
- Which signals did it produce, accept, reject, and why?
- What happened after simulated positions were opened?
- Which signal families are proving or disproving their expected edge?

## Non-Goals

- No autonomous live trading.
- No leveraged live positions.
- No custody, transfer, or withdrawal automation.
- No outside-money workflows.
- No LLM authority over risk, sizing, execution, or kill switches.
- No scaling claims based on sample data or single-period backtests.

## Milestone 0: Baseline Integrity

Purpose: make the current app deterministic, testable, and safe to extend.

Work:

- Fix connector defaults so missing `ENABLE_PUBLIC_MARKET_FETCH` uses deterministic sample data.
- Add tests for connector mode selection.
- Make state-changing API routes return explicit action summaries and audit entries.
- Keep all live-trading guardrails blocked by default.
- Commit the current repo baseline before deeper changes.

Acceptance gates:

- `npm test` passes.
- `npm run build` passes.
- No environment variable means sample-data mode.
- Live-trade evaluation remains blocked without explicit opt-in plus manual confirmation.

## Milestone 1: Data Trust Layer

Purpose: ensure every signal starts with inspectable data quality.

Work:

- Add a `DataQualityReport` domain type.
- Validate freshness, missing fields, crossed books, abnormal spreads, zero liquidity, fallback usage, and source timestamp drift.
- Persist raw or normalized market snapshots with connector id, source timestamp, ingest timestamp, and quality status.
- Generate risk alerts when data is stale, incomplete, or fixture-backed during a live/public-data session.
- Add a dashboard system-health panel.

Acceptance gates:

- Every refresh produces a data-quality report.
- Stale or invalid data blocks new paper entries.
- Fixture fallback is visible in dashboard, alerts, and audit log.
- Tests cover stale data, bad books, and fallback behavior.

## Milestone 2: Durable State and Audit

Purpose: move core state out of `.valor/state.json` and into durable storage.

Work:

- Add a database access layer around the existing Drizzle schema.
- Persist market snapshots, signals, risk states, paper trades, alert events, alert deliveries, kill-switch changes, and audit events.
- Keep local JSON only as a development fallback.
- Add idempotent write patterns for repeated refreshes.
- Add migration notes for local SQLite and Docker Postgres/Timescale.

Acceptance gates:

- Restarting the app reconstructs dashboard state from the database.
- Re-running the same refresh does not duplicate logical events.
- Audit trail records refreshes, signal generation, paper decisions, alert routing, and kill-switch changes.

Current status: local SQLite storage is implemented for file-backed `DATABASE_URL` values, with JSON fallback for unsupported Postgres URLs. Restart-recovery tests now verify that SQLite-backed state can be closed, reopened, and compared against pre-restart evidence without losing core dashboard state. A Postgres adapter is still pending.

## Milestone 3: Continuous Scheduler

Purpose: turn Valor from a dashboard into a recurring decision machine.

Work:

- Replace scheduler placeholder with a loop: ingest data, compute risk, generate signals, step paper ledger, route alerts, persist results.
- Add interval configuration and a run lock to prevent overlapping cycles.
- Add run status, duration, error count, and last-success timestamp.
- Add worker boundaries for alert delivery and future queue consumers.

Acceptance gates:

- Scheduler runs for a multi-hour local session without duplicate cycles.
- Failures are recorded and visible in the dashboard.
- A failed connector does not crash the process silently.
- Alerts are deduped across cycles.

Current status: scheduler cycles now run through `POST /api/ops/scheduler`, persist status, refresh data, store paper preview state, and keep alert sends opt-in. Scheduler status now records active run ids, heartbeats, skipped overlaps, and stale-run recoveries so unattended operation is easier to audit. `GET /api/ops/health` adds a read-only deployment health report for droplet probes, and `npm run soak:scheduler` provides a short pre/post-deploy soak harness. Queue-backed worker boundaries are still pending.

## Milestone 4: Paper Ledger v2

Purpose: replace one-shot paper preview with a position lifecycle.

Work:

- Persist paper positions across refreshes.
- Add entry, hold, reduce, close, and reject decisions.
- Mark positions to market on every cycle.
- Accrue estimated funding for perp-related strategies.
- Close positions when edge decays, risk state worsens, data quality fails, or max holding period is reached.
- Record realized PnL, unrealized PnL, fees, slippage assumptions, funding, and exit reason.

Acceptance gates:

- A paper position can open, update, and close across separate scheduler runs.
- Every rejected signal has a reason.
- Paper PnL changes only from explicit marks, funding, fees, or closes.
- Tests cover open, reject, mark, close, and risk-forced exit flows.

Current status: first lifecycle cut is implemented. Paper positions open, hold, mark, accrue estimated funding, close on edge/risk/data rules, and record `filled`/`held`/`closed`/`rejected` ledger events. Paper marks now prefer current market reference prices, ratios, or spreads when the refreshed market bundle can identify a matching instrument; edge-proxy marks remain the fallback.

## Milestone 5: Edge Scoreboard

Purpose: make signal quality measurable.

Work:

- Aggregate performance by signal kind, venue, asset pair, and risk state.
- Track generated count, accepted count, rejected count, average expected edge, realized PnL, win rate, drawdown, average hold time, and edge decay.
- Compare expected edge versus realized paper edge.
- Add a dashboard panel and API route for signal-family performance.
- Flag underperforming signal families.

Acceptance gates:

- Dashboard shows evidence by signal family.
- Underperforming signal families can be marked watch-only.
- Realized paper outcomes are traceable back to original signal ids.
- Tests cover scoreboard aggregation and underperformance flags.

Current status: first scoreboard cut is implemented. Dashboard state aggregates signal-family evidence from current signals, open positions, filled/held/closed ledger events, and rejected signals. `GET /api/ops/scoreboard` exposes the same view for automation. Underperforming families now feed back into signal eligibility so weak families become watch-only before scheduler or manual paper entries, and existing paper positions close when their refreshed source signal carries that policy state.

## Milestone 6: Risk Control Hardening

Purpose: make the system safer as it becomes more autonomous.

Work:

- Add system-trust restrictions for stale data, fallback data, scheduler failures, alert delivery failures, and paper ledger drift.
- Expand kill-switch behavior so it blocks new paper entries as well as any future live attempt.
- Add risk-policy tests for Green, Yellow, Red, and Black behavior.
- Add explicit alerts for data trust, system health, and signal underperformance.

Acceptance gates:

- Black state blocks new paper entries.
- Stale or invalid data blocks new paper entries.
- Kill switch state survives restart.
- Risk restrictions explain exactly what is blocked and why.

Current status: system-trust gate and trust alerts are implemented. The gate aggregates data quality, fixture fallback, Black risk, kill switch, scheduler failures, alert delivery failures, and paper-ledger drift. The paper broker rejects new entries when system trust blocks paper mode, live evaluation includes the trust verdict, and the dashboard surfaces trust status in Overview. System trust now emits `WATCH`, `CRITICAL`, or `BLACK` alert events, and underperforming edge-scoreboard families emit `WATCH` alerts that match the watch-only edge policy.

## Milestone 7: Dry-Run Execution Interface

Purpose: prepare for future live execution without placing orders.

Work:

- Define exchange executor interfaces for balances, order preview, order placement, cancellation, and fills.
- Implement a dry-run executor that records order intents but never calls live order endpoints.
- Add pre-trade checks for venue, asset, notional, leverage, daily loss, data trust, risk state, kill switch, and manual confirmation.
- Add reconciliation stubs for future real fills.

Acceptance gates:

- Dry-run execution creates auditable order-intent records.
- No implementation can place a live order.
- Existing live-trading tests still prove default blocking.
- Manual confirmation is required for every dry-run execution attempt.

Current status: dry-run executor and reconciliation cut are implemented. Valor has executor interfaces for balances, preview, order intent recording, cancellation, and dry-run fills. `POST /api/ops/dry-run-execution` records guarded order intents, `GET /api/ops/dry-run-execution` returns attempts plus reconciliation status, the dashboard shows the dry-run execution ledger, and SQLite persists normalized attempt rows. Manual confirmation is enforced by the executor even if env settings try to relax it. There is still no live exchange executor.

## Milestone 8: Tiny-Live Readiness Review

Purpose: create a decision gate for v0.3, not live execution in v0.2.

Work:

- Produce a readiness report from the edge scoreboard and paper ledger.
- Define minimum evidence thresholds before any tiny-live work starts.
- Document operational runbooks for stop, resume, failed data, failed alerts, and position drift.
- Decide which one signal family, if any, has earned dry-run-to-tiny-live consideration.

Acceptance gates:

- At least several weeks of continuous paper results are available.
- Signal performance is positive after costs and stress assumptions.
- Drawdown and failure behavior are understood.
- A human-readable go/no-go memo exists.

Current status: first operational runbook and tiny-live readiness layers are implemented. Valor now evaluates stop/resume, failed data, failed alert, scheduler, paper drift, and dry-run execution drift procedures from current system evidence. The dashboard shows the active operator runbook, and `GET /api/ops/runbook` exposes the same report for scripts or monitors. Valor also produces a Tiny-Live Readiness report from data trust, system trust, edge-scoreboard evidence, paper history, execution reconciliation, and runbook status. `GET /api/ops/readiness` exposes the readiness report, and sample or short-history evidence remains `no_go`. `GET /api/ops/evidence-packet` now exports a redacted operator case file as JSON or Markdown.

## Suggested Build Order

1. Baseline integrity.
2. Data trust layer.
3. Durable state and audit.
4. Continuous scheduler.
5. Paper ledger v2.
6. Edge scoreboard.
7. Risk hardening.
8. Dry-run execution interface.
9. Tiny-live readiness review.

## First Implementation Slice

The first useful slice should be small enough to finish without redesigning the whole app:

- Fix connector default behavior.
- Add `DataQualityReport`.
- Add tests for sample-mode default and stale-data detection.
- Persist refresh metadata in the local store or database abstraction.
- Add a dashboard system-health section showing connector mode, generated time, data age, and quality status.

That slice turns the current app into a safer foundation for the rest of v0.2.

## Success Metric

Valor v0.2 succeeds when it can run unattended in paper mode, produce a durable audit trail, explain every simulated decision, measure realized edge by signal family, and block itself when data, risk, or system health is not good enough.
