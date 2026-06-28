# Operational Runbook

The Operational Runbook turns Valor's trust, data, alert, paper, scheduler, and dry-run evidence into operator procedures.

The evaluator lives in `src/lib/runbook/operational-runbook.ts`.

It currently covers:

- Stop/resume gates from system trust and the persisted kill switch.
- Failed or degraded data recovery.
- Failed alert delivery recovery.
- Scheduler retry or stop procedures.
- Paper position drift review.
- Dry-run execution reconciliation drift.

`GET /api/ops/runbook` returns the same runbook report shown on the dashboard.

Statuses:

- `ready`: no active operator action is required for paper-mode operation.
- `attention`: review is required before readiness can advance.
- `blocked`: at least one stop condition blocks paper or live progression.

The runbook does not authorize live trading. It documents the next required operator action and the evidence needed to verify recovery.
