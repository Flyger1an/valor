# Strict quote admission and reviewed virtual-book recovery

The feed rejects an entire batch before replacing `quotes.json` if a provider
timestamp is ahead of its local receipt, behind the last accepted timestamp, or
has changed numeric bid/ask values at the same timestamp. Receipt clocks cannot
rewind, and source, venue, policy, and instrument identity must match. No tolerance
or replacement timestamp is used. Stale, unchanged quotes keep their provider
timestamps and remain subject to the existing execution freshness gates.

The last accepted file carries per-asset watermarks, including absent assets,
across feed restarts. Immutable `market/quote-rejections/<hash>.json` records retain
the rejected batch, prior publication, receipt clocks, and per-asset violations.
Each record is bounded to 250 KB and the archive to 64 MiB. A failed or full archive
leaves publication blocked; it never deletes evidence or refreshes the previous
receipt. The existing reducer guards remain a second boundary.

New reducer failures retain the complete rejected event and prior state hash in
the failure journal transaction. Older failures replay exactly as recorded. A
missing original frame is never reconstructed. The runner idles on a durable
halt without refreshing observations; its stale/unhealthy status remains visible.

## Explicit recovery command

`experiment_runner recover-quotes --root <experiment-root> --source-root
<existing-source-root> --policy <existing-policy> --recovery-review <review.json>`
is a local operator command, protected by the existing single-writer lock. It is
never invoked by capture, model output, a feed update, or a process restart.

The review supplies `id`, `reviewed_by`, `reason`, `original_cause`, `evidence_hash`,
`identity_hash`, `policy_hash`, `failed_state_hash`, and `failure`. The failure
object contains the exact final journal row's `seq`, `id`, `hash`, `reason`, and
`rejected_hash`. The evidence hash identifies the retained incident review. A
legacy failure without its rejected frame requires
`original_cause: unknown_original_frame_not_retained`.

Admission requires a recognized quote-integrity halt, the existing expanded
evidence policy, paper/demo mode, an unchanged identity/policy, time before the
original deadline, and untouched cash-only books: no fills, lots, positions,
pending orders, capital changes, or book-level risk halts. Every asset needs a
fresh, monotonic quote from the existing source. A bad review rolls back without
replacing the original halt or adding a failure. An identical committed event is
idempotent; a changed review cannot reuse its event identity.

The resulting `operator_quote_recovery` event appends to the original journal.
Replay checks its binding to the immediately preceding failure and halted state.
The reducer preserves balances, books, limits, identity, epoch, endpoint, previous
completed evidence blocks, and old observations. It creates no market frame,
settlement, signal, or order. The affected day is explicitly incomplete, the next
real observation records the outage, and entry history must start after the next
five-minute boundary. This cutoff is separate from feed revision cutoffs, so a
feed cutoff of zero cannot erase it. Fresh reviewed quotes also become a strict
watermark for the first resumed observation. Future bad quotes still halt.

## Release checks and rollback boundary

Run the full trading suite and isolated image tests. Replay a consistent copy of
the actual journal before recovery, append the reviewed recovery on that copy,
accept a captured real frame, and replay again. Verify the original journal prefix,
capital, identity, deadline, completed blocks, and source accounting are unchanged.
Verify a new bad quote still halts on a disposable copy.

Before activation, stop only the affected writers and retain consistent database
backups, journal-prefix digests, current market inputs, and deployment definitions.
Do not activate against a changed incident or failed preflight. Before any recovery
is committed, definitions may be restored while the original halted experiment
stays stopped. After recovery is committed, never run an older image that does not
understand the new event, and never restore an old database over the journal. A
failed rollout then leaves affected writers stopped for diagnosis or roll-forward
repair. Retain all rejection and recovery evidence.
