# Baseline, Kelly shadow, and Henry — virtual experiment

This is local software for three **fictional** $500 books. It creates no financial
accounts, places no broker orders, reads no credentials, calls no models, and does
not modify the existing broker-demo runtime or its live execution block. The
three virtual books begin together at an explicit forward initialization epoch.
The prior admission drill and the broker account history are not copied into them.
Their separate 90-day comparison does not start or reset the real live-study clock.

The original v1 identity and BTC/ETH journal are immutable. An explicit
[additive universe migration](UNIVERSE.md) enables ADA/BTC/ETH/SHIB/SKY/WIF
against USD and starts a prospective v3 Kelly evidence cohort. It preserves the
original epoch, deadline, balances, fees and earlier evidence classifications.

## Policies frozen at initialization

| Book | Rule |
|---|---|
| Baseline | Existing stair-step position and aggregate limits, supervisor scale/entry pause, planned trade-loss budget, independent daily equity-loss halt, six entry attempts/day. At $500 and scale 1: $25 position, $50 aggregate, $15 planned trade loss, $50 daily loss. |
| Kelly shadow | Same limits, plus the versioned deterministic robust quarter-Kelly recommendation. Missing, unusable, unsettled, insufficient, or negative-edge evidence means no entry. |
| Henry chaos | Up to all available cash in **one** eligible position, reserving fees. Symbols sort lexically (originally BTC before ETH). No $100/$200 position envelope, 20% halt, leverage, debt, top-ups, transfers, or position averaging. Henry ignores the supervisor's discretionary pause/scale and baseline loss budgets; he retains the common data/news, entry-session, spread, signal, and execution rules. |

Henry can lose the entire fictional bankroll. He stops permanently when the flat
book cannot fund the minimum entry plus its fee reserve, when held dust cannot be
executed, or when input/accounting integrity fails. Temporarily reserved refundable
fees count when assessing exhaustion; they are not replenishment. No special
winning probability or advantageous fill is assigned to Henry. His personality
changes the label, not the arithmetic.

All books use the same frozen strategy version and quote/bar stream. They retain
their own cash, orders, positions, fills, fees, limits, skips, drawdown, and event
history. A source runtime strategy promotion does not silently replace the
comparison strategy. Exits use the common stop, cost-aware target, ordinary
strategy exit, and 144 five-minute-bar maximum-hold condition. Virtual exits are
mechanical: they do **not** claim to reproduce an operational AI review.

## Inputs, approvals, and modeled execution

The runner reads pre-existing `market/quotes.json`, `market/signals.json`,
`outbox/snapshot.json`, and optionally `news/snapshot.json`. It never starts a feed.
When a raw news bundle is present, its content hash, source health, publication
times and freshness are assessed directly for all three books. A stale supervisor
does not allow Henry to bypass this shared news/data-integrity gate.
Historical closed bars can warm up the signal, but no performance is backfilled
before the epoch. Each eligible closed-bar signal gets a shared immutable identity;
each book records its independent acceptance or skip. Rejected and unfilled
opportunities are observations with no earned P&L.

The comparison uses a **hypothetical deterministic approval rule**, shared signal
and data/news checks. It does not reuse the broker account's approval for another
quantity. Baseline/Kelly additionally observe the source supervisor's current
pause and scale; Henry's intentionally different rule is stated above. The source
account's cash, holdings, realized profit and daily loss state never enter the books.
The existing exact-intent approval hashes and real engine controls are unchanged.

An order queues at the decision timestamp. An IOC fill needs a strictly later,
fresh quote within 30 seconds. Buys use the later ask plus adverse slippage and
cannot exceed the original limit. Sells use the later bid minus adverse slippage.
Quantity rounds **down** to the supplied asset increment. Entry orders below $10
are skipped, never enlarged. Partial fills retain only executed quantity; an IOC
remainder expires. Entry controls are checked again before a virtual fill.

In file-capture mode, executable capacity is modeled as **1% of the preceding
closed five-minute bar's volume**, shared across all fills for that asset/bar
bucket inside each book. Counterfactual books each get the same independent
liquidity assumption. This is a conservative participation proxy, **not observed
order-book depth**, a queue-position model, or proof of live execution quality.
Different order sizes may therefore have different partial fills and outcomes.

Stops trigger at an observed bid and become a market exit at a later available
quote. A price gap is charged at that later adverse price; the stop is not a
guaranteed loss limit. Missing intervals produce no invented intrabar fills and
invalidate the corresponding Kelly evidence block. This simulator does not claim
to reproduce a broker-held stop-limit filling while a host is offline.

Fees are USD-equivalent modeled costs, initially the source policy's fee rate
(currently 25 bps), rounded up to cents per fill. Each fill reserves up to 100 bps
in cash. The difference between reserved and estimated fees is an escrow asset,
included in net equity but unavailable for another trade. Thus Henry cannot spend
money needed by a subsequent fee adjustment. Modeled fees settle at the next UTC
day, releasing unused escrow; these are **not actual broker fee receipts**.

