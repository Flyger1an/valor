# Isolated trading runtime

This package supplies the existing BTC/ETH cash-study runtime and the shared
contracts, ledger, strategy, news and snapshot types used by the virtual experiment.
The three virtual books are described in [EXPERIMENT.md](EXPERIMENT.md).
See [DASHBOARD.md](DASHBOARD.md) for private, automatically refreshed monitoring.

`policy.paper.json` is a local simulation policy. `policy.demo.json` selects the
Alpaca paper broker with simulated funds. Real-money execution is blocked by the
adapter. Provider/account selection and any broker operation require separate
authorization; running these services is not part of installing the dashboard.

## Components

The Compose definition separates feed, execution worker, model reviews, research,
news, monitoring and watchdog processes. Broker credentials belong only to the
worker; the virtual experiment never receives credentials or an execution client.
The monitor port is bound to loopback. Source snapshots are read-only inputs for
the separate virtual ledger and dashboard.

The source runtime records fills, partials, modeled/provisional and posted fees,
protection status, available cash, realized/unrealized P&L and operating estimates.
Operating estimates are not reconciled invoices. Stale data and uncertain broker
requests block entries; persistent client IDs are reconciled before retrying.
A broker-held stop-limit may remain unfilled after a price gap. Planned loss
budgets are not guarantees of maximum realized loss.

## Configuration and operation

Keep connection metadata, account identifiers, actual policy/deployment records,
keys, environment files, journals and snapshots outside Git. Environment examples
contain names only. Never copy real credentials into examples or commit a runtime
volume. `.gitignore` and `.dockerignore` exclude local state and secret drops.

Review `compose.yaml`, resource limits, provider terms and the selected policy
before any separately authorized deployment. The existing Coinbase adapter is
legacy code, not a recommendation or permission to use that service; its current
market-data terms must be resolved before any use. The currently deployed study
uses its existing Alpaca source. Cross-venue prices must not be passed off as
executable Alpaca quotes, and source changes require a versioned study boundary.

For an existing authorized deployment, `check-cloud.py` reads status using the
restricted monitoring identity and can save a local snapshot. A saved snapshot
becomes stale; the dashboard must refresh it and retain its original timestamps.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=evolver python3 -m unittest discover \
  -s evolver/tests -p 'test_trading*.py'
```

Cloud provisioning, credential setup, admission orders, policy changes and live
activation are separate operations and are not performed by those tests.
Private operator records describe the actual deployment and must remain private.
