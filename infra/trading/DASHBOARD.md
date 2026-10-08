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

## Approved private phone service

`private-dashboard/server.mjs` is a separate production service. It reuses the
study parsers, pins the source policy and experiment identity/epoch, rejects live
or fixture data, and returns a selected status projection. It has four page/asset
routes and one read-only status route. Unknown paths return 404; write methods
return 405. Every route requires the approved operator identity and hostname.
Missing or invalid access configuration returns 403, including before setup.

`dashboard.compose.yaml` gives this service no container network, no published
ports, no credentials and no Docker socket. The root filesystem and source
volumes are read-only. Source volume directories are mounted because the
producers atomically replace snapshots; binding a single file would retain an
obsolete inode. The service only reads each directory's `snapshot.json` and
never serves the raw files. Its only writable mount holds its own Unix socket,
which has mode 0600 in a mode-0700 directory owned by UID 10001. Memory/CPU limits
are 128 MiB and 0.25 CPU. Restarting it does not restart or write to either study.

Use an official `node:22-alpine` image resolved to a digest as
`VALOR_DASHBOARD_BASE`, then build `Dockerfile.dashboard` from a context containing
only the private dashboard and the two parser files. Set the verified resulting
image in `VALOR_DASHBOARD_IMAGE`. There is no npm install or package dependency in
the deployed image. Tailscale 1.102.4 supports the intended proxy target:

```sh
tailscale serve --bg --https=443 unix:/opt/valor-dashboard/socket/dashboard.sock
```

This command is an activation step, not an installation prerequisite. Before
running it, the operator must sign in, verify the actual account is on an eligible
$0 plan, and approve device enrollment. The free Personal plan is for
noncommercial use; an advertised free tier or a trial is not account verification.
Stop if a paid plan is required. Do not create API/auth keys for this setup.

Keep the new server's incoming Tailscale connections blocked (`--shields-up`)
until the access policy has been reviewed. Only the chosen, approved Mac/iPhone
may reach this server's HTTPS port. Do not retain an allow-all policy, grant
shell/SSH access, advertise or accept subnet routes, select an exit node, or share
the node. Preserve any existing tailnet uses while resolving these restrictions.
User devices must remain untagged so Serve can supply their user identity.

Enable HTTPS with the operator's consent and a generic server name. Its assigned
`*.ts.net` hostname appears in public certificate-transparency logs. Leave Funnel
disabled, including the optional Funnel capability in the HTTPS consent flow.
Only after the plan and device policy are verified, populate the private
`/opt/valor-dashboard/config/access.json` (outside Git):

```json
{
  "schema_version": 1,
  "operator_login": "operator@example.invalid",
  "hostname": "monitor.example.ts.net",
  "source": "alpaca",
  "source_policy_hash": "<verified 64-character policy hash>",
  "experiment_identity_hash": "<verified 64-character identity hash>",
  "experiment_epoch": 0,
  "plan_verified_zero_cost": true,
  "device_access_verified": true
}
```

The placeholder values deliberately do not activate access. Use the existing
study's exact epoch, never initialize a replacement. Serve strips client-supplied
identity headers and injects the authenticated user. For Unix targets it sets
`Host: localhost` and replaces `X-Forwarded-Host` and `X-Forwarded-Proto`; the
backend checks those values and the exact login. Only local processes with
permission to access the socket can reach this trusted-proxy boundary.

Verify denial for a missing/other identity and an unapproved device, successful
HTTPS viewing from each approved device, accurate timestamps/stale warnings,
and automatic refresh after a dashboard-only restart. These end-to-end checks
remain pending until sign-in/enrollment and the actual plan/policy are verified.
The source code and a closed, unauthenticated deployment do not establish a
working phone URL.

Local verification: `npm run test:private-dashboard`; the existing parser tests
continue to cover book conservation, quote age, accounting and invalid data.

References: [terms](https://tailscale.com/terms),
[pricing and eligibility](https://tailscale.com/pricing),
[official iPhone app](https://tailscale.com/download/ios),
[Serve identity headers](https://tailscale.com/docs/features/tailscale-serve),
[Unix-socket CLI support](https://github.com/tailscale/tailscale/blob/v1.102.4/cmd/tailscale/cli/serve_v2.go),
[proxy header implementation](https://github.com/tailscale/tailscale/blob/v1.102.4/ipn/ipnlocal/serve.go).
