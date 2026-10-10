"""Offline regressions for wasted review calls during unresolved execution."""
import json
import unittest
from unittest.mock import patch

import test_trading_orchestration as fixtures
from test_trading_runtime import NOW, approve
from evolver.trading.agent_worker import process_request
from evolver.trading.contracts import Intent


class ReviewAdmissionTests(unittest.TestCase):
    setUp = fixtures.OrchestrationTests.setUp
    tearDown = fixtures.OrchestrationTests.tearDown
    market = fixtures.OrchestrationTests.market

    def enter_position(self):
        self.worker.engine.analyst = self.worker.engine.reviewer = approve
        self.worker.engine.tick({'BTC-USD': self.q}, [self.buy], NOW)
        self.worker.engine.analyst = self.worker.engine.reviewer = None
        self.assertIn('BTC-USD', self.book.positions())
        return Intent('exit-bar', self.buy.strategy, 'BTC-USD', 'sell',
                      self.book.positions()['BTC-USD']['quantity'], '0', NOW+1,
                      'target_or_strategy_exit')

    def test_halt_does_not_queue_repeated_exit_reviews_and_still_writes_status(self):
        sell = self.enter_position()
        self.book.halt('broker_accounting_mismatch', NOW+1)
        with patch.object(self.worker.engine, '_reconcile', return_value=False) as reconcile, \
                patch('evolver.trading.worker.make_intents', return_value=[sell]), \
                patch.object(self.broker, 'submit', wraps=self.broker.submit) as submit:
            for stamp in (NOW+1, NOW+2, NOW+3):
                snapshot = self.worker.tick(stamp)
            self.assertEqual(reconcile.call_count, 3)
            submit.assert_not_called()
        self.assertFalse(self.book.get('pending_reviews'))
        self.assertFalse(list((self.root/'outbox/requests').glob('*.json')))
        written = json.loads((self.root/'outbox/snapshot.json').read_text())
        self.assertEqual(written['timestamp'], NOW+3)
        self.assertEqual(snapshot['halt'], 'broker_accounting_mismatch')

    def test_pending_ordinary_order_blocks_duplicate_exit_review_without_halt(self):
        sell = self.enter_position()
        outstanding = Intent('earlier-exit', sell.strategy, sell.instrument, 'sell',
                             sell.quantity, '0', NOW+1, 'target_or_strategy_exit')
        self.book.reserve(outstanding, NOW+1)
        with patch.object(self.worker.engine, '_reconcile', return_value=False), \
                patch('evolver.trading.worker.make_intents', return_value=[sell]):
            snapshot = self.worker.tick(NOW+2)
        self.assertFalse(snapshot['halt'])
        self.assertEqual(len(snapshot['pending_orders']), 1)
        self.assertFalse(self.book.get('pending_reviews'))

    def test_reconciliation_pause_blocks_exit_review_before_a_halt(self):
        sell = self.enter_position()
        for reason in ('broker_reconciliation_unavailable', 'unresolved_order',
                       'activity_lag', 'order_preparation_unconfirmed', 'order_state_refresh'):
            with self.subTest(reason=reason):
                with self.book.db:
                    self.book.set('entry_pause', reason)
                with patch.object(self.worker.engine, '_reconcile', return_value=False), \
                        patch('evolver.trading.worker.make_intents', return_value=[sell]):
                    snapshot = self.worker.tick(NOW+2)
                self.assertFalse(snapshot['halt'])
                self.assertFalse(self.book.get('pending_reviews'))

    def test_incident_invalidates_old_approval_preserves_evidence_and_recovery_needs_new_review(self):
        with patch('evolver.trading.worker.make_intents', return_value=[self.buy]):
            self.worker.tick(NOW)
            old = self.book.get('pending_reviews')[self.buy.client_id]['request']
            result = process_request(old, self.p, approve, approve, NOW)
            self.worker.mailbox.answer(old, result, NOW+.1)
            self.book.halt('broker_accounting_mismatch', NOW+.2)
            with patch.object(self.worker.engine, '_reconcile', return_value=False):
                self.worker.tick(NOW+1)
            self.assertFalse(self.book.get('pending_reviews'))
            self.assertFalse(self.book.orders())
            self.assertTrue((self.root/'outbox/requests'/f"{old['id']}.json").exists())
            self.assertTrue((self.root/'inbox/responses'/f"{old['id']}.json").exists())
            invalid = json.loads((self.root/'outbox/invalidations'/f"{old['id']}.json").read_text())
            self.assertEqual(invalid['reason'], 'review.execution_blocked')
            # Simulate an independently completed recovery in this temporary fixture.
            with self.book.db:
                self.book.set('halt', '')
            self.worker.tick(NOW+2)
            fresh = self.book.get('pending_reviews')[self.buy.client_id]['request']
            self.assertNotEqual(fresh['id'], old['id'])
            self.assertFalse(self.book.orders())

    def test_protective_exit_still_runs_before_review_gate(self):
        self.enter_position()
        self.book.halt('operator_kill_switch', NOW+1)
        snapshot = self.worker.tick(NOW+1)
        self.assertFalse(snapshot['positions'])
        self.assertEqual(len(self.book.closed_trades()), 1)
        self.assertFalse(self.book.get('pending_reviews'))
        self.assertTrue(any(json.loads(o['intent'])['reason'] == 'protective_exit'
                            for o in self.book.orders()))

    def test_supervisor_pause_does_not_block_independently_reviewed_exit(self):
        sell = self.enter_position()
        self.worker.engine.supervise(dict(action='pause_entries', risk_scale='1',
            policy_hash=self.p.fingerprint, issued_at=NOW+1, expires_at=NOW+600,
            reason='pause new entries while permitting ordinary exits'), NOW+1)
        with patch('evolver.trading.worker.make_intents', return_value=[sell]):
            self.worker.tick(NOW+2)
            request = self.book.get('pending_reviews')[sell.client_id]['request']
            result = process_request(request, self.p, approve, approve, NOW+2)
            self.worker.mailbox.answer(request, result, NOW+2.1)
            snapshot = self.worker.tick(NOW+3)
        self.assertFalse(snapshot['positions'])
        self.assertEqual(len(self.book.closed_trades()), 1)


if __name__ == '__main__':
    unittest.main()
