"""Offline rejection, incident recovery, replay, and unchanged execution boundaries."""
import copy
from contextlib import redirect_stdout
from dataclasses import asdict
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from evolver.trading.contracts import Quote, encode
from evolver.trading.experiment import Experiment, IntegrityError, digest
from evolver.trading.experiment_runner import main
from evolver.trading.quote_admission import publish
from evolver.trading.runtime import Feed
from test_trading_experiment import NOW, SPEC, frame, policy
from test_trading_evidence_v2 import activate
from test_trading_universe import SIX, expand, expanded_policy, six_frame


def batch(at=NOW, quotes=None):
    return {'timestamp': at, 'source': 'test_fixture', 'venue': 'test_fixture',
            'policy_hash': expanded_policy().fingerprint,
            'quotes': quotes if quotes is not None else six_frame(at)['quotes']}


class QuoteAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original = batch()
        publish(self.root, self.original, SIX)
        self.accepted = (self.root/'quotes.json').read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def test_future_rewind_revision_and_unexpected_assets_reject_entire_batch(self):
        for kind in ('future', 'rewind', 'revision', 'unexpected', 'instrument', 'receipt', 'source', 'venue', 'policy_hash'):
            with self.subTest(kind=kind):
                incoming = batch(NOW+5)
                q = incoming['quotes']['SKY-USD']
                if kind == 'future': q['timestamp'] = NOW+5.001
                elif kind == 'rewind': q['timestamp'] = NOW-.001
                elif kind == 'revision': q.update(timestamp=NOW, bid='99')
                elif kind == 'unexpected': incoming['quotes']['NOPE-USD'] = copy.deepcopy(q)
                elif kind == 'instrument': q['instrument'] = 'NOPE-USD'
                elif kind == 'receipt': incoming['timestamp'] = NOW-1
                else: incoming[kind] = 'changed'
                with self.assertRaisesRegex(ValueError, 'quote_batch_rejected'):
                    publish(self.root, incoming, SIX)
                self.assertEqual((self.root/'quotes.json').read_bytes(), self.accepted)
                records = [json.loads(p.read_text())['identity'] for p in (self.root/'quote-rejections').glob('*.json')]
                found = [r for r in records if r['batch'] == incoming]
                self.assertEqual(len(found), 1)
                self.assertTrue(found[0]['violations'])
                self.assertEqual(found[0]['previous'], json.loads(self.accepted))

    def test_numeric_equivalence_and_stale_prices_keep_original_provider_time(self):
        incoming = batch(NOW+120, copy.deepcopy(self.original['quotes']))
        incoming['quotes']['BTC-USD']['bid'] = '100.0000'
        publish(self.root, incoming, SIX)
        saved = json.loads((self.root/'quotes.json').read_text())
        self.assertEqual(saved['quotes']['BTC-USD']['timestamp'], NOW)
        self.assertGreater(saved['timestamp']-saved['quotes']['BTC-USD']['timestamp'], 30)

    def test_absent_asset_watermark_survives_restart_and_reappearance(self):
        incoming = batch(NOW+5)
        del incoming['quotes']['SKY-USD']
        publish(self.root, incoming, SIX)
        before = (self.root/'quotes.json').read_bytes()
        incoming = batch(NOW+10)
        incoming['quotes']['SKY-USD']['timestamp'] = NOW-1
        with self.assertRaisesRegex(ValueError, 'quote_batch_rejected'):
            publish(Path(str(self.root)), incoming, SIX)
        self.assertEqual((self.root/'quotes.json').read_bytes(), before)

    def test_rejections_are_immutable_and_archive_failure_is_fail_closed(self):
        bad = batch(NOW+1)
        bad['quotes']['ETH-USD']['timestamp'] = NOW+2
        with self.assertRaises(ValueError): publish(self.root, bad, SIX)
        files = {p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in (self.root/'quote-rejections').glob('*.json')}
        with self.assertRaises(ValueError): publish(self.root, bad, SIX)
        self.assertEqual(files, {p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in (self.root/'quote-rejections').glob('*.json')})
        for target, value in [('retain', OSError('disk failure')), ('MAX_ARCHIVE_BYTES', 0)]:
            if target == 'retain':
                context = patch('evolver.trading.quote_admission.retain', side_effect=value)
            else: context = patch('evolver.trading.quote_admission.'+target, value)
            with context, self.assertRaises((OSError, ValueError)):
                publish(self.root, bad, SIX)
            self.assertEqual((self.root/'quotes.json').read_bytes(), self.accepted)

    def test_feed_uses_guard_before_publication_or_bar_refresh(self):
        feed = Feed.__new__(Feed)
        feed.target, feed.policy = self.root, expanded_policy()
        feed.provider = Mock(source='test_fixture', venue='test_fixture')
        feed.provider.quotes.return_value = {s: Quote(s, '100', '100.05', NOW+2) for s in SIX}
        feed.increments, feed.instrument_rules = {}, {}
        feed.next_bars, feed.bar_thread, feed.background = 0, None, False
        feed._refresh_bars = Mock()
        with patch('evolver.trading.runtime.time.time', return_value=NOW+1), self.assertRaises(ValueError):
            feed.tick(NOW+1)
        feed._refresh_bars.assert_not_called()
        self.assertEqual((self.root/'quotes.json').read_bytes(), self.accepted)


class QuoteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root/'experiment.sqlite'
        self.exp = Experiment(self.path, policy(), epoch=NOW-120, strategy=SPEC.version)
        self.exp.apply(frame(NOW-120))
        activate(self.exp, NOW-119)
        expand(self.exp)
        good = six_frame(signals=SIX)
        good['context']['data_entry_allowed'] = False
        self.exp.apply(good)
        self.exp.policy = expanded_policy()
        self.bad = six_frame(NOW+5)
        self.bad['quotes']['SKY-USD']['timestamp'] = NOW-1
        with self.assertRaises(IntegrityError): self.exp.apply(self.bad)
        self.failed = self.exp.state()
        self.prefix = self.rows()
        self.review = self.make_review()

    def tearDown(self):
        self.exp.close()
        self.tmp.cleanup()

    def rows(self):
        return [tuple(r) for r in self.exp.db.execute('SELECT * FROM experiment_events ORDER BY seq')]

    def make_review(self):
        tail = self.exp.db.execute('SELECT * FROM experiment_events ORDER BY seq DESC LIMIT 1').fetchone()
        failure = json.loads(tail['payload'])
        return {'id': 'approved-incident', 'reviewed_by': 'fixture_owner',
                'reason': 'Explicitly reviewed prospective restart with no capital or clock reset',
                'original_cause': 'unknown_original_frame_not_retained', 'evidence_hash': 'a'*64,
                'identity_hash': digest(self.exp.identity), 'policy_hash': self.exp.policy_hash(self.exp.state()),
                'failed_state_hash': digest(self.exp.state()),
                'failure': {'seq':tail['seq'], 'id':tail['id'], 'hash':tail['hash'],
                            'reason':failure['reason'], 'rejected_hash':failure['rejected_hash']}}

    def recover(self, review=None, market=None, at=NOW+120):
        return self.exp.recover_quotes(review or self.review, market or batch(at), at)

    def test_full_rejected_event_is_retained_transactionally_and_replayed(self):
        failure = json.loads(self.prefix[-1][-1])
        self.assertEqual(failure['rejected_event'], self.bad)
        self.assertEqual(failure['rejected_hash'], digest(self.bad))
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_append_only_recovery_preserves_books_clock_identity_blocks_and_old_failure(self):
        identity = copy.deepcopy(self.exp.identity)
        report = self.recover()
        state = self.exp.state()
        self.assertEqual(self.rows()[:-1], self.prefix)
        for field in ('books', 'histories', 'quotes', 'frames', 'universe', 'evidence_last_observation_at'):
            self.assertEqual(state[field], self.failed[field])
        self.assertEqual(state['evidence']['blocks'], self.failed['evidence']['blocks'])
        self.assertEqual(self.exp.identity, identity)
        self.assertEqual(report['epoch'], NOW-120)
        self.assertEqual(report['evaluation_end'], NOW-120+90*86400)
        self.assertFalse(report['halt'])
        self.assertFalse(report['evidence_quality']['complete_so_far'])
        self.assertTrue(all(not q['history_available'] for q in report['quote_status'].values()))
        self.assertEqual(report['last_market_observation_at'], NOW)
        saved_event = json.loads(self.rows()[-1][-1])
        self.assertEqual(self.exp.apply(saved_event), report)
        self.assertEqual(len(self.rows()), len(self.prefix)+1)
        self.exp.close()
        self.exp = Experiment(self.path, expanded_policy())
        self.assertEqual(self.exp.report(), report)
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_wrong_or_missing_bindings_leave_original_halt_and_journal_unchanged(self):
        for field in ('failed_state_hash', 'identity_hash', 'policy_hash', 'evidence_hash', 'reviewed_by'):
            with self.subTest(field=field):
                bad = copy.deepcopy(self.review); bad[field] = ''
                with self.assertRaises((IntegrityError, ValueError)): self.recover(review=bad)
                self.assertEqual(self.exp.state(), self.failed)
                self.assertEqual(self.rows(), self.prefix)
        for field in ('seq', 'hash', 'id', 'rejected_hash', 'reason'):
            bad = copy.deepcopy(self.review); bad['failure'][field] = 'wrong'
            with self.assertRaises(IntegrityError): self.recover(review=bad)
        bad = copy.deepcopy(self.review); del bad['failure']
        with self.assertRaises((IntegrityError, KeyError)): self.recover(review=bad)
        self.assertEqual(self.rows(), self.prefix)

    def test_legacy_failure_replays_and_unknown_cause_cannot_be_fabricated(self):
        # Construct the old three-field format on this synthetic fixture only.
        tail = json.loads(self.prefix[-1][-1])
        legacy = {k:tail[k] for k in ('type','reason','rejected_hash')}
        with self.exp.db:
            self.exp.db.execute('UPDATE experiment_events SET hash=?,payload=? WHERE seq=?',
                                (digest(legacy),encode(legacy),self.prefix[-1][0]))
        self.assertTrue(self.exp.verify_replay()['verified'])
        review = self.make_review(); review['original_cause'] = 'invented_provider_cause'
        before = self.rows()
        with self.assertRaisesRegex(IntegrityError, 'unknown_original_cause'): self.recover(review=review)
        self.assertEqual(self.rows(), before)
        self.recover(review=self.make_review())
        self.assertEqual(self.rows()[:-1], before)
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_fresh_complete_monotonic_quotes_are_mandatory_for_recovery(self):
        for kind in ('stale', 'future', 'rewind', 'revision', 'missing', 'source', 'policy', 'venue', 'receipt'):
            with self.subTest(kind=kind):
                market = batch(NOW+120)
                q = market['quotes']['ADA-USD']
                if kind == 'stale': q['timestamp'] = NOW+89
                elif kind == 'future': q['timestamp'] = NOW+120.001
                elif kind == 'rewind': q['timestamp'] = NOW-1
                elif kind == 'revision': q.update(timestamp=NOW, bid='99')
                elif kind == 'missing': del market['quotes']['ADA-USD']
                elif kind == 'receipt': market['timestamp'] = NOW+121
                else: market[{'policy':'policy_hash'}.get(kind,kind)] = 'wrong'
                with self.assertRaises(IntegrityError): self.recover(market=market)
                self.assertEqual(self.rows(), self.prefix)
                self.assertEqual(self.exp.state(), self.failed)

    def test_nonflat_pending_fills_capital_or_risk_halts_cannot_be_overridden(self):
        changes = {'cash':'499', 'positions':{'BTC-USD':'lot'}, 'pending':{'BTC-USD':{}},
                   'fills':{'fill':{'settled':False}}, 'lots':{'lot':{}}, 'halt':'risk_limit', 'daily_halt':True}
        for field, value in changes.items():
            with self.subTest(field=field):
                altered = copy.deepcopy(self.failed); altered['books']['baseline'][field] = value
                with self.exp.db: self.exp._save(altered)
                with self.assertRaisesRegex(IntegrityError, 'untouched_flat_books'): self.recover(review=self.make_review())
                self.assertEqual(self.exp.state(), altered)
                self.assertEqual(self.rows(), self.prefix)
        with self.exp.db: self.exp._save(self.failed)
        for at in (NOW, NOW-1, NOW-120+90*86400):
            with self.assertRaises(IntegrityError): self.recover(at=at)
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_old_signals_are_excluded_gap_visible_and_only_new_evidence_can_enter(self):
        self.recover()
        later = six_frame(NOW+121); later['bars'] = {}
        later['history_cutoffs'] = dict.fromkeys(SIX, 0)
        self.exp.apply(later)
        state = self.exp.state()
        self.assertTrue(all(not b['pending'] and not b['fills'] for b in state['books'].values()))
        self.assertIn('observation_gap', state['evidence']['invalid_reasons'])
        self.assertGreater(state['evidence']['coverage']['max_gap_seconds'], 60)
        cutoff = state['recovery_history_cutoff']
        needed = SPEC.slow*3+1
        at = cutoff+needed*300
        new = six_frame(at, signals=('BTC-USD',))
        new['bars'] = {s:[b for b in bars if b['timestamp'] >= cutoff] for s,bars in new['bars'].items()}
        self.exp.apply(new)
        self.assertIn('BTC-USD', self.exp.state()['books']['baseline']['pending'])
        self.assertFalse(self.exp.state()['books']['baseline']['fills'])
        next_quote = six_frame(at+5); next_quote['bars'] = {}
        self.exp.apply(next_quote)
        self.assertTrue(self.exp.state()['books']['baseline']['fills'])
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_subsequent_bad_quote_still_halts_and_old_review_cannot_be_reused(self):
        self.recover()
        bad = six_frame(NOW+125); bad['bars'] = {}
        bad['quotes']['SKY-USD']['timestamp'] = NOW+126
        with self.assertRaisesRegex(IntegrityError, 'future_or_rewound_quote'): self.exp.apply(bad)
        before = self.rows()
        with self.assertRaises(IntegrityError): self.recover(at=NOW+130)
        self.assertEqual(self.rows(), before)
        self.assertEqual(json.loads(before[-1][-1])['rejected_event'], bad)
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_first_resumed_quote_cannot_rewind_behind_the_reviewed_snapshot(self):
        self.recover()
        bad = six_frame(NOW+125); bad['bars'] = {}
        bad['quotes']['SKY-USD']['timestamp'] = NOW+119
        with self.assertRaisesRegex(IntegrityError, 'future_or_rewound_quote'): self.exp.apply(bad)
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_replay_rechecks_recovery_binding_even_if_row_hash_is_recomputed(self):
        self.recover()
        row = self.rows()[-1]
        bad = json.loads(row[-1]); bad['review']['failure']['seq'] -= 1
        with self.exp.db:
            self.exp.db.execute('UPDATE experiment_events SET payload=?,hash=? WHERE seq=?', (encode(bad),digest(bad),row[0]))
        with self.assertRaisesRegex(IntegrityError, 'failure_binding_mismatch'): self.exp.verify_replay()

    def test_explicit_cli_requires_review_and_preserves_full_replay(self):
        config = self.root/'policy.json'; config.write_text(encode(asdict(expanded_policy())))
        source = self.root.parent/(self.root.name+'-source')
        # Keep all fixture paths managed by a separate temporary directory.
        with tempfile.TemporaryDirectory() as source_dir:
            source = Path(source_dir); (source/'market').mkdir()
            (source/'market/quotes.json').write_text(encode(batch(NOW+120)))
            review = self.root/'review.json'; review.write_text(encode(self.review))
            with patch('evolver.trading.experiment_runner.time.time', return_value=NOW+120), redirect_stdout(StringIO()):
                self.assertEqual(main(['recover-quotes','--root',str(self.root),'--source-root',str(source),
                    '--policy',str(config),'--recovery-review',str(review)]), 0)
        self.assertEqual(self.rows()[:-1], self.prefix)
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_halted_runner_idles_without_refreshing_time_or_reading_new_input(self):
        config = self.root/'policy.json'; config.write_text(encode(asdict(expanded_policy())))
        stop = Mock(); stop.is_set.return_value = False; stop.wait.return_value = True
        with patch('evolver.trading.experiment_runner.threading.Event', return_value=stop), \
                patch('evolver.trading.experiment_runner.signal.signal'), \
                patch('evolver.trading.experiment_runner.capture') as capture, redirect_stdout(StringIO()):
            self.assertEqual(main(['run','--root',str(self.root),'--source-root',str(self.root)+'-source',
                                   '--policy',str(config)]), 0)
        capture.assert_not_called()
        stop.wait.assert_called_once_with(30)
        self.assertEqual(self.exp.state(), self.failed)
        self.assertEqual(self.rows(), self.prefix)


if __name__ == '__main__':
    unittest.main()
