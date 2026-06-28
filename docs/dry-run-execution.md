# Dry-Run Execution

Dry-run execution is Valor's first executor boundary for future live work. It records order intents and guardrail outcomes without initializing an exchange client or calling any venue API.

The implementation lives in `src/lib/execution/dry-run-executor.ts` and exposes:

- Local balance inspection for synthetic dry-run balances.
- Order preview with deterministic fee and slippage estimates.
- Order intent recording through the existing live guardrails.
- Cancel and fill interfaces for future executor parity.

`POST /api/ops/dry-run-execution` selects a signal, applies edge policy, evaluates system trust, evaluates live guardrails, and records a `LiveTradeAttempt`. Blocked attempts are kept because they are part of the evidence trail.

`GET /api/ops/dry-run-execution` returns recorded attempts, synthetic dry-run balances, and a reconciliation report.

The dry-run executor only records a successful intent when existing guardrails pass, `LIVE_TRADING_DRY_RUN` remains true, and the attempt carries manual confirmation. This confirmation is required even if environment settings try to relax live confirmation. It cannot place a live order.

Reconciliation checks that dry-run attempts and fills remain internally consistent: no non-dry-run attempts, no fills on blocked attempts, no allowed attempts without synthetic fills, and no fill notional drift.

The dashboard shows recorded attempts in the Dry-Run Execution section. SQLite stores the normalized attempt row in `live_trade_attempts`, while the state snapshot keeps the richer preview, reason, and dry-run fill detail.
