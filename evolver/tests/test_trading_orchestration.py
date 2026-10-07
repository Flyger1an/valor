import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from test_trading_runtime import NOW, approve, policy
from evolver.trading.agent_worker import BudgetedModel, process_request
from evolver.trading.alpaca import AlpacaBroker, AlpacaHTTP, BrokerError
from evolver.trading.broker import PaperBroker
from evolver.trading.contracts import Intent, Quote, OrderReport, decimal as D, encode, utc_timestamp
from evolver.trading.engine import Engine, write_snapshot
from evolver.trading.ipc import Mailbox
from evolver.trading.ledger import Ledger
from evolver.trading.learning import evaluate
from evolver.trading.market import merge_bars
from evolver.trading.runtime import status
from evolver.trading.strategies import CATALOG, position_budget, backtest
from evolver.trading.worker import Worker


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.p = policy(approved_strategies=tuple(s.version for s in CATALOG))
        self.book = Ledger(self.root / 'state/book.sqlite', self.p, PaperBroker.identity)
        self.broker = PaperBroker(self.book, self.p)
        self.worker = Worker(self.book, self.p, self.broker, *(self.root / s for s in
                             ('state', 'outbox', 'inbox', 'market', 'research')))
        self.q = Quote('BTC-USD', '99.9', '100', NOW)
        self.buy = Intent('bar1', CATALOG[0].version, 'BTC-USD', 'buy', '.24', '97', NOW, 'fixture', '100.05')
        self.worker.engine.supervise(dict(action='resume_entries', risk_scale='1', policy_hash=self.p.fingerprint,
                                         issued_at=NOW, expires_at=NOW+600, reason='fixture'), NOW)
        self.market(self.q)

    def tearDown(self):
        self.book.close()
        self.tmp.cleanup()

    def market(self, quote):
        header = {'timestamp': quote.timestamp, 'source': 'coinbase_public', 'policy_hash': self.p.fingerprint}
        write_snapshot(self.root / 'market/quotes.json', {**header, 'quotes': {'BTC-USD': asdict(quote)}})
        bars = [dict(timestamp=(quote.timestamp//300-80+i)*300, open='100', high='101', low='99', close='100', volume='20')
                for i in range(80)]
        write_snapshot(self.root / 'market/signals.json', {**header, 'histories': {'BTC-USD': bars}})
        write_snapshot(self.root / 'market/history.json', {**header, 'histories': {'BTC-USD': bars}})

    def test_async_two_reviews_restart_and_unattended_stop(self):
        with patch('evolver.trading.worker.make_intents', return_value=[self.buy]):
            self.worker.tick(NOW)
            self.assertEqual(self.book.orders(), [])
            queued = self.book.get('pending_reviews')[self.buy.client_id]['request']
            result = process_request(queued, self.p, approve, approve, NOW)
            self.worker.mailbox.answer(queued, result, NOW+.1)
            self.worker.tick(NOW+1)
            self.assertEqual(len(self.book.orders()), 1)
            self.worker.tick(NOW+2)
            self.assertEqual(len(self.book.orders()), 1)
        self.market(Quote('BTC-USD', '90', '90.1', NOW+601))
        snapshot = self.worker.tick(NOW+601)
        self.assertFalse(snapshot['positions'])
        self.assertTrue(any(e['event'] == 'trade.closed' for e in snapshot['events']))

    def test_expired_approval_cannot_place_a_later_order(self):
        with patch('evolver.trading.worker.make_intents', return_value=[self.buy]):
            self.worker.tick(NOW)
            request = self.book.get('pending_reviews')[self.buy.client_id]['request']
            self.worker.mailbox.answer(request, process_request(request, self.p, approve, approve, NOW), NOW+31)
            self.market(replace(self.q, timestamp=NOW+32))
            self.worker.tick(NOW+32)
            self.assertFalse(self.book.orders())

    def test_mailbox_rejects_modified_envelopes_and_future_responses(self):
        box = self.worker.mailbox
        req = box.put('trade', {'policy_hash': self.p.fingerprint}, NOW, 30)
        box.answer(req, {'test': True}, NOW+1)
        with self.assertRaises(ValueError):
            box.result(req, NOW)
        self.assertEqual(box.result(req, NOW+2), {'test': True})
        self.assertIsNone(box.result(req, NOW+31))
        altered = {**req, 'expires_at': NOW+999}
        write_snapshot(box.requests / (req['id'] + '.json'), altered)
        self.assertEqual(list(box.pending(NOW+32)), [])

    def test_step_sizing_shrinks_and_doubles_only_at_thresholds(self):
        for equity, expected in ((250, '12.5'), (500, '25'), (999, '25'), (1000, '50'), (1999, '50'), (2000, '100')):
            self.assertEqual(position_budget(self.p, equity), D(expected))
        self.assertEqual(position_budget(self.p, 1000, '.5'), D(25))

    def test_remaining_daily_risk_blocks_before_hard_loss_is_hit(self):
        with self.book.db:
            self.book.set('day_start_equity', '550')  # $50 already lost; only $5 risk remains
        snap = self.worker.engine.snapshot({'BTC-USD': self.q}, NOW)
        intent = replace(self.buy, stop_price=D(60))
        self.assertEqual(self.worker.engine._entry_reason(intent, {'BTC-USD': self.q}, NOW, snap), 'remaining_daily_loss_budget')

    def test_study_deadline_closes_positions_without_agent(self):
        engine = self.worker.engine
        engine.analyst = engine.reviewer = approve
        engine.tick({'BTC-USD': self.q}, [self.buy], NOW)
        engine.analyst = engine.reviewer = None
        end = self.p.study_end_timestamp
        snap = engine.tick({'BTC-USD': replace(self.q, timestamp=end)}, [], end)
        self.assertEqual(snap['halt'], 'study_complete')
        self.assertFalse(snap['positions'])

    def test_health_turns_stale_without_faking_a_new_timestamp(self):
        self.worker.tick(NOW)
        self.assertTrue(status(self.root / 'outbox', NOW)['healthy'])
        self.assertFalse(status(self.root / 'outbox', NOW+31)['healthy'])

    def test_preflight_without_live_deadline_can_request_supervision_and_promote(self):
        p = replace(self.p, study_start_event='first_live_fill', study_end_utc=None)
        root = self.root / 'preflight'
        book = Ledger(root / 'state/book.sqlite', p, PaperBroker.identity)
        try:
            worker = Worker(book, p, PaperBroker(book, p), *(root / s for s in
                            ('state', 'outbox', 'inbox', 'market', 'research')))
            snap = worker.tick(NOW)
            self.assertIsNone(snap['study_deadline'])
            self.assertIsNotNone(book.get('supervision_request'))
            evidence = {'evidence_hash': 'synthetic-proof', 'challenger': CATALOG[1].version,
                        'source': 'coinbase_public'}
            from evolver.trading.history import digest
            history = {'source': 'coinbase_public', 'policy_hash': p.fingerprint, 'timestamp': NOW, 'histories': {'BTC-USD': []}}
            write_snapshot(root/'market/history.json', history)
            write_snapshot(root/'market/signals.json', history)
            evidence['history_binding'] = {'context': {}, 'source': 'coinbase_public', 'venue': None,
                                           'end': NOW-300, 'hashes': {'BTC-USD': digest([])}}
            request = worker.mailbox.put('promotion', {'policy_hash': p.fingerprint, 'evidence': evidence}, NOW, 30)
            with book.db:
                book.set('promotion_request', request)
            decision = {'verdict': 'approve', 'reason': 'synthetic test',
                        'evidence_hash': evidence['evidence_hash'], 'policy_hash': p.fingerprint}
            worker.mailbox.answer(request, {'analyst': decision, 'reviewer': decision}, NOW+1)
            worker.tick(NOW+2)
            self.assertEqual(book.get('active_strategy'), CATALOG[1].version)
            queued = len(list((root / 'outbox/requests').glob('*.json')))
            # Expiring the paper operating window must not spawn further AI work.
            worker.tick(NOW+90*86400)
            self.assertEqual(len(list((root / 'outbox/requests').glob('*.json'))), queued)
        finally:
            book.close()

    def test_budget_is_reserved_before_timeout_and_never_blindly_retried(self):
        limited = replace(self.p, max_model_calls_per_day=1)
        model = BudgetedModel(self.root / 'usage.sqlite', limited, 'gpt-6-luna', 'fake-test-key')
        with patch('urllib.request.urlopen', side_effect=TimeoutError) as fetch:
            with self.assertRaises(TimeoutError):
                model('JSON only', 'test')
            with self.assertRaises(RuntimeError):
                model('JSON only', 'test')
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(model.usage()['uncertain_calls'], 1)
        self.assertGreater(D(model.usage()['estimated_cost_usd']), 0)
        model.db.close()

    def test_model_has_no_network_call_when_key_is_missing(self):
        model = BudgetedModel(self.root / 'usage.sqlite', self.p, 'gpt-6-luna', '')
        with patch('urllib.request.urlopen') as fetch:
            with self.assertRaises(RuntimeError):
                model('JSON only', 'test')
            fetch.assert_not_called()
        model.db.close()

    def test_authentication_failure_blocks_repeated_calls_across_restart(self):
        from urllib.error import HTTPError
        path = self.root / 'auth-usage.sqlite'
        model = BudgetedModel(path, self.p, 'gpt-6-luna', 'fake-test-key')
        with patch('urllib.request.urlopen', side_effect=HTTPError('https://api.openai.com', 401, 'Unauthorized', {}, None)) as fetch:
            with self.assertRaises(HTTPError):
                model('JSON only', 'probe')
            model.db.close()
            model = BudgetedModel(path, self.p, 'gpt-6-luna', 'fake-test-key')
            with self.assertRaises(RuntimeError):
                model('JSON only', 'probe')
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(model.usage()['model_connection']['status'], 'blocked')
        model.db.close()
        replacement = BudgetedModel(path, self.p, 'gpt-6-luna', 'new-fake-key')
        self.assertEqual(replacement.access_status()['status'], 'unverified')
        replacement.db.close()

    def test_live_adapter_cannot_be_enabled_before_fee_verification(self):
        with self.assertRaisesRegex(BrokerError, 'live execution unavailable'):
            AlpacaBroker(replace(self.p, mode='live'), object(), 'fixture-account')
        with self.assertRaises(BrokerError):
            AlpacaHTTP('paper', 'fake', 'fake')

    def test_closed_candle_revisions_and_invalid_ohlc_are_rejected(self):
        bar = dict(timestamp=NOW//300*300-300, open='100', close='101', low='99', high='102', volume='20')
        self.assertEqual(merge_bars([bar], [dict(bar)], NOW), [bar])
        with self.assertRaises(ValueError):
            merge_bars([bar], [{**bar, 'volume': '21'}], NOW)
        with self.assertRaises(ValueError):
            merge_bars([], [{**bar, 'low': '110'}], NOW)

    def test_nanosecond_market_timestamps_are_normalized_without_relabeling_time(self):
        self.assertEqual(utc_timestamp('2026-10-04T18:20:02.760761771Z'),
                         utc_timestamp('2026-10-04T18:20:02.760761+00:00'))
        with self.assertRaises(ValueError):
            utc_timestamp('2026-10-04T18:20:02')

    def test_challenger_freezes_before_any_forward_validation(self):
        bars = [dict(timestamp=NOW+i*300, open='100', close='100', low='99', high='101', volume='1') for i in range(650)]
        def train_or_forward(spec, rows, p, start=0, end=float('inf')):
            if end != float('inf'):
                return [(bars[i]['timestamp'], .01) for i in range(20)]
            return []
        with patch('evolver.trading.learning.backtest', side_effect=train_or_forward):
            result = evaluate(self.p, {'BTC-USD': bars}, {}, CATALOG[0].version)
            self.assertEqual(result['cutoff'], bars[-1]['timestamp']+300)
            self.assertEqual(result['candidate']['trades'], 0)
            later = evaluate(self.p, {'BTC-USD': bars}, result, CATALOG[0].version)
            self.assertEqual(later['challenger'], result['challenger'])
            self.assertEqual(later['cutoff'], result['cutoff'])

    def test_three_losing_closes_roll_back_only_when_flat(self):
        with self.book.db:
            self.book.set('previous_strategy', CATALOG[1].version)
            self.book.set('promoted_at', NOW)
            for i in range(3):
                self.book.event(NOW+i, 'trade.closed', {'strategy': CATALOG[0].version, 'realized_pnl': '-.1'})
        self.worker._rollback(NOW+4)
        self.assertEqual(self.book.get('active_strategy'), CATALOG[1].version)
        self.assertEqual(self.book.get('supervisor')['action'], 'pause_entries')


if __name__ == '__main__':
    unittest.main()
