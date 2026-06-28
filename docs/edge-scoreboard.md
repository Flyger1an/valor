# Edge Scoreboard

The Edge Scoreboard turns paper-ledger evidence into a signal-family trust view.

Each dashboard refresh aggregates:

- Current generated signals by `SignalKind`.
- Paper-eligible counts from the current signal set.
- Open position count, active notional, mark PnL, funding, hold time, and edge decay.
- Ledger events across fills, holds, closes, and rejections.
- Realized PnL, total PnL, win rate, acceptance rate, and status.

Statuses:

- `insufficient`: not enough paper evidence to trust or reject the family.
- `watch`: some evidence exists, but outcomes are not decisive.
- `proving`: closed paper outcomes are non-negative with at least half winning.
- `underperforming`: closed paper evidence is net negative after costs and marks.

The dashboard shows the scoreboard after Paper Trading, and `GET /api/ops/scoreboard` returns the same structured view for scripts or monitors.

Underperforming families also emit `WATCH` alert events through the `edge-scoreboard` source. The alert records the family, paper PnL, closed-trade count, win rate, and the same recommendation shown in the scoreboard.

## Edge Policy

The scoreboard now gates paper exposure for underperforming families. When a family reaches `underperforming`, Valor marks matching current signals `watch_only`, sets `eligibleForPaperTrading` to `false`, attaches an edge-policy reason, and appends the policy reason to the signal explanation. The paper broker closes existing positions whose refreshed source signal carries that policy state. The scheduler, manual paper-trade endpoint, alert generation, and dashboard all consume the same policy-adjusted signals.

The scoreboard is still paper-only. A family marked `proving` is not live-trade authorization; live execution remains blocked by the live guardrails, kill switch, data quality, risk state, and manual approval requirements.