The event API also accepts a bounded virtual `fee_settlement` for an unsettled
fill. It can revise the fee upward or downward, including after a partial/full
exit. Entry-fee changes split correctly across realized and unrealized profit;
historical daily evidence receives the original fee-day correction. Duplicate
settlements are idempotent. A conflicting final settlement or fee above reserved
funds is a terminal integrity incident, not a silent favorable accounting rewrite.

## Kelly assumptions and limitations

Kelly learns only from this experiment's forward baseline. For each complete UTC
day it records a **synchronized active-universe vector** (BTC/ETH under v1/v2): each asset's change in net marked P&L,
divided by the declared unscaled baseline position-capital allowance at day start.
The denominator and timestamps are retained. These are opportunity-sleeve return
proxies at baseline participation, not pooled serial trade returns, historical
screening returns, or $500 account returns. They include idle periods and modeled
costs. Size-dependent liquidity means they are still an approximation for a
different allocation; the independent Kelly book tests that approximation.

Under v1, partial first/last days, any stale marks, gaps over 60 seconds, future
data and unsettled fees invalidate a day. The explicit [v2 evidence correction](EXPERIMENT-EVIDENCE.md)
instead requires continuous observation and valid valuations of held inventory
at each UTC boundary, while preserving execution guards and reporting quote
coverage. It never reclassifies v1 history or moves the epoch/deadline.
V3 retains those valuation rules and separates evidence by universe cohort.
It computes one joint six-asset allocation per frame using bounded deterministic
5% grid ascent, with fixed held exposures and add/remove/exchange moves. This
search is a documented heuristic, not a guarantee of the global optimum. Earlier
two-asset enumeration and journal replay remain unchanged.
The estimator requires at least **30 usable
daily blocks**, with at least **10 active blocks for an eligible asset**, and uses
at most the most recent 60. Thirty blocks are an initial operational policy, not a
statistical guarantee of an edge. No confidence number supplied by an LLM is used.

The deterministic v1 estimator shrinks each mean by 50%, subtracts twice an
estimated standard error (inflated for positive lag-one dependence), and requires
a positive adjusted mean. It enumerates nonnegative raw allocations on a 5% grid
with total allocation at most 100%, keeping existing holdings fixed. It maximizes
the worse expected log growth of synchronized empirical and comonotonic scenarios,
both mixed with a 1% joint -20% return shock. These are **declared conservative
stress assumptions**, not calibrated probabilities, drawdown guarantees, or a
validated optimal portfolio model.

Only the new raw recommendation is multiplied by 0.25. Existing baseline cash,
position, total-exposure and loss limits then cap it. Raw, fractional and final
notional, dataset hash, sample size, cutoff and reason are reported separately.
Quarter Kelly can still hit the $25 cap; it does not mean quartering $25. A final
size below the broker minimum remains cash. No existing holding is automatically
resized when a Kelly estimate changes. Data known after a decision can affect
subsequent decisions only; the journal retains the earlier state.

## Local operation

From the Valor repository, use a **separate non-nested output directory**. The
example source must already have fresh local inputs or an independently provided
read-only mirror of them. A downloaded status snapshot alone has no bar history
and is insufficient. This implementation does not change the VPS deployment or
add a paid feed/mirroring service.

For a new local fixture only, initialize the original identity and explicitly
activate both version boundaries. Existing studies start at `tick`/`run` after
their controlled migration; never rerun `init`.

```sh
PYTHONPATH=evolver python3 -m evolver.trading.experiment_runner init \
  --policy infra/trading/policy.demo.initial.json --root .valor/three-books

PYTHONPATH=evolver python3 -m evolver.trading.experiment_runner upgrade-evidence \
  --policy infra/trading/policy.demo.initial.json --root .valor/three-books

PYTHONPATH=evolver python3 -m evolver.trading.experiment_runner expand-universe \
  --policy infra/trading/policy.demo.initial.json \
  --new-policy infra/trading/policy.demo.json --root .valor/three-books

PYTHONPATH=evolver python3 -m evolver.trading.experiment_runner tick \
  --policy infra/trading/policy.demo.json --root .valor/three-books \
  --source-root .valor/trading-paper

# Optional continuous LOCAL observation of those existing files:
PYTHONPATH=evolver python3 -m evolver.trading.experiment_runner run \
  --policy infra/trading/policy.demo.json --root .valor/three-books \
  --source-root .valor/trading-paper

PYTHONPATH=evolver python3 -m evolver.trading.experiment_runner report \
  --policy infra/trading/policy.demo.json --root .valor/three-books

# Replays the journal in memory and compares the complete state. It never resets a book.
PYTHONPATH=evolver python3 -m evolver.trading.experiment_runner replay \
  --policy infra/trading/policy.demo.json --root .valor/three-books
```

`init` freezes the current timestamp and the first approved strategy by default;
`--strategy` can choose another already-approved immutable version. Subsequent
initialization, policy/rules changes, backdated events, changed observations under
the same ID, and conflicting market revisions are rejected. Preserve the SQLite
file and its journal. There is no refill/reset command. Atomic transactions commit
the input and all three projections together; a single-writer file lock protects
the CLI. A restart resumes the same book and epoch.

