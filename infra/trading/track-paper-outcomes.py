#!/usr/bin/env python3
"""Compare archived broker-demo outcomes with a frozen pre-change cohort. Entirely offline."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'evolver'))
from evolver.trading.outcome_tracking import compare, make_baseline, read_backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--change-at', type=float)
    parser.add_argument('--source-commit')
    args = parser.parse_args()
    report = read_backup(args.backup)
    snapshot = json.loads(args.snapshot.read_text())
    if args.initialize:
        if args.change_at is None or not args.source_commit:
            parser.error('initialization requires --change-at and --source-commit')
        baseline = make_baseline(report, snapshot, args.change_at, args.source_commit)
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        with args.baseline.open('x') as target:  # never silently reset the baseline
            json.dump(baseline, target, indent=2)
    baseline = json.loads(args.baseline.read_text())
    result = compare(baseline, report, snapshot)
    result['backup_sha256'] = hashlib.sha256(args.backup.read_bytes()).hexdigest()
    payload = json.dumps(result, indent=2) + '\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    history = args.output.parent/(args.output.stem+'-history')
    history.mkdir(exist_ok=True)
    (history/(hashlib.sha256(payload.encode()).hexdigest()+'.json')).write_text(payload)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(payload)
    temporary.replace(args.output)
    print(json.dumps({k: result[k] for k in ('status', 'before_revised', 'after',
                     'baseline_fee_and_accounting_revision_usd', 'mean_net_change_usd')}))


if __name__ == '__main__':
    main()
