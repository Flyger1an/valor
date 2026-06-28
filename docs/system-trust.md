# System Trust

System Trust is Valor's operational gate for paper and future live actions.

It aggregates:

- Data-quality status, stale data, and fixture fallback.
- Market risk state, especially `Black`.
- Persisted kill-switch state.
- Scheduler consecutive failures.
- Alert delivery failures.
- Paper-ledger drift, including invalid equity, duplicate positions, or notional above configured caps.

The verdict has three statuses:

- `trusted`: paper research can continue under current limits.
- `caution`: paper research may continue, but live execution remains blocked.
- `blocked`: new paper entries and all live attempts are blocked.

Sample fixtures intentionally produce `caution`: local paper research is allowed, but live execution is not trusted from fixture-backed data.

The dashboard shows the verdict in Overview, and `GET /api/ops/system-trust` returns the same structured verdict for scripts or monitors.

System Trust also emits alert events. Fixture-backed sample data is a `WATCH` alert because paper research can continue but live trading cannot. Paper-blocking trust issues become `CRITICAL`, while global halt conditions such as the kill switch or Black market risk become `BLACK`.

The paper broker consumes the verdict directly. When `blocksPaperTrading` is true, new paper entries are rejected with the system-trust reason. Existing position exits still follow the paper ledger lifecycle: data-quality blocks, market risk, source signal disappearance, edge decay, edge policy, signal risk, and max holding time.
