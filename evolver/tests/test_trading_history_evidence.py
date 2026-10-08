"""Fault/restart regressions for immutable candles and evidence-bound approvals."""
import copy
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from test_trading_runtime import NOW, approve, policy
import test_trading_orchestration as orchestration_fixture
import test_trading_universe as universe_fixture
from evolver.trading.agent_worker import process_request
from evolver.trading.contracts import Quote, encode
from evolver.trading.engine import write_snapshot
from evolver.trading.history import advance, binding_valid, context, digest, retain, window
from evolver.trading.runtime import Feed, research_tick
from evolver.trading.strategies import CATALOG


def candle(t, close="100"):
    return dict(timestamp=t, open="100", high="102", low="99", close=close, volume="20")


class HistoryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.p = policy(approved_strategies=tuple(s.version for s in CATALOG))
        self.old = [candle(NOW-300*(80-i)) for i in range(80)]
        self.provider = type("Provider", (), {"source": "coinbase_public", "venue": "coinbase"})()
        self.incoming = {s: copy.deepcopy(self.old) for s in self.p.allowed_instruments}
        self.provider.bars = lambda symbols, now: {s: self.incoming.get(s, []) for s in symbols}
        self.provider.bars_since = lambda symbols, now, start: self.provider.bars(symbols, now)
        self.feed = Feed.__new__(Feed); self.feed.policy=self.p; self.feed.target=self.root; self.feed.provider=self.provider
        write_snapshot(self.root/'history.json', {'source':'coinbase_public','venue':'coinbase',
            'histories':{s:copy.deepcopy(self.old) for s in self.p.allowed_instruments}})

    def tearDown(self):self.tmp.cleanup()

    def refresh(self, now):
        self.feed._refresh_bars(now, {'source':'coinbase_public','venue':'coinbase','timestamp':now,'policy_hash':self.p.fingerprint})
        return [json.loads((self.root/n).read_text()) for n in ['history.json','signals.json']]

    def revise(self):self.incoming['BTC-USD'][-1]['close']='101'

    def test_revision_preserves_original_and_appends_new_bars(self):
        self.revise(); self.incoming['BTC-USD'].append(candle(NOW))
        self.refresh(NOW+300);history, signals = self.refresh(NOW+360)
        self.assertEqual(history['histories']['BTC-USD'][:-1],self.old)
        self.assertEqual(history['histories']['BTC-USD'][-1]['timestamp'],NOW)
        self.assertEqual(signals['histories']['BTC-USD'],[])
        self.assertTrue(signals['histories']['ETH-USD'])
        records=[json.loads(p.read_text()) for p in (self.root/'history-evidence').glob('*.json')]
        self.assertTrue(any(r['identity']['kind']=='legacy_history' for r in records))
        revision=next(r for r in records if r['identity']['kind']=='closed_bar_revision')
        self.assertEqual(revision['identity']['original'],self.old[-1])
        self.assertEqual(revision['identity']['incoming']['close'],'101')

    def test_repeated_old_revision_and_restart_do_not_move_boundary(self):
        self.revise(); history,_=self.refresh(NOW)
        before=history['history_integrity']['BTC-USD'];evidence={p.name:p.read_bytes() for p in (self.root/'history-evidence').glob('*.json')}
        for step in [300,600]:
            self.incoming['BTC-USD'].append(candle(NOW+step-300))
            history,_=self.refresh(NOW+step)
            history,_=self.refresh(NOW+step+60)
            self.assertEqual(history['history_integrity']['BTC-USD']['cutoff'],before['cutoff'])
            self.assertEqual(history['history_integrity']['BTC-USD']['generation'],before['generation'])
        current={p.name:p.read_bytes() for p in (self.root/'history-evidence').glob('*.json')}
        self.assertTrue(all(current[k]==v for k,v in evidence.items()))
        self.assertEqual(sum(json.loads(v)['identity']['kind']=='closed_bar_revision' for v in current.values()),1)
        self.assertEqual(history['history_integrity']['BTC-USD']['clean_bars'],2)

    def test_recovery_uses_only_new_contiguous_observations(self):
        self.revise(); history,_=self.refresh(NOW)
        minimum=min(s.slow*3+1 for s in CATALOG)
        self.incoming['BTC-USD'] += [candle(NOW+i*300) for i in range(minimum-1)]
        self.refresh(NOW+(minimum-1)*300);history,signals=self.refresh(NOW+(minimum-1)*300+60)
        self.assertIn('BTC-USD',signals['history_errors'])
        self.incoming['BTC-USD'].append(candle(NOW+(minimum-1)*300))
        self.refresh(NOW+minimum*300);history,signals=self.refresh(NOW+minimum*300+60)
        self.assertNotIn('BTC-USD',signals['history_errors'])
        self.assertTrue(all(b['timestamp']>=NOW for b in signals['histories']['BTC-USD']))
        self.assertEqual(history['histories']['BTC-USD'][:len(self.old)],self.old)
        _,reason,_=window(signals,'BTC-USD',CATALOG[0].slow*3+1,NOW+minimum*300)
        self.assertEqual(reason,'insufficient_contiguous_closed_bars')

    def test_invalid_bar_does_not_discard_valid_new_observations(self):
        self.incoming['BTC-USD'] += [dict(candle(NOW),low='200'),candle(NOW+300)]
        self.refresh(NOW+600);history,signals=self.refresh(NOW+660)
        self.assertEqual(history['histories']['BTC-USD'][-1],candle(NOW+300))
        self.assertFalse(any(b['timestamp']==NOW for b in history['histories']['BTC-USD']))
        self.assertIn('BTC-USD',signals['history_errors'])

    def test_gap_never_becomes_a_contiguous_signal_window(self):
        history,signals=self.refresh(NOW)
        signals['histories']['BTC-USD']=self.old[:-4]+self.old[-2:]
        _,reason,binding=window(signals,'BTC-USD',64,NOW)
        self.assertEqual(reason,'insufficient_contiguous_closed_bars')
        self.assertEqual(binding['first_bar'],self.old[-2]['timestamp'])

    def test_legacy_quarantine_is_preserved_on_first_adoption(self):
        raw=json.loads((self.root/'history.json').read_text());raw['history_errors']={'BTC-USD':'old_error'}
        write_snapshot(self.root/'history.json',raw)
        history,signals=self.refresh(NOW)
        self.assertEqual(history['histories']['BTC-USD'],self.old)
        self.assertEqual(history['history_integrity']['BTC-USD']['cutoff'],NOW)
        self.assertIn('BTC-USD',signals['history_errors'])

    def test_failed_evidence_write_never_publishes_recovered_history(self):
        before=(self.root/'history.json').read_bytes();self.revise()
        with patch('evolver.trading.history.retain',side_effect=OSError('fixture disk full')):
            with self.assertRaises(OSError):self.refresh(NOW)
        self.assertEqual((self.root/'history.json').read_bytes(),before)
        self.assertFalse((self.root/'signals.json').exists())

    def test_evidence_written_before_projection_crash_still_quarantines(self):
        self.revise()
        with patch('evolver.trading.runtime.write_snapshot',side_effect=OSError('fixture crash')):
            with self.assertRaises(OSError):self.refresh(NOW)
        history,signals=self.refresh(NOW+300)
        self.assertIn('BTC-USD',signals['history_errors'])
        self.assertGreaterEqual(history['history_integrity']['BTC-USD']['cutoff'],NOW)

    def test_uncommitted_revision_requires_two_agreeing_observations(self):
        self.incoming['BTC-USD'].append(candle(NOW));history,_=self.refresh(NOW+300)
        self.assertEqual(history['histories']['BTC-USD'],self.old)
        self.incoming['BTC-USD'][-1]['close']='101';history,_=self.refresh(NOW+360)
        self.assertEqual(history['histories']['BTC-USD'],self.old)
        history,signals=self.refresh(NOW+419)
        self.assertEqual(history['histories']['BTC-USD'],self.old)
        history,signals=self.refresh(NOW+420)
        self.assertEqual(history['histories']['BTC-USD'][-1]['close'],'101')
        self.assertEqual(history['history_integrity']['BTC-USD']['cutoff'],0)
        records=[json.loads(p.read_text()) for p in (self.root/'history-evidence').glob('*.json')]
        versions=[r for r in records if r['identity']['kind']=='uncommitted_closed_bars']
        self.assertEqual(len(versions),2)


