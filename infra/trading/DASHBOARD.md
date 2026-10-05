# Private study monitoring

`/study` shows the separate broker-demo runtime and the three fictional books.
It displays the immutable epoch/deadline, equity, fees, P&L, snapshot freshness,
provider-quote freshness and prospective evidence coverage. A current snapshot
does not refresh an old quote. The visible tab refreshes every ten seconds.

## Existing Mac access

For an existing authorized VPS, `dashboard_mirror.py` reads exactly two snapshot
files over the existing pinned SSH connection. It makes no broker requests or
remote writes, creates no credentials, retains original timestamps and checks
the source policy and experiment identity. Transport failure retains the last
verified snapshot, which becomes visibly stale. Local copies have mode 0600.

The existing deployment identity is required for the experiment volume; the
restricted monitoring identity currently permits only the source runtime status.
The web process receives file paths only, never SSH keys or connection metadata.
Connection and key files must already exist and remain outside Git.

```sh
python3 infra/trading/dashboard_mirror.py \
  --connection /private/path/cloud-connection.json \
  --key /private/path/existing-deployment-key \
  --destination "$PWD/.valor/dashboard"

# In a separate terminal; bind only to this Mac's loopback interface.
VALOR_TRADING_SNAPSHOT_PATH="$PWD/.valor/dashboard/runtime.json" \
VALOR_EXPERIMENT_SNAPSHOT_PATH="$PWD/.valor/dashboard/experiment.json" \
npm run dev -- --hostname 127.0.0.1 --port 3026
```

Open `http://127.0.0.1:3026/study` on the same Mac. This uses the existing local
development page-access behavior. The experiment API continues to require
authorization even when other read APIs are configured public. No public-read
flag, authentication override, firewall rule or access grant is changed.
Stop each process with Ctrl-C when finished. The study keeps running on the VPS.

## Phone access: separate approval required

There is no phone-access route implied by a localhost URL. A private network
deployment on the existing VPS is the proposed next step: enroll the server and
the operator's chosen devices with an authenticated private-network provider,
serve a read-only study dashboard only on that private interface, and permit only
those devices. It would reveal the study's virtual equity/P&L, evidence coverage,
data freshness and selected source-runtime status to that operator.

This requires explicit approval for the new identity/device access grants,
software installation and private dashboard service. It should not expose a
public financial endpoint, open public firewall ports, create broker credentials
or change execution settings. Provider terms and resource availability must be
verified before setup. No such access is created by this source change.
