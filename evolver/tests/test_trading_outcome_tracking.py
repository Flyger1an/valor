from dataclasses import asdict, replace
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import unittest

from evolver.trading.contracts import decimal as D, encode
from evolver.trading.feedback import compact, summarize
from evolver.trading.outcome_tracking import compare, make_baseline, read_backup
from test_trading_feedback import Book
import test_trading_broker as broker_fixtures
from test_trading_runtime import NOW


class CohortTests(unittest.TestCase):
    def setUp(self):
        self.book = Book(); self.addCleanup(self.book.db.close)
        self.book.policy = replace(self.book.policy, mode='demo')
        self.book.add()
        self.baseline = make_baseline(self.report(), self.snapshot(), 200, 'fixture-deployment')

    def report(self):
        return {**summarize(self.book), 'broker_identity': 'alpaca:demo:fixture',
                'ledger_reconciled_at': 400, 'backup_snapshot_at': 400}

    def snapshot(self):
        return {'mode': 'demo', 'policy_hash': self.book.policy.fingerprint, 'timestamp': 400,
                'positions': [], 'pending_orders': [], 'halt': '', 'healthy': True,
                'outcome_feedback': compact(self.report()), 'costs': {'experiment_pnl_estimated_usd': '-2'}}

    def test_late_baseline_fees_are_not_new_strategy_profits(self):
        self.book.trades[0].update(realized_pnl='-.08', fees_provisional=0)
        self.book.add('new', 300, '.1', price='101')
        result = compare(self.baseline, self.report(), self.snapshot())
        self.assertEqual(D(result['baseline_fee_and_accounting_revision_usd']), D('.02'))
        self.assertEqual(D(result['after']['net_pnl_usd']), D('.1'))
        self.assertEqual(result['after_lot_ids'], ['new'])
        self.assertEqual(result['status'], 'provisional_comparison')
        self.assertFalse(result['causal_improvement_established'])

    def test_unchanged_account_is_not_reported_as_profitable(self):
        result = compare(self.baseline, self.report(), self.snapshot())
        self.assertEqual(result['status'], 'awaiting_post_change_trades')
        self.assertIsNone(result['mean_net_change_usd'])
        self.assertIsNone(result['after']['mean_net_pnl_usd'])

    def test_old_backup_is_not_presented_as_the_current_trade_sample(self):
        stale = self.report()
        self.book.add('new', 300, '-.3', price='99')
        result = compare(self.baseline, stale, self.snapshot())
        self.assertEqual(result['status'], 'awaiting_current_backup')
        self.assertFalse(result['closed_totals_match_current_snapshot'])
        self.assertIsNone(result['mean_net_change_usd'])

    def test_reopened_baseline_lot_requires_review_instead_of_vanishing_loss(self):
        self.book.trades.clear()
        result = compare(self.baseline, self.report(), self.snapshot())
        self.assertEqual(result['baseline_lots_no_longer_closed'], ['lot1'])
        self.assertIsNone(result['baseline_fee_and_accounting_revision_usd'])
        self.assertEqual(result['status'], 'awaiting_current_backup')

    def test_trade_crossing_change_boundary_is_separate(self):
        self.book.add('straddler', 195)
        result = compare(self.baseline, self.report(), self.snapshot())
        self.assertEqual(result['transition']['trades'], 1)
        self.assertEqual(result['after']['trades'], 0)

    def test_losses_and_wins_and_costs_all_count(self):
        self.book.add('loss', 300, '-.4', price='99')
        self.book.add('win', 350, '.1', price='101')
        result = compare(self.baseline, self.report(), self.snapshot())
        self.assertEqual((result['after']['wins'], result['after']['losses']), (1, 1))
        self.assertEqual(D(result['after']['net_pnl_usd']), D('-.3'))
        self.assertEqual(D(result['after']['profit_factor']), D('.25'))
        self.assertEqual(result['current_costs'], self.snapshot()['costs'])

    def test_settled_fees_do_not_establish_causality(self):
        self.book.add('new', 300, '.1', price='101')
        for t in self.book.trades: t['fees_provisional'] = 0
        result = compare(self.baseline, self.report(), self.snapshot())
        self.assertEqual(result['status'], 'settled_descriptive_comparison')
        self.assertFalse(result['causal_improvement_established'])

    def test_policy_and_account_changes_do_not_mix_cohorts(self):
        for key, value in [('mode', 'live'), ('policy_hash', 'different'), ('broker_identity', 'different')]:
            report = {**self.report(), key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                compare(self.baseline, report, self.snapshot())

    def test_initialization_cannot_backfill_post_change_trades_into_baseline(self):
        self.book.add('new', 300)
        with self.assertRaises(ValueError):
            make_baseline(self.report(), self.snapshot(), 200, 'fixture')

    def test_initialization_needs_a_flat_matching_paper_snapshot(self):
        for changes in [{'positions': [{'instrument': 'BTC-USD'}]}, {'pending_orders': ['pending']},
                        {'mode': 'live'}, {'timestamp': 199}, {'halt': 'accounting'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                make_baseline(self.report(), {**self.snapshot(), **changes}, 200, 'fixture')


class BackupTrackingTests(unittest.TestCase):
    setUp = broker_fixtures.BrokerTests.setUp
    tearDown = broker_fixtures.BrokerTests.tearDown
    enter = broker_fixtures.BrokerTests.enter
    exit = broker_fixtures.BrokerTests.exit

    def archive(self, corrupt=False, duplicate=False):
        self.enter(); snapshot = self.exit()
        copied = Path(self.tmp.name)/'copied.sqlite'
        with sqlite3.connect(copied) as target:
            self.book.db.backup(target)
        parts = {'state/book.sqlite': copied.read_bytes(), 'policy.json': encode(asdict(self.p)).encode(),
                 'outbox/snapshot.json': encode(snapshot).encode()}
        manifest = {name: hashlib.sha256(raw).hexdigest() for name, raw in parts.items()}
        if corrupt: manifest['state/book.sqlite'] = 'mismatch'
        parts['manifest.json'] = encode(manifest).encode()
        path = Path(self.tmp.name)/'backup.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, raw in parts.items():
                member = tarfile.TarInfo(name); member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
                if duplicate and name == 'policy.json': archive.addfile(member, io.BytesIO(raw))
        return path

    def test_real_book_replay_is_read_only_and_has_net_fees(self):
        path = self.archive(); previous = path.read_bytes()
        report = read_backup(path)
        self.assertEqual(report['summary']['trades'], 1)
        self.assertEqual(D(report['summary']['net_pnl_usd']), D('-.12'))
        self.assertEqual(report['broker_identity'], self.broker.identity)
        self.assertEqual(path.read_bytes(), previous)

    def test_modified_archive_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'manifest mismatch'):
            read_backup(self.archive(corrupt=True))

    def test_duplicate_named_member_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            read_backup(self.archive(duplicate=True))


if __name__ == '__main__':
    unittest.main()