class ApprovalEvidenceTests(unittest.TestCase):
    setUp=orchestration_fixture.OrchestrationTests.setUp
    tearDown=orchestration_fixture.OrchestrationTests.tearDown
    market=orchestration_fixture.OrchestrationTests.market

    def data(self):return json.loads((self.root/'market/signals.json').read_text())
    def save(self,value):write_snapshot(self.root/'market/signals.json',value)
    def queue(self):
        with patch('evolver.trading.worker.make_intents',return_value=[self.buy]):self.worker.tick(NOW)
        return self.book.get('pending_reviews')[self.buy.client_id]['request']
    def answer(self,request):self.worker.mailbox.answer(request,process_request(request,self.p,approve,approve,NOW),NOW+.1)

    def test_supervisor_sees_real_candidate_while_execution_is_paused(self):
        data=self.data();data['histories']['BTC-USD'][-1]['close']='101';self.save(data)
        with self.book.db:self.book.set('supervisor',dict(action='pause_entries',scale='1',issued_at=NOW-600,expires_at=NOW))
        snapshot=self.worker.tick(NOW)
        request=self.book.get('supervision_request');facts=request['body']['snapshot'];card=facts['market_evidence']['symbols']['BTC-USD']
        self.assertTrue(card['entry_signal']);self.assertIsNotNone(card['candidate'])
        self.assertEqual(card['candidate']['execution_block'],'supervisor_paused_or_expired')
        self.assertEqual(len(card['recent_candles']),2)
        self.assertEqual(len(card['indicators']),4)
        self.assertEqual(self.book.orders(),[])
        evidence=self.root/'outbox/candle-evidence'/ (facts['market_evidence']['candle_evidence_hash']+'.json')
        archived=json.loads(evidence.read_text())['identity']
        self.assertEqual(digest(archived['windows']['BTC-USD']),card['history_binding']['window_hash'])
        self.assertTrue(binding_valid(facts['history_binding'],data,self.p,NOW))

    def test_six_candidates_and_full_news_fit_unchanged_input_budget(self):
        from evolver.trading.broker import PaperBroker
        from evolver.trading.ledger import Ledger
        from evolver.trading.worker import Worker
        from evolver.trading.news import item
        p=replace(self.p,allowed_instruments=universe_fixture.SIX,trading_hours_utc=tuple(range(24)),
                  trading_weekdays_utc=tuple(range(7)),max_model_calls_per_day=200,max_trades_per_day=24)
        root=self.root/'six';book=Ledger(root/'book.sqlite',p,PaperBroker.identity)
        try:
            worker=Worker(book,p,PaperBroker(book,p),*(root/s for s in ['state','outbox','inbox','market','research']))
            quotes={s:Quote(s,'99.9','100',NOW) for s in p.allowed_instruments}
            bars=[candle(NOW-300*(64-i),'101' if i==63 else '100') for i in range(64)]
            data={'source':'coinbase_public','timestamp':NOW,'policy_hash':p.fingerprint,
                  'histories':dict.fromkeys(p.allowed_instruments,bars)}
            write_snapshot(root/'market/signals.json',data)
            snapshot=worker.engine.snapshot(quotes,NOW);facts=worker._facts(snapshot)
            facts['news']['items']=[item('alpaca_crypto',str(i)*100,'X'*220,'https://example.org/'+str(i)*380,
                                        NOW-120,NOW-60,['BTC-USD','ETH-USD'],NOW) for i in range(10)]
            facts['recent_strategy_outcomes']=[{'lot_id':str(i)*64,'instrument':'BTC-USD','strategy':CATALOG[0].version,
                'opened':NOW-3600,'closed':NOW-600,'quantity':'0.00250000','cost_basis':'25.00000000',
                'proceeds':'25.08000000','realized_pnl':'0.08000000'} for i in range(6)]
            self.assertEqual(sum(bool(c['candidate']) for c in facts['market_evidence']['symbols'].values()),6)
            def model(system,prompt):
                self.assertLessEqual(len((system+prompt).encode()),23850)  # keep >=150 B headroom under the 24 KB hard cap
                decoded=json.loads(prompt);value=decoded['snapshot']
                self.assertEqual(value['news']['items'],facts['news']['items'])
                if 'intent' in decoded:
                    self.assertEqual(set(value['market_evidence']['symbols']),{'BTC-USD'})
                    return encode({**decoded['required_output'],'verdict':'reject','reason':'fixture'})
                return encode({'action':'pause_entries','risk_scale':'1','reason':'fixture'})
            process_request({'kind':'supervision','body':{'policy_hash':p.fingerprint,'snapshot':facts}},p,model,model,NOW)
            process_request({'kind':'trade','body':{'policy_hash':p.fingerprint,'snapshot':facts,'intent':asdict(self.buy)}},p,model,model,NOW)
            self.assertEqual(len(facts['history_binding']['symbols']),6)
            self.assertEqual(len(facts['market_evidence']['symbols']),6)
        finally:book.close()

    def test_quarantine_invalidates_unanswered_review_and_agent_work(self):
        request=self.queue();data=self.data();data['history_errors']={'BTC-USD':'history_recovery_warmup'};self.save(data)
        self.worker.tick(NOW+1)
        self.assertEqual(self.book.get('pending_reviews'),{})
        self.assertEqual(self.book.orders(),[])
        self.assertNotIn(request['id'],[r['id'] for r in self.worker.mailbox.pending(NOW+1)])
        self.assertTrue((self.root/'outbox/invalidations'/(request['id']+'.json')).exists())

    def test_changed_raw_candle_rejects_old_approval_without_metadata_change(self):
        request=self.queue();self.answer(request);data=self.data();data['histories']['BTC-USD'][-2]['close']='100.5';self.save(data)
        self.worker.tick(NOW+1)
        self.assertEqual(self.book.orders(),[])
        self.assertIsNone(self.worker.mailbox.result(request,NOW+1))

    def test_unrelated_quarantine_does_not_invalidate_exact_btc_review(self):
        request=self.queue();self.answer(request);data=self.data();data['history_errors']={'ETH-USD':'history_recovery_warmup'};self.save(data)
        self.worker.tick(NOW+1)
        self.assertEqual(len(self.book.orders()),1)

    def test_legacy_approval_without_candle_binding_fails_closed(self):
        request=self.queue();item=self.book.get('pending_reviews')[self.buy.client_id]
        old_body=copy.deepcopy(request['body']);old_body['snapshot'].pop('history_binding')
        request=self.worker.mailbox.put('trade',old_body,NOW,30);item['request']=request
        with self.book.db:self.book.set('pending_reviews',{self.buy.client_id:item})
        self.answer(request);self.worker.tick(NOW+1)
        self.assertEqual(self.book.orders(),[])
        self.assertIsNone(self.worker.mailbox.result(request,NOW+1))

    def test_pending_supervisor_resume_cannot_outlive_changed_history(self):
        with self.book.db:self.book.set('supervisor',dict(action='pause_entries',scale='1',issued_at=NOW-600,expires_at=NOW))
        self.worker.tick(NOW);request=self.book.get('supervision_request')
        command=dict(action='resume_entries',risk_scale='1',policy_hash=self.p.fingerprint,issued_at=NOW,expires_at=NOW+600,reason='fixture')
        self.worker.mailbox.answer(request,command,NOW+.1)
        data=self.data();data['history_errors']={'BTC-USD':'history_recovery_warmup'};self.save(data)
        self.worker.tick(NOW+1)
        self.assertEqual(self.book.get('supervisor')['action'],'pause_entries')
        self.assertEqual(self.book.orders(),[])

    def test_pending_supervisor_pause_is_safe_when_history_changes(self):
        with self.book.db:self.book.set('supervisor',dict(action='pause_entries',scale='1',issued_at=NOW-600,expires_at=NOW))
        self.worker.tick(NOW);request=self.book.get('supervision_request')
        command=dict(action='pause_entries',risk_scale='1',policy_hash=self.p.fingerprint,issued_at=NOW,expires_at=NOW+600,reason='fixture')
        self.worker.mailbox.answer(request,command,NOW+.1)
        data=self.data();data['history_errors']={'BTC-USD':'history_recovery_warmup'};self.save(data)
        self.worker.tick(NOW+1)
        self.assertEqual(self.book.get('supervisor')['expires_at'],NOW+600)

    def test_reduce_risk_cannot_reopen_entries_from_changed_history(self):
        with self.book.db:self.book.set('supervisor',dict(action='pause_entries',scale='1',issued_at=NOW-600,expires_at=NOW))
        self.worker.tick(NOW);request=self.book.get('supervision_request')
        command=dict(action='reduce_risk',risk_scale='.5',policy_hash=self.p.fingerprint,issued_at=NOW,expires_at=NOW+600,reason='fixture')
        self.worker.mailbox.answer(request,command,NOW+.1)
        data=self.data();data['history_errors']={'BTC-USD':'history_recovery_warmup'};self.save(data)
        self.worker.tick(NOW+1)
        self.assertEqual(self.book.get('supervisor')['action'],'pause_entries')
        self.assertEqual(self.book.get('supervisor')['scale'],'1')
        self.assertIsNone(self.worker.mailbox.result(request,NOW+1))

    def test_trade_request_keeps_the_history_that_created_its_signal(self):
        before=self.data();after=copy.deepcopy(before);after['histories']['BTC-USD'][-1]['close']='101'
        with patch.object(self.worker,'_history_data',side_effect=[before,after]), patch('evolver.trading.worker.make_intents',return_value=[self.buy]):
            self.worker.tick(NOW)
        request=self.book.get('pending_reviews')[self.buy.client_id]['request']
        self.assertTrue(binding_valid(request['body']['snapshot']['history_binding'],before,self.p,NOW))
        self.assertFalse(binding_valid(request['body']['snapshot']['history_binding'],after,self.p,NOW))

    def test_accepted_positive_lease_pauses_on_lost_provenance(self):
        snapshot=self.worker.engine.snapshot({'BTC-USD':self.q},NOW)
        facts=self.worker._facts(snapshot)
        with self.book.db:self.book.set('supervision_history_binding',facts['history_binding'])
        data=self.data();data['history_integrity']={'BTC-USD':{'cutoff':NOW,'generation':'new'}};self.save(data)
        self.worker.tick(NOW+1)
        self.assertEqual(self.book.get('supervisor')['action'],'pause_entries')
        self.assertEqual(self.book.get('supervisor')['scale'],'1')

    def test_final_history_guard_blocks_change_during_model_review(self):
        engine=self.worker.engine;engine.analyst=engine.reviewer=approve
        engine.context_valid=lambda intent,now:False
        engine.tick({'BTC-USD':self.q},[self.buy],NOW)
        self.assertEqual(self.book.orders(),[])
        self.assertTrue(any(e['event']=='review.history_changed_before_submit' for e in self.book.recent_events()))

    def test_pending_promotion_rejects_new_integrity_boundary(self):
        from evolver.trading.history import digest
        data=json.loads((self.root/'market/history.json').read_text())
        evidence={'evidence_hash':'fixture','challenger':CATALOG[1].version,'source':'coinbase_public',
            'history_binding':{'context':{},'source':'coinbase_public','venue':None,'end':NOW-300,
                               'hashes':{s:digest(b) for s,b in data['histories'].items()}}}
        request=self.worker.mailbox.put('promotion',{'policy_hash':self.p.fingerprint,'evidence':evidence},NOW,120)
        with self.book.db:self.book.set('promotion_request',request)
        decision=dict(verdict='approve',reason='fixture',policy_hash=self.p.fingerprint,evidence_hash='fixture')
        self.worker.mailbox.answer(request,{'analyst':decision,'reviewer':decision},NOW+.1)
        data['history_integrity']={'BTC-USD':{'cutoff':NOW,'generation':'new'}}
        write_snapshot(self.root/'market/history.json',data);self.worker.tick(NOW+1)
        self.assertEqual(self.book.get('active_strategy'),CATALOG[0].version)
        self.assertIsNone(self.book.get('promotion_request'))

    def test_research_replaces_stale_label_without_destroying_old_assessment(self):
        paths={s:self.root/s for s in ['market','research','outbox']}
        prior=dict(policy_hash=self.p.fingerprint,status='review_required',evidence_hash='old',end=NOW-600)
        write_snapshot(paths['research']/'assessment.json',prior)
        data=json.loads((paths['market']/'history.json').read_text())
        data.update(history_errors={'BTC-USD':'history_recovery_warmup'},history_integrity={'BTC-USD':{'cutoff':NOW,'generation':'new'}})
        write_snapshot(paths['market']/'history.json',data)
        research_tick(self.p,paths,NOW)
        current=json.loads((paths['research']/'assessment.json').read_text())
        self.assertEqual(current['status'],'blocked_history_integrity')
        self.assertEqual(current['evaluated_at'],NOW)
        self.assertEqual(current['cutoff'],NOW+300)
        self.assertEqual([json.loads(p.read_text()) for p in paths['research'].glob('assessment-*.json')],[prior])


