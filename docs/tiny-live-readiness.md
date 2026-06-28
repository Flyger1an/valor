# Tiny-Live Readiness

Tiny-Live Readiness is a v0.2 decision report, not live execution approval.

The evaluator lives in `src/lib/readiness/tiny-live-readiness.ts`.

It asks whether any signal family has earned human review for future tiny-live consideration. It uses:

- Data quality and fixture/fallback status.
- System Trust.
- Edge Scoreboard family evidence.
- Paper ledger evidence window and closed trade count.
- Dry-run execution reconciliation.
- Operational Runbook status.

Statuses:

- `no_go`: blockers remain or no family has enough positive evidence.
- `watchlist`: a candidate may be forming, but warning blockers remain.
- `candidate_review`: a family meets the configured evidence gates and may be reviewed by a human. This is not authorization to trade.

Default minimums:

- 21 evidence days.
- 10 closed paper trades for the candidate signal family.
- 55% candidate win rate.
- Positive candidate PnL after modeled costs.

`GET /api/ops/readiness` returns the same report shown in the dashboard Readiness panel.

`GET /api/ops/evidence-packet?format=markdown` exports readiness with runbook, trust, paper, dry-run, and scoreboard evidence as a review memo.

Sample or fixture-backed data always remains `no_go`.
