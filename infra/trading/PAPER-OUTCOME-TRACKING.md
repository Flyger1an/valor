# Measuring paper learning changes

`track-paper-outcomes.py` reads a secret-free monitoring backup and a fresh cloud snapshot. It has no broker credentials, network calls, model calls, or execution authority. The backup's policy, ledger and snapshot are checked against the backup manifest; the copied SQLite database is opened read-only.

Freeze a baseline once, at the verified deployment time while the paper account is flat:

```sh
python3 infra/trading/track-paper-outcomes.py \
  --backup /path/to/monitor-backup.tar.gz --snapshot /path/to/snapshot.json \
  --baseline /path/to/paper-feedback-baseline.json \
  --output /path/to/paper-feedback-comparison.json \
  --initialize --change-at VERIFIED_DEPLOYMENT_UNIX_TIMESTAMP --source-commit DEPLOYED_COMMIT
```

On later monitoring runs use the same command without the final line. Pass the backup downloaded by that run, not a filename sorted lexically. Initialization refuses to overwrite an existing baseline. Reports retain a content-addressed observation history beside the output.

The comparison uses trade entry times and fixed baseline lot IDs. Later fee revisions on old trades are reported separately, so they cannot masquerade as new strategy profit. Trades spanning the change boundary are shown separately. Both losing and winning trades count; the engineering drill is excluded. Missing baseline lots, changed account/policy identity, or totals ahead of the available backup prevent a current comparison claim.

Inspect net P&L, mean net per closed trade, win/loss counts, profit factor, fee/quantity drag, and results by instrument and strategy. The notional-normalized metric covers only trades with complete attribution. Gross fill-price P&L already includes spread/slippage; do not subtract these again. Fee/quantity effects include provisional reserves and rounding dust, not solely posted fees. Current open exposure and estimated hosting/model costs appear separately.

The first baseline for PR #10 is the seven completed strategy trades before the October 12 learning deployment. Their initial combined net was approximately **-$1.669001**, with six outcomes provisional. Future actual fee postings revise that baseline rather than moving its boundary. Before/after evidence is descriptive: a small sample, changing market conditions, or different instrument mix cannot establish that the software caused an improvement. Quiet periods and vetoed entries are not profitable trades. Paper evidence never activates live trading or starts the live study clock.
