# Demo activity reconciliation repair

## Status and scope

This change supports explicit, evidence-bound demo accounting repair. Unit tests
use synthetic Trading API-shaped records; actual broker verification is a separate
operator step. A local copy of the captured incident was reconciled on 2026-10-07,
including duplicate and restart replay, before any production halt clear.
Do not replace the last verified balance with an inferred current balance.
No credentials, raw account records, funding evidence or runtime DBs
belong in Git. Do not reset the book, restart the study clock, submit test orders,
change risk limits, lift the live guard, or alter any virtual book.

## Schema decision

Official sources checked for this change:

- [Alpaca Trading Account Activities](https://docs.alpaca.markets/us/docs/account-activities)
- [Alpaca Crypto Spot Trading Fees](https://docs.alpaca.markets/us/docs/crypto-fees)
- [Alpaca Journals API](https://docs.alpaca.markets/us/docs/funding-via-journals)

The API distinguishes monetary net amounts and asset quantities. Crypto fees are
charged in the received asset: base crypto for buys and USD for USD-pair sells.
The official example has CFEE, negative quantity and zero net amount. It does not
show the incident's cash CFEE. Accepting a negative-net-amount CFEE with no quantity
movement is a defensive extension, still requiring verification against real raw
records. This parser is for Trading API activities, not Broker SSE records.

- Base CFEE requires negative quantity, explicit zero net amount and an approved
  instrument. The actual Trading API base-fee record labels `currency` as USD,
  its account currency. USD or the instrument's base currency is accepted; the
  quantity and symbol establish the asset deducted. Foreign currencies fail closed
- A USD fee requires negative net amount and absent or zero quantity. Explicit
  currency must be USD. Symbol may be USD, absent, or an approved USD instrument
- Conflicting amount fields, rebates, ambiguous zero fees, invalid numbers,
  unsupported currency/assets and non-executed status fail closed
- Existing order-ID attribution remains exact; otherwise allocation is disclosed
  as daily pro rata. Sell-side cash and buy-side base fees stay distinct. No fee
  is applied to whichever position happens to be open when it arrives
- A settlement date can differ from its fill date. No automatic date shifting or
  amount matching is applied. `review_fee_allocation` records an immutable review
  of the exact fee and fill hashes, broker/policy/admission context and private
  evidence digest. Replay verifies its audit event, side, symbol, order reference
  when present, and chronology. The allocation method is explicitly recorded as
  `reviewed_fill_set_pro_rata`; unrelated later fills retain their own reserves
- Fee caps, provisional same-day accruals, lot replay and raw activity immutability
  are retained. Reconciliation stages projections before independent balance
  validation, so failed validation cannot publish guessed balances or P&L

## Cash journals and opening capital

JNLC is a cash journal, not proof of a deposit or profit. Alpaca documents that
JNLC v2 can update balances at execution before creating the activity. A late
journal may therefore describe already admitted capital, but neither its amount,
description, posting delay, date nor an apparently balanced replay proves that.
Day-only activity dates can be settlement dates and cannot establish intraday order.

Unreviewed JNLC remains blocked. `ActivityJournal.review_cash_journal` accepts only
an explicit `opening_capital` or `external_funding` decision for an already-ingested
immutable JNLC. It requires a named reviewer and SHA-256 of a private, verified
evidence packet. The decision is append-only, bound to the canonical activity hash,
book broker/policy identity, admission timestamp, admission-event hash and recorded
starting cash. A conflicting decision raises an error rather than rewriting history.
The evidence digest is an audit pointer, not an automated truth check: the operator
must independently establish the funding provenance described below.

Reviewed opening cash remains represented exactly once in starting capital and
is excluded from net flows and profit. Known opening CSD and reviewed opening JNLC
amounts cannot sum above starting capital. External funding changes cash and net
flows, never trading P&L, and preserves the existing external-cash-flow entry pause.
Unknown corrections and journals whose economic purpose is not demonstrably one
of these two classifications require further review; do not force them into either.
There is no automatic review, halt clearing, resume or policy migration.

## Required private verification, before applying a review

1. Keep the existing demo halt and supervisor state. Verify the exact account,
   paper endpoint, policy identity, deployment revision and hard live block through
   the existing authorized executor. Do not create new credentials or access
2. Make a consistent SQLite backup including committed WAL contents via SQLite
   backup, not an unsynchronized file copy. Save private hashes and baseline counts
   of raw activities, orders/receipts, admission event, existing reviews and ledger
   events. Record virtual-book identities, epochs, journal prefixes and balances
3. Read actual raw activities through the existing read-only broker connection,
   with complete pagination and a full admission-period rescan. Preserve missing
   late activities append-only; do not edit a stored activity or omit unsupported
   types. Compare fee amount, quantity, symbol, currency, date and order links
   against the schema; any different shape stays blocked for another parser review
4. Locate the original broker admission cash evidence and the specific funding
   journal/transfer evidence. Demonstrate whether the exact journal is already
   included in admitted starting cash or is a separate later cash movement. Review
   every opening CSD/JNLC together. Do not decide just because the amount is $500
5. Prepare a private evidence packet containing raw activity IDs/hashes, broker and
   policy identity, admission cash/time, authoritative funding provenance, complete
   scan bounds, independent current account/position/open-order snapshot and its
   capture time. Hash that packet. Do not publish it or its sensitive source data
6. On separate disposable backup copies, compare unreviewed (must block), proposed
   opening-capital, and proposed external-funding interpretations. Only record a
   review supported by authoritative evidence. Invoke `review_cash_journal` with
   the exact activity ID, classification, evidence digest, reviewer and actual
   review time. Never insert invented broker activities or modify opening_activity_ids
7. Replay twice and after reopening the backup. Confirm identical raw activity
   bytes/prefixes; stable review count and projection hash; cash, inventory, pending
   orders, fees, reserves, lot costs and closed-trade P&L; funding distinct from profit;
   no duplicate fees or new-order attribution. Use `rebuild(..., persist=False)` for
   a no-write projection; it still requires all classifications to be verified
   For a settlement-date fee, first establish the exact fill set using the complete
   broker history, fill/order records, fee creation timestamp and price where
   available. Record `review_fee_allocation` with that set and the same verified
   evidence packet. Do not select fills solely because the amounts fit a reserve
8. Compare projected cash and positions to a fresh independent broker snapshot
   using the existing strict cent-precision and instrument-increment checks. Show
   confirmed versus fee-accrual-bridge status honestly. For this incident, require
   confirmed balances, no unexplained fees, no order/activity lag and no external
   cash-flow review pause before proposing a halt clear. If evidence conflicts or
   classification is uncertain, leave the halt in place and report the exact delta

## Applying the verified repair

After offline verification and within the owner's specific demo repair approval,
use the existing deployment procedure for this scoped revision. Stop the single
writer for the evidence-bound review write; retain a restorable private backup and
compare all pinned evidence immediately before writing. Apply the same reviewed
record through the method, not raw SQL. If IDs/hashes/account/policy/admission changed,
stop and regenerate the evidence review rather than weakening the binding.

The additive cash-journal and fee-review tables are created on bind; no existing raw rows or schema columns
are rewritten. No automatic classification is populated by upgrade. Retain the
original opening IDs and all historical events. If rollback is needed, preserve the
new raw activities/review evidence; the old parser will block these records again.

Fresh production reconciliation must pass while the halt stays set. Use a read-only
transport for verification and `reconcile(..., protect=False)` if invoking the adapter:
normal reconciliation's protection stage can create/cancel protective orders. If
there are unknown pending orders or holdings, stop for a separate scoped review.
Only after independently verified confirmed reconciliation may the existing explicit
halt-clear procedure be considered. This change itself never clears the halt or
resumes entries. Preserve the supervisor lease/pause and all other guards. Verify
another fresh reconciliation after any authorized clear without injecting an order.

Finally verify the three virtual books still retain the original epoch, identity,
history prefixes and balances and continue their independent observations. Report
code-tested versus broker-confirmed stages separately, including exact deployed
revision and remaining blockers. Never claim GitHub CI ran without an actual run.

## Offline regression command

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=evolver python3 -m unittest discover \
  -s evolver/tests -p 'test_trading*.py'
```

No unit tests require network or credentials. Coverage includes cash/base fee shapes,
late closed-trade adjustment, duplicate/reordered activities, restart replay,
provisional accruals, fee limits, explicit opening/funding classification, evidence
binding, immutable reviews, settlement-date fee attribution, USD account-currency
labels on base fees, aggregate opening limits and failed projection staging.
