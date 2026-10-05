# Six-candidate PAPER/DEMO universe

`policy.demo.json` allows ADA-USD, BTC-USD, ETH-USD, SHIB-USD, SKY-USD and WIF-USD.
The four additions passed the [prospective quote-freshness scan](USD-UNIVERSE-ASSESSMENT.md).
Membership makes a symbol eligible for the normal signal and approval pipeline;
it does not force an order or promise a fill. The original spread (20 bps), quote
age (30 seconds), $10 minimum, catalog increments, volume participation, shared
cash/exposure/loss limits, daily order count, session and approval rules remain.
The policy differs from `policy.demo.initial.json` only in `allowed_instruments`.
Real-money execution remains blocked by the adapter.

The source runtime shares one bankroll and its existing account limits across
all six symbols. Each fictional book also has one bankroll: adding symbols does
not multiply its cash, exposure allowance or daily loss budget. Baseline and
Kelly retain their shared caps and supervision. Henry retains his explicitly
different experiment rule: one cash-funded position, selected in lexical order
from currently eligible signals, with no leverage, top-ups or future ranking.

## Data and execution

Alpaca US venue identity is recorded in quotes and bars. Quotes refresh separately
from background historical-bar requests; those requests use bounded batches and
incremental windows after bootstrap. Closed-bar revisions still fail closed.
Catalog minimum quantity, quantity increment and price increment are checked.
Entry limits round down within the existing slippage bound; later simulated buys
round adversely up to the price grid and must still fit that original limit.
Unchanged quotes can therefore produce an unfilled order where grid rounding
crosses the limit. Sells round down. No synthetic favorable fills are introduced.

An unavailable or stale unused symbol does not prevent another fresh symbol from
passing its checks. Fresh held-inventory marks remain required for new risk;
stale holdings make portfolio valuation unknown. Protective execution is never
invented when its quote is unavailable. Dashboards show individual quote ages
separately from portfolio valuation. Missing history has zero modeled capacity.
The virtual 1% preceding-bar-volume rule is still a conservative participation
proxy, not measured executable depth. Sparse venue volume and wide spreads can
keep an admitted symbol idle for long periods.

## Prospective identity and evidence boundary

The source ledger receives an append-only `policy.universe_changed` event and a
new active policy pin. Its accounting and supervisor state are preserved. The
experiment's root identity, original epoch, deadline and journal prefix remain
unchanged; a `universe_policy_update` event records the active symbols, policy,
activation time and cohort. Earlier daily classifications remain as recorded.
The boundary day is partial and excluded. New six-symbol evidence is collected
forward, without filling new assets' history with hypothetical zero returns.

Kelly v3 requires 30 usable synchronized daily blocks and 10 active blocks for
an eligible asset. It never pools earlier BTC/ETH cohorts. It computes one joint
recommendation per frame using the same shrinkage, uncertainty penalty, adverse
joint scenario and quarter fraction. Held/pending exposures stay fixed. The
deterministic 5% grid ascent is bounded to 400 improving steps and is not a global
optimality guarantee. Insufficient evidence leaves new allocations in cash.

## Migration procedure for an existing authorized deployment

1. Build and test the new images before stopping writers. Read current state;
   use a flat, reconciled source account with no pending orders for this rollout.
   Never flatten holdings, override a supervisor pause or reset state to migrate.
2. Stop only the affected writers and take consistent SQLite backups. Record
   hashes of historical event prefixes, accounting, identity, epoch/deadline,
   supervisor, evidence blocks and Telegram delivery state. Replay a copy first.
3. Call `universe.migrate_ledger(path, old_policy, new_policy, boundary_time)`.
   It rejects removals, live mode and changes to any non-universe policy field.
4. Apply the experiment boundary while its writer is stopped:

   ```sh
   PYTHONPATH=evolver python3 -m evolver.trading.experiment_runner expand-universe \
     --policy infra/trading/policy.demo.initial.json \
     --new-policy infra/trading/policy.demo.json --root /existing/experiment
   ```

   Reopen with the new policy and verify replay. Do not initialize a fresh study.
5. Migrate the notifier with `telegram_alerts.migrate_config`. Change only its
   source policy/symbols and pin `acceptance_identity_policy_hash` to the original
   policy. Keep bot, recipient, credentials, cursor, outbox and deduplication IDs.
   Update only the dashboard's source-policy pin; retain its experiment identity,
   epoch and access settings. Replace the runtime policy and pinned images.
6. Restart the affected services. Confirm all six symbols are observed, replay
   still agrees, old event prefixes/accounting persist, all four health checks
   recover and unrelated services retain their IDs/start times. Verify private
   dashboard access and ordinary notifier health without test orders or pings.

The research classification starts a new prospective cutoff and archives its old
assessment on a policy change. This does not grant an approval or bypass any
supervisor decision. The original study clock remains fixed.
