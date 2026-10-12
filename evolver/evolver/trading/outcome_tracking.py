"""Read-only before/after paper outcomes. No broker or model calls, no strategy authority."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile

from .contracts import Policy, decimal as D
from .feedback import summarize


class ArchiveBook:
    """The minimum feedback interface over an immutable, copied SQLite backup."""
    def __init__(self, path, policy):
        self.policy = policy
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        self.db.row_factory = sqlite3.Row

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def orders(self, include_protection=False):
        return [dict(r) for r in self.db.execute('SELECT * FROM orders ORDER BY created,client_id')
                if include_protection or r['purpose'] != 'protection']

    def closed_trades(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM broker_closed_trades ORDER BY closed,lot_id')]


def read_backup(path):
    """Read only three named regular members, verify their manifest hashes, never extract paths."""
    with tarfile.open(path, 'r:gz') as archive:
        def read(name, limit):
            matches = [m for m in archive.getmembers() if m.name == name]
            if len(matches) != 1 or not matches[0].isfile() or matches[0].size > limit:
                raise ValueError('missing, duplicate or invalid backup member: ' + name)
            return archive.extractfile(matches[0]).read()
        manifest = json.loads(read('manifest.json', 20_000_000))
        parts = {name: read(name, limit) for name, limit in (
            ('policy.json', 20_000), ('state/book.sqlite', 256_000_000), ('outbox/snapshot.json', 20_000_000))}
        if any(hashlib.sha256(raw).hexdigest() != manifest.get(name) for name, raw in parts.items()):
            raise ValueError('backup manifest mismatch')
    policy = Policy.from_dict(json.loads(parts['policy.json']))
    if policy.mode != 'demo':
        raise ValueError('tracking requires authenticated paper-demo evidence')
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp)/'book.sqlite'
        path.write_bytes(parts['state/book.sqlite'])
        book = ArchiveBook(path, policy)
        try:
            if book.get('identity', {}).get('policy') != policy.fingerprint or not book.get('activity_accounting'):
                raise ValueError('backup ledger does not match policy or broker accounting')
            report = summarize(book)
            report['broker_identity'] = book.get('identity')['broker']
            report['ledger_reconciled_at'] = book.get('broker_reconciled_at', 0)
            report['backup_snapshot_at'] = json.loads(parts['outbox/snapshot.json'])['timestamp']
            return report
        finally:
            book.db.close()


def stats(rows):
    values = [D(t['net_pnl_usd']) for t in rows]
    net = sum(values, D(0))
    gains, losses = sum((max(v, D(0)) for v in values), D(0)), -sum((min(v, D(0)) for v in values), D(0))
    known = [r for r in rows if r['attribution'] == 'reconciled_fill_prices']
    notional = sum((D(r['entry_notional_usd']) for r in known), D(0))
    return {'trades': len(rows), 'wins': sum(v > 0 for v in values), 'losses': sum(v < 0 for v in values),
            'net_pnl_usd': str(net), 'mean_net_pnl_usd': str(net/len(rows)) if rows else None,
            'profit_factor': str(gains/losses) if losses else None,
            'no_losses': bool(rows) and not losses,
            'provisional_trades': sum(bool(t['fees_provisional']) for t in rows),
            'attributed_trades': len(known),
            'gross_fill_price_pnl_usd': str(sum((D(r['gross_fill_price_pnl_usd']) for r in known), D(0))),
            'fee_and_quantity_effect_usd': str(sum((D(r['fee_and_quantity_effect_usd']) for r in known), D(0))),
            'net_bps_on_attributed_entry_notional': str(sum((D(r['net_pnl_usd']) for r in known), D(0))/notional*10000) if notional else None,
            'best_net_usd': str(max(values)) if values else None,
            'worst_net_usd': str(min(values)) if values else None}


def snapshot_matches(report, snapshot):
    # The cloud snapshot has a six-decimal compact summary, not the complete trade list.
    compact = snapshot.get('outcome_feedback', {}).get('summary', {})
    current = stats(report['trades'])
    return (compact.get('trades') == current['trades']
            and compact.get('provisional_trades') == current['provisional_trades']
            and compact.get('net_pnl_usd') is not None
            and abs(D(compact['net_pnl_usd'])-D(current['net_pnl_usd'])) <= D('.0000005'))


def validate_identity(report, snapshot, baseline=None):
    if (report['mode'] != 'demo' or snapshot.get('mode') != 'demo'
            or report['policy_hash'] != snapshot.get('policy_hash')):
        raise ValueError('paper mode and matching policy required')
    if baseline and any(report[k] != baseline[k] for k in ('mode', 'policy_hash', 'broker_identity')):
        raise ValueError('cohort identity changed; preserve this baseline and review a new boundary')


def make_baseline(report, snapshot, change_at, source_commit):
    validate_identity(report, snapshot)
    if (not 0 < change_at <= snapshot['timestamp'] or snapshot.get('positions')
            or snapshot.get('pending_orders') or snapshot.get('halt')
            or any(t['closed'] > change_at for t in report['trades'])
            or not snapshot_matches(report, snapshot)):
        raise ValueError('baseline needs a matching flat snapshot and only pre-change closed trades')
    return {'version': 1, 'change_at': change_at, 'source_commit': source_commit,
            **{k: report[k] for k in ('mode', 'policy_hash', 'broker_identity')},
            'baseline_observed_at': snapshot['timestamp'], 'trades': report['trades'],
            'summary': stats(report['trades']), 'costs_at_observation': snapshot.get('costs', {}),
            'basis': 'Entries after the deployed change; baseline lot IDs remain pre-change through later fee revisions.'}


def compare(baseline, report, snapshot):
    validate_identity(report, snapshot, baseline)
    anchor = baseline['change_at']
    original = {t['lot_id']: t for t in baseline['trades']}
    current = {t['lot_id']: t for t in report['trades']}
    before, after, transition = [], [], []
    for t in report['trades']:
        if t['lot_id'] in original or t['closed'] <= anchor:
            before.append(t)
        elif t['opened'] >= anchor:
            after.append(t)
        else:
            transition.append(t)
    missing = sorted(set(original)-set(current))
    revisions = (str(sum((D(current[cid]['net_pnl_usd'])-D(t['net_pnl_usd'])
                          for cid, t in original.items()), D(0))) if not missing else None)
    pre, post = stats(before), stats(after)
    detail_matches = snapshot_matches(report, snapshot)
    status = ('awaiting_current_backup' if not detail_matches or missing else
              'awaiting_post_change_trades' if not after else
              'provisional_comparison' if pre['provisional_trades'] or post['provisional_trades'] else
              'settled_descriptive_comparison')
    groups = defaultdict(lambda: {'before': [], 'after': []})
    for name, rows in (('before', before), ('after', after)):
        for row in rows:
            groups[(row['instrument'], row['strategy'])][name].append(row)
    return {'version': 1, 'status': status, 'mode': 'demo', 'change_at': anchor,
            'source_commit': baseline['source_commit'], 'snapshot_at': snapshot['timestamp'],
            'detail_backup_at': report.get('backup_snapshot_at'),
            'detail_reconciled_at': report.get('ledger_reconciled_at'),
            'closed_totals_match_current_snapshot': detail_matches,
            'before_at_baseline': baseline['summary'], 'before_revised': pre, 'after': post,
            'baseline_fee_and_accounting_revision_usd': revisions,
            'baseline_lots_no_longer_closed': missing, 'transition': stats(transition),
            'after_lot_ids': [t['lot_id'] for t in after],
            'mean_net_change_usd': str(D(post['mean_net_pnl_usd'])-D(pre['mean_net_pnl_usd'])) if before and after and detail_matches and not missing else None,
            'by_instrument_strategy': [{'instrument': key[0], 'strategy': key[1],
                **{name: stats(rows) for name, rows in group.items()}} for key, group in sorted(groups.items())],
            'current_open_positions': snapshot.get('positions', []), 'current_costs': snapshot.get('costs', {}),
            'current_halt': snapshot.get('halt'), 'current_healthy': snapshot.get('healthy'),
            'causal_improvement_established': False,
            'interpretation': 'Descriptive before/after evidence, not a controlled causal test. Fees can revise both cohorts. Gross/fee attribution covers only attributed trades; net includes all outcomes. Spread/slippage already appear in fills. Operating estimates and open exposure are separate; no trade is not a profitable trade.'}
