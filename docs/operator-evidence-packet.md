# Operator Evidence Packet

The Operator Evidence Packet is a deterministic v0.2 review artifact. It gathers the current readiness memo, operational runbook, system trust, dry-run reconciliation, edge scoreboard, paper ledger, imported Evolver soak evidence, and backtest summary into one redacted case file.

It is not live execution approval.

## API

`GET /api/ops/evidence-packet` returns a JSON packet:

- `decision`: `no_go`, `watchlist_review`, or `candidate_review`.
- `readiness`: tiny-live status, minimums, candidate summary, memo conclusion, and required next evidence.
- `controls`: data quality, system trust, runbook, dry-run reconciliation, kill switch, and scheduler posture.
- `evidence`: aggregate paper, edge-scoreboard, imported Evolver soak, Evolver recovery-plan, signal-family, and backtest evidence.
- `blockers`: readiness, runbook, system-trust, execution, and imported-evidence blockers.
- `nextActions`: deduplicated operator actions.
- `attestations`: guardrails that must remain true for v0.2 review.

`GET /api/ops/evidence-packet?format=markdown` returns the same packet as a Markdown memo for review or archival.

`GET /api/ops/evolver-evidence` returns the imported Evolver soak report directly, including `recoveryPlan` thresholds for required PnL recovery, evidence-window/trade-count gaps, win/convergence improvement, calibration haircut, and families to bench. Configure `VALOR_EVOLVER_EVIDENCE_DIR` to point at the live evidence directory. Imported soak can add blockers when shadow PnL, calibration, or research-gate evidence is poor, but it does not satisfy v0.2's own paper-ledger minimums by itself.

## Guardrails

The packet is aggregate and redacted. It does not expose full balances, account identifiers, private labels, addresses, custody details, or secrets.

The packet cannot authorize live trades, size orders, override deterministic controls, or replace legal, tax, venue, custody, or compliance review.
