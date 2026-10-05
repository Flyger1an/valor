# Always-on virtual experiment deployment

The `valor-experiment` / `ledgers` service can run on an existing authorized VPS
independently of a developer laptop. It reads existing market, news and source
runtime snapshots, and writes only its own virtual experiment volume.

## Isolation

- No network, published ports, Docker socket, broker credentials or model keys.
- Read-only container root; UID/GID 10001; all capabilities dropped;
  no-new-privileges; only the experiment state volume is writable.
- Source market, outbox and news volumes mounted read-only.
- Resource ceilings: 0.25 CPU, 256 MiB RAM and 32 processes; bounded logs.
- Explicit one-time initialization and a persistent 90-day epoch; restarts resume
  existing state. Never rerun initialization, remove the volume or use `down -v`.

The service is a sizing simulation with hypothetical deterministic approvals.
Baseline, Kelly and Henry each start with fictional $500. It does not place broker
orders or add model/data API requests. Existing hosting can still accrue costs.
Results and operating estimates must not be represented as reconciled real profit.

## Release process

Build from an already-present pinned runtime image using `Dockerfile.experiment`.
Prepare a bounded context with the three virtual modules and frozen policy.
Record the source commit, input hashes, image ID and Compose configuration in
private deployment metadata. Build without pulling or network access.

Before changing an existing experiment, back up its SQLite database consistently
and replay a copy. Preserve identity, epoch, deadline, financial state and prior
journal bytes. Verify the candidate image on that copy before stopping only the
isolated service. Do not restart the source broker/feed services.

For an authorized v1-to-v2 correction, release the single-writer lock and run the
candidate image's `upgrade-evidence` command against the existing experiment.
It appends exactly one version-boundary event and is idempotent. Verify the old
journal prefix and financial state, replay again, then start the candidate image
against the same volume. Do not restore an old backup to erase later observations.
Use a v2-capable binary after migration.

## Verification

Check health, advancing frame/coverage counters, snapshot age, source identity,
mounts, isolation and unchanged source-service identities. Replay a temporary
consistent copy without disturbing the writer. The activation day must remain
ineligible; the first potential full v2 day starts at the next UTC midnight.
A full day is not claimed until it closes and all valuation/settlement checks pass.

Store actual deployment addresses, connection details, journal snapshots, operator
records and performance observations privately. They are intentionally excluded
from this public source repository. See [EXPERIMENT-EVIDENCE.md](EXPERIMENT-EVIDENCE.md)
for the forward-evidence definition and [EXPERIMENT.md](EXPERIMENT.md) for run commands.
