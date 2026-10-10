#!/usr/bin/env python3
"""Read the cloud paper ledger and optionally download its secret-free backup via restricted SSH."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', type=Path, default=repo / '.valor/cloud-connection.json')
    parser.add_argument('--save-snapshot', type=Path)
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.connection.read_text())
    if config['user'] != 'valor-monitor':
        raise ValueError('This command permits only the read-only monitoring account')
    ssh = ['ssh', '-i', config['key_path'], '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
           '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
           '-o', 'UserKnownHostsFile=' + config['known_hosts_path'], config['user'] + '@' + config['host']]
    result = subprocess.run(ssh + ['status'], capture_output=True, timeout=30, check=True)
    snapshot = json.loads(result.stdout)
    if snapshot.get('policy_hash') and snapshot['policy_hash'] != config['policy_hash']:
        raise ValueError('Remote policy fingerprint changed; audit before relying on it')
    if args.save_snapshot:
        args.save_snapshot.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.save_snapshot.with_suffix('.tmp')
        temporary.write_bytes(result.stdout)
        temporary.replace(args.save_snapshot)
    backup = None
    if args.backup_dir:
        response = subprocess.run(ssh + ['backup'], capture_output=True, timeout=60, check=True)
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(response.stdout).hexdigest()
        path = args.backup_dir / (digest[:16] + '.tar.gz')
        if not path.exists():
            path.write_bytes(response.stdout)
            path.chmod(0o600)
        backup = str(path)
    if args.json:
        print(json.dumps(snapshot, sort_keys=True))
    else:
        print(json.dumps({key: snapshot.get(key) for key in
                          ('healthy', 'mode', 'timestamp', 'equity', 'cash', 'realized_pnl', 'halt',
                           'entry_pause', 'broker_reconciled_at', 'reconciliation_error',
                           'study_started_at', 'study_deadline', 'paper_deadline', 'model_usage', 'host')}, sort_keys=True))
    if backup:
        print(json.dumps({'downloaded_backup': backup}))


if __name__ == '__main__':
    main()
