# Candle evidence and prospective recovery

The feed preserves accepted closed candles when the provider revises an older
observation. It archives the original history and each distinct revised or
invalid observation in `market/history-evidence/<sha256>.json`, then continues
collecting valid later bars. It never substitutes revised prices into accepted
history or rewrites a recorded decision.

A new candle is provisional until two numerically identical observations at
least 60 seconds apart agree. Every distinct provisional version is retained;
provisional bars cannot support orders or approvals. Pending observations survive
feed restarts in `history.json` and are excluded from `signals.json`.

A revision of accepted history starts a recovery boundary at the next five-minute
boundary (the current boundary if observed exactly on it). An existing quarantine
also starts a boundary on first adoption. Repeated revisions of observations
before that boundary do not keep moving it. Another incident within the new
segment starts a new boundary. Invalid observations are recorded and excluded.
If evidence cannot be saved, publication fails closed.

The feed exposes only the latest uninterrupted segment after the boundary. It
does not interpolate missing candles. A recovering asset remains quarantined
until the shortest approved strategy has enough observations; every consumer
still checks its strategy's complete window and freshness. The current catalogue
minimum is 61 closed bars; the initial EMA strategy needs 64. Recovery therefore
needs at least 5 hours 20 minutes of fresh five-minute bars plus observation
confirmation for that EMA, and longer if there are gaps or further revisions.

## Supervisor and trade evidence

Immutable requests include per-asset history status, exact window hash and
boundaries, two recent closed candles, computed indicators, entry/exit signals,
and any deterministic candidate with its current execution blocker. A supervisor
pause does not hide a candidate. It also does not authorize one.

The complete calculation windows are retained under
`outbox/candle-evidence/<sha256>.json`; the request identifies that archive.
Model prompts omit the duplicate binding map already present in the per-asset
cards. Exact-trade prompts include that asset's card while keeping portfolio,
policy, outcomes, costs, and complete selected news evidence. Requests retain
the complete original evidence. Existing input and spend limits still apply.

An unanswered or positive supervisor request, exact-trade approval, or strategy
promotion cannot survive a change in its supporting history. Invalidation records
live in `outbox/invalidations/`; original request and response files remain intact.
A final history check runs immediately before an approved order is submitted.
An accepted positive supervisor lease is paused if its usable history loses
provenance. A later ordinary candle does not by itself expire an accepted lease.
Protective exits remain independent of model response time and history warm-up.

Research archives an earlier assessment when its policy or history generation
changes, reports `blocked_history_integrity` while quarantined, and starts a new
prospective selection cohort. Promotion reviews bind the source, generation,
reviewed endpoint, and candle prefix hash. Both market projections must agree.

## Virtual books and rollout

New experiment frames carry monotonic recovery cutoffs and source generations.
The books preserve their original identity, epoch, journal, past decisions, and
balances. New decisions use contiguous history after the cutoff; pending buys
crossing a boundary or losing their usable window are canceled. Protective exits
remain available. Frames recorded before this change retain their original replay
semantics; no earlier journal entries are reclassified.

Roll out matching versions of `feed`, `worker`, `agents`, `research`, and
`experiment-ledgers` together. Before activation, verify the unchanged paper/demo
policy, current images, absence of a concurrent deployment, existing supervisor
pause, and no positions or pending orders. Back up source/experiment databases,
market files, review requests/responses, and research artifacts consistently;
record journal prefixes and study identity. Image validation runs without network,
credentials, or runtime-volume mounts. Activation requires separate approval.

After activation, verify all services, evidence archives, request bindings,
invalidation behavior, preserved accounting and study identity, and clean-bar
warm-up. No approval, trade, risk-scale change, or strategy promotion is injected
as an acceptance test. Existing news, spread, liquidity, exposure, loss, session,
budget, and broker-reconciliation gates remain in force.

Do not roll back to consumers that ignore recovery metadata after new boundaries
have been recorded. Preserve the evidence and use a reviewed forward repair. A
controlled stop while flat is preferable to discarding boundaries or restoring
older databases. Dashboard, notifier, news, monitor, and watchdog services do not
need this code change.