The separately authorized `upgrade-evidence` command is the explicit journaled
exception for the documented v2 evidence semantics. `expand-universe` is the
separate append-only v3 boundary; neither command can reset the study. V2 changes no execution/risk
settings, preserves old blocks and excludes the activation day. Release the
single-writer lock by stopping only the experiment service before using it.

At 90 elapsed days, new entries stop and virtual positions seek exits using fresh
observations and available modeled liquidity. The runner ends once flat with fees
settled; it cannot invent a closing fill during an outage.
An always-on service can pass `--stay-running-after-completion` to remain idle at
that settled end state. It then consumes no further inputs and never initializes
a replacement bankroll when its container restarts.

## Isolated always-on service

`experiment.compose.yaml` defines a separate `valor-experiment` Compose project.
It reads the existing `valor-demo_market`, `valor-demo_outbox` and
`valor-demo_news` volumes read-only, and writes only `valor-experiment_state`.
It has no network, ports, host socket, broker ledger, credentials or model budget
mounts. The root filesystem is read-only, UID/GID is 10001, capabilities are
dropped, and CPU/memory are capped at 0.25 CPU/256 MiB. This is an additional
container on the existing VPS, with no additional cloud resource or paid API.
It still consumes host CPU, disk and memory; existing hosting bills continue.

Build `Dockerfile.experiment` from an explicitly pinned, already-present runtime
image. The release context contains only `experiment.py`, `experiment_runner.py`,
`shadow_sizing.py`, `contracts.py`, `universe.py`, frozen `policy.json`, and the Dockerfile. The inherited runtime
dependencies must match the locally verified versions. Build with network off
and pulling disabled, under a distinct experiment image tag; never overwrite the
existing runtime image/tag or change its Compose project.

Set `VALOR_EXPERIMENT_IMAGE` to the verified release image in a separate deployment
environment file. A new deployment requires explicit initialization with the
original policy and the version boundaries shown above. An existing deployment
uses the controlled [migration](UNIVERSE.md). The common virtual 90-day clock
starts at original initialization. The `ledgers` command only resumes existing state; missing state
is an error, never an implicit new $500. Container restarts resume the same epoch,
policy, journal and balances. Never remove the state volume or run `down -v`.

For replay/idempotence checks, stop only this new service, open its state with the
same release/policy, replay in memory, and confirm duplicate input does not change
event count or state hash. Restart only this service afterward. The source broker
runtime and original live-study clock must remain untouched. Its supervision may
remain paused outside the entry window: this is not authority to resume it.

All books remain a **sizing simulation with hypothetical approvals**, not an exact
reproduction of actual model-gated execution. Henry differs in exposure and
discretionary risk/supervision policy, not in the shared requirements for valid,
fresh quotes, closed bars, news evidence, frozen source/policy identity and sound
fictional accounting. A shared integrity failure stops the simulation.

Set the local dashboard environment variable and open `/study`:

```sh
VALOR_EXPERIMENT_SNAPSHOT_PATH=/absolute/path/to/.valor/three-books/snapshot.json npm run dev
```

The existing browser authentication remains in force. The authenticated read-only
endpoint is `/api/ops/trading-experiment`. Neither page nor endpoint starts a
runner or mutates a book. Stale, invalid, and missing data are labeled explicitly.

## Evaluation and operating costs

Compare equity, realized/unrealized profit, modeled fees, drawdown, exposure,
turnover, closed trades, rejected/unfilled opportunities, and safety incidents.
The UI keeps a stable Baseline/Kelly/Henry order and does not crown the highest
raw-profit arm. Higher exposure and drawdown must accompany any profit comparison.
Kelly's log-growth objective is not a guarantee of profit taken over 90 days.

Shared model/hosting estimates are measured as a delta from the first captured
source cost counter. They are counted once, with a clearly labeled one-third
attribution for comparison. Actual shared invoices remain `null` unless recorded
as a separate cumulative `shared_cost` event. There are zero incremental model API
calls; local compute and future hosting invoices are not claimed free or verified.
Operating costs are reported separately and never replenish/debit fictional
trading cash. Execution prices already contain modeled spread/slippage; they are
not charged twice. Each policy's fee model is reported separately from operating
expenses and actual broker accounting.

Run the local checks:

```sh
PYTHONPATH=evolver python3 -m unittest discover -s evolver/tests -p 'test_trading*.py'
npm test -- --reporter=dot
npx eslint src/app/study/page.tsx src/app/study/experiment-comparison.tsx \
  src/app/api/ops/trading-experiment/route.ts src/lib/trading/experiment-status.ts \
  tests/trading-experiment-status.test.ts
npm run build
```

This change is not a deployment or live-activation authorization. A future VPS
rollout, feed mirror, long-running process, financial account, or resource purchase
requires its own applicable authorization. No strategy edge has been established.
