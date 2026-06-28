# Paper Ledger

Paper Ledger v2 turns paper trading from a one-shot preview into a position lifecycle.

## Cycle Behavior

Each scheduler or manual paper-trade cycle:

- Marks existing open positions against current market reference prices when available, with signal-edge proxy marks as fallback.
- Accrues estimated funding for `spot_perp_basis` and `funding_carry` positions.
- Holds positions when edge and risk remain acceptable.
- Closes positions when data quality blocks entries, market risk blocks paper trading, edge policy marks the family watch-only, source signals disappear, edge decays, signal risk rises, or max holding time is reached.
- Opens new positions only for eligible signals that are not already open and were not closed earlier in the same cycle.
- Records ledger events with `filled`, `held`, `closed`, or `rejected` status.

## Current Assumptions

- Funding accrual is estimated from current expected edge and elapsed holding time.
- Market-price marks use the current instrument, ratio, or spread reference captured from the latest market bundle. If a reference is missing, Valor falls back to edge-proxy marks.
- Fills remain simulated and use the existing fee model.
- Closed positions realize current mark PnL into paper cash.
- Open-position marks are not exchange reconciliations.
- Same-cycle close and reopen is blocked to avoid churn.

## Risk Controls

New paper entries are blocked by:

- Data-quality `blocked` status.
- Risk states outside the paper allowlist.
- Signal paper ineligibility.
- Signal risk above the configured limit.
- Liquidity below the configured minimum.
- Portfolio notional cap.

Open positions are closed by:

- Data-quality `blocked` status.
- Risk states outside the paper allowlist.
- Edge policy watch-only state from an underperforming signal family.
- Signal disappearance.
- Edge decay.
- Signal risk crossing the exit threshold.
- Max holding period.
