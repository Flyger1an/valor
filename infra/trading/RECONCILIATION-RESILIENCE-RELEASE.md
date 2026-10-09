# Paper accounting repair: release and recovery

This revision fixes false split-fill fee overruns, accounts explicitly for the
observed cent-settlement model, reports reconciliation causes, avoids repeated
trade reviews during execution incidents, and checks protection evidence freshness.
It builds on the deployed order-state race repair in main at `9d400a5`.

## Verified before release

- Trading regression suite: 263 tests pass, including real-overrun rejection,
  unknown inventory rejection, one-cent discrepancy rejection, restart replay,
  immutable activity handling, protected exits, and review invalidation.
- Original source reproduced the fee-overrun error using the actual newly
  captured paper-account activities and a disposable copy of the last backup.
- Corrected adapter reconciled the complete capture, then repeated after reopening
  its database. The projection stayed identical. Six fees were accounted for;
  no reserves, positions, ordinary/protective pending orders, or external flows
  remained. No cash-journal or fee-review decision was added or changed.
- The live adapter still rejects construction. Policy, supervisor, initial capital,
  original admission, study clock and raw historical records are retained.
- Private evidence and account/credential identifiers are excluded from Git.

No VPS change or halt clear is part of this local verification. The owner's
automated monitoring instructions prohibit privileged VPS mutation. Obtain
specific approval for deploying this revision and this bounded paper recovery
before using the deployment identity. Do not use the older incident scripts
unchanged: they assert a different pending-order state and permit a provisional
fee bridge that is not sufficient for this recovery.

## Bounded deployment and recovery

1. Compare the current monitor's mode, policy hash, halt, book state, and deployed
   image with the expected paper incident. If the policy or code changed since
   preparation, review the new state first. Do not migrate policy or session limits.
2. Stage this exact revision, hash its Python modules, and build an overlay on the
   current runtime image. Run the trading suite inside that image with network
   disabled before stopping a service. Retain the original image and compose file.
3. Stop the single source-book worker. Save a consistent SQLite backup with the
   backup API, including committed WAL contents; verify integrity and save hashes.
   Preserve all other source/virtual books and original policy, budget and clocks.
4. Use a transport that rejects every broker method except GET. Verify the
   dedicated account identity, paper endpoint, active status, flat broker positions,
   and no broker open orders. Confirm the existing book is flat with no pending
   orders and still halted specifically for `broker_accounting_mismatch`.
5. Force a full admission-period activity scan. On a disposable copy of this new
   backup, run `reconcile(now, protect=False)` with this revision. Require confirmed
   balances, zero provisional fee reserves, no order/activity lag, no external-flow
   pause, and no unknown positions/orders. Preserve original raw prefixes and
   existing review decisions. Repeat and reopen once to check identical projection.
6. Repeat fresh GET-only reconciliation on the stopped source book while retaining
   the halt. Check the same invariants and the pinned account/policy/admission again.
   Require `cash_match_basis=per_fill_cent_settlement` for this incident and record
   the explicit precision difference. If fresh facts differ, leave the halt set.
7. Only within the owner's approved recovery, append a `risk.accounting_halt_cleared`
   event identifying this release and verified cause and clear this specific halt.
   Preserve supervisor authority, risk scale, expiry, model budget, session policy,
   starting cash and live-study clock. Reconcile once more before restarting; on
   failure reassert the accounting halt and retain all newly collected raw facts.
8. Pin the four existing source services to the tested image and restart them.
   Check fresh worker/feed/research timestamps, account reconciliation, protection,
   model connection, news evidence, and pending-order state. No manual test trade
   is needed. Normal paper decisions still require both reviews and all guards.
9. Confirm the forced-command monitor receives the structured diagnostic and fresh
   readiness fields. Verify the separate virtual studies retained their identities
   and histories. Report the exact release and current remaining entry pauses.

If image startup or verification fails, stop the new worker and retain the hard
halt before restoring the original compose/image. Do not overwrite the book with
an older backup that would discard broker events. The original code may remain
halted on the posted fees; report that rather than clearing its guard.

This repair does not resolve incomplete per-symbol news coverage or fallible
model veto explanations. It does not qualify the system for live trading or
promise uninterrupted execution or positive returns.