class VirtualHistoryBoundaryTests(unittest.TestCase):
    setUp=universe_fixture.UniverseExperimentTests.setUp
    tearDown=universe_fixture.UniverseExperimentTests.tearDown

    def test_boundary_blocks_old_pending_buys_without_rewriting_history(self):
        now=universe_fixture.NOW
        self.exp.apply(universe_fixture.six_frame(signals=universe_fixture.SIX))
        before=copy.deepcopy(self.exp.state());prefix=[tuple(r) for r in self.exp.db.execute('SELECT * FROM experiment_events ORDER BY seq')]
        f=universe_fixture.six_frame(now+5,signals=universe_fixture.SIX)
        f['history_cutoffs']={s:now for s in universe_fixture.SIX}
        result=self.exp.apply(f)
        self.assertTrue(all(b['fills']==0 and b['cash']=='500' for b in result['books']))
        self.assertEqual(self.exp.state()['histories'],before['histories'])
        self.assertEqual(self.exp.identity,self.origin)
        self.assertEqual([tuple(r) for r in self.exp.db.execute('SELECT * FROM experiment_events ORDER BY seq')][:len(prefix)],prefix)
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_old_history_cannot_complete_a_short_recovery_window(self):
        now=universe_fixture.NOW
        self.exp.apply(universe_fixture.six_frame())
        f=universe_fixture.six_frame(now+600)
        f['history_cutoffs']={s:now+300 for s in universe_fixture.SIX}
        f['bars']={s:[candle(now+300,'101')] for s in universe_fixture.SIX}
        self.exp.apply(f)
        self.assertFalse(self.exp.state()['opportunities'])
        self.assertTrue(all(not b['pending'] for b in self.exp.state()['books'].values()))
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_recovery_boundary_cannot_rewind(self):
        from evolver.trading.experiment import IntegrityError
        now=universe_fixture.NOW
        f=universe_fixture.six_frame();f['history_cutoffs']={s:now for s in universe_fixture.SIX};self.exp.apply(f)
        f=universe_fixture.six_frame(now+5);f['history_cutoffs']={s:0 for s in universe_fixture.SIX}
        with self.assertRaises(IntegrityError):self.exp.apply(f)

    def test_recovered_signal_cannot_bridge_a_gap_in_retained_history(self):
        now=universe_fixture.NOW
        f=universe_fixture.six_frame(signals=universe_fixture.SIX)
        f['history_cutoffs']=dict.fromkeys(universe_fixture.SIX,0)
        for bars in f['bars'].values():del bars[-3]
        self.exp.apply(f)
        self.assertFalse(self.exp.state()['opportunities'])
        self.assertTrue(all(not b['pending'] for b in self.exp.state()['books'].values()))
        self.assertTrue(self.exp.verify_replay()['verified'])

    def test_history_recovery_does_not_disable_protective_exits(self):
        now=universe_fixture.NOW
        self.exp.apply(universe_fixture.six_frame(signals=('BTC-USD',)))
        self.exp.apply(universe_fixture.six_frame(now+5,signals=('BTC-USD',)))
        self.assertTrue(self.exp.state()['books']['baseline']['positions'])
        f=universe_fixture.six_frame(now+10,signals=('BTC-USD',))
        f['history_cutoffs']={s:now+300 for s in universe_fixture.SIX}
        f['history_unavailable_symbols']=['BTC-USD']
        for q in f['quotes'].values():q.update(bid='90',ask='90.05')
        self.exp.apply(f)
        order=self.exp.state()['books']['baseline']['pending']['BTC-USD']
        self.assertEqual((order['side'],order['reason']),('sell','protective_exit'))
        self.assertTrue(self.exp.verify_replay()['verified'])
