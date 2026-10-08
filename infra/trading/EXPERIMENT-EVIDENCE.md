# Forward daily evidence correction: robust-quarter-kelly-v2

## Diagnosis

The producer polls approximately every five seconds. Its file-level `timestamp`
is response receipt time; each quote retains Alpaca's separate provider `t`.
The parser and that separation are correct. The latest-quote endpoint provides
the latest bid/ask, without promising a new event time on every poll. See the
[endpoint](https://docs.alpaca.markets/us/reference/cryptolatestquotes-1) and
[quote schema](https://docs.alpaca.markets/us/docs/real-time-crypto-pricing-data).

Observed producer samples repeatedly returned unchanged ETH timestamps/prices
while receipt timestamps advanced normally. New provider events arrived promptly,
but some successive event timestamps were separated by several minutes.

This supports sparse provider updates rather than a local polling/parser failure.
It cannot distinguish an unchanged venue quote from a missing provider update or
prove an old quote executable. **Receipt time never refreshes a price.** No feed,
broker setting or execution freshness guard is changed. Every book retains its
existing fresh-quote conditions for entry, fills and protective exits.

The v1 evidence rule incorrectly treated every stale quote as unknown daily wealth,
even when Baseline held only cash. It discarded continuously observed cash days
because an unused asset quote aged.

## Meaning and eligibility of a v2 daily observation

The estimand remains the forward virtual Baseline policy's paired daily BTC/ETH
net marked P&L divided by its declared day-start position allowance. It includes
actual abstentions under stale prices, unavailable news or paused supervision.
It is not the return of an always-executable market strategy.

An eligible observation requires:

- A full UTC day after the explicit v2 activation day.
- The unchanged maximum 60-second observation gap, an opening observation within
  30 seconds of midnight and a closing observation within the last 30 seconds.
  This is a sampling bound, not a complete market tape.
- Valid boundary valuations: any held Baseline inventory needs a quote no older
  than 30 seconds at that boundary, known before it. Cash/zero inventory requires
  no market price. Unknown endpoints invalidate the entire paired vector.
- Settled modeled fees and evidence available before the sizing decision.

Intraday stale quotes remain recorded and block executions. They do not alone erase
a known daily cash/endpoint result: daily net P&L is the difference between two
known ledger valuations, including recorded fills and fees. Losing exits following
stale intervals remain included when both boundaries are valued. A fresh quote
after midnight cannot repair an unknown prior closing value. Entirely missing
calendar days remain visible, never converted into zero returns.

There is no price interpolation, fictional fill, imputed unknown P&L, inflation by
the fresh-observation percentage, or selection of favorable intraday slices.
Observed cash-only days contribute their actual zero return, but cannot satisfy
the active-day/positive-edge checks by themselves. Endpoint-invalid days remain
excluded and counted; missingness may depend on market conditions, so this is not
an unbiased estimate of unrestricted market returns or proof of an edge.

All numerical settings remain unchanged: 30 usable days, 10 days with Baseline
entry fills for an eligible asset, at most 60 usable days, the same shrinkage,
uncertainty penalty and stress scenarios, quarter fraction and execution caps.

## Version boundary and preservation

`upgrade-evidence` appends one `evidence_policy_update` event with old/new versions,
activation time and exact rules hash. It retains the prior open-day description
and old blocks. Epoch, deadline, immutable identity, execution policy, balances,
lots, fills and prior journal events remain intact. Repeating it is idempotent.

The activation day is ineligible. No historical block is reclassified and v1 days
never enter the v2 estimator. The first potential full v2 day starts at the next
UTC midnight; the common 90-day deadline does not move. Restarts retain this
boundary. Use a v2-capable release afterward, not a pre-upgrade binary.

Snapshot `rules` retains the original identity's rules. `active_evidence_policy`,
Kelly's `policy_version` and `evidence_policy_history` identify the effective
version. Quality reports contain observation counts, fresh-pair fraction, stale
and held-stale counts by asset, maximum quote ages, gaps, missing days, boundary
valuations and invalidity reasons. `latest_input_provenance` separates receipt
times from provider execution clocks. Coverage fractions describe observations,
not profitability or a guarantee of continuous executable liquidity.

Upgrade only with the experiment's single-writer lock released, a consistent
backup and successful replay before/after. Restart only this isolated service;
the existing broker runtime and original live-study clock remain untouched.
