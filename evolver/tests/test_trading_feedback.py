import copy
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evolver.trading.agent_worker import process_request
from evolver.trading.agents import execution_math
from evolver.trading.contracts import Intent, decimal as D, encode
from evolver.trading.feedback import attribution, compact, summarize
from evolver.trading.worker import Worker
from test_trading_runtime import NOW, policy


class Book:
    def __init__(self):
        self.policy = policy()
        self.db = sqlite3.connect(':memory:'); self.db.row_factory = sqlite3.Row
        self.db.executescript('CREATE TABLE events(timestamp REAL,event TEXT);'
            'CREATE TABLE alpaca_receipts(remote_id TEXT,client_id TEXT);'
            'CREATE TABLE broker_activities(kind TEXT,payload TEXT);')
        self.meta = {'activity_accounting': True, 'broker_projection_hash': 'first'}
        self.order_rows = []; self.trades = []
    def get(self, k, default=None): return self.meta.get(k, default)
    def orders(self): return copy.deepcopy(self.order_rows)
    def closed_trades(self): return copy.deepcopy(self.trades)
    def add(self, name='lot1', at=100, net='-.1', engineering=False, price='100.1'):
        for cid, side, qty, px, stamp in [(name,'buy','0.25','100',at), (name+'exit','sell','0.249375',price,at+10)]:
            self.order_rows.append({'client_id':cid,'quantity':qty,'value':str(D(qty)*D(px)),
                'intent':encode({'strategy':'ema','instrument':'BTC-USD','side':side,
                                 'reason':'broker_demo_validation' if engineering else 'signal' if side=='buy' else 'target_or_strategy_exit'})})
            self.db.execute('INSERT INTO alpaca_receipts VALUES (?,?)',(cid,cid))
            self.db.execute('INSERT INTO broker_activities VALUES (?,?)',('FILL',encode({
                'id':cid+'fill','order_id':cid,'transaction_time':dt.datetime.fromtimestamp(stamp,dt.timezone.utc).isoformat(),
                'symbol':'BTC/USD','qty':qty,'price':px,'side':side})))
        self.trades.append({'lot_id':name,'instrument':'BTC-USD','strategy':'ema','opened':at,'closed':at+10,
                            'realized_pnl':net,'fees_provisional':1})


class FeedbackTests(unittest.TestCase):
    def setUp(self): self.book = Book(); self.addCleanup(self.book.db.close)

    def test_costs_can_erase_a_gross_gain_and_are_not_counted_twice(self):
        self.book.add()
        r=summarize(self.book);t=r['trades'][0]
        self.assertEqual(D(t['gross_fill_price_pnl_usd']),D('.025'))
        self.assertEqual(D(t['fee_and_quantity_effect_usd']),D('.125'))
        self.assertEqual(r['summary']['gross_gains_erased_by_costs'],1)
        self.assertEqual(D(t['gross_fill_price_pnl_usd'])-D(t['fee_and_quantity_effect_usd']),D(t['net_pnl_usd']))

    def test_engineering_is_excluded_but_wins_and_losses_both_remain(self):
        self.book.add('drill',10,'-1',True)
        self.book.add('loss',100,'-.1')
        self.book.add('win',200,'.1',price='101')
        s=summarize(self.book)['summary']
        self.assertEqual((s['trades'],s['wins'],s['losses']),(2,1,1))
        self.assertEqual(D(s['net_pnl_usd']),0)

    def test_late_fee_posting_revises_evidence_and_provisional_flag(self):
        self.book.add();old=summarize(self.book)
        self.book.trades[0].update(realized_pnl='-.09',fees_provisional=0)
        new=summarize(self.book)
        self.assertNotEqual(old['evidence_hash'],new['evidence_hash'])
        self.assertEqual(new['summary']['provisional_trades'],0)
        self.assertEqual(D(new['summary']['fee_and_quantity_effect_usd']),D('.115'))

    def test_repeated_derivation_is_read_only_and_stable(self):
        self.book.add();changes=self.book.db.total_changes
        self.assertEqual(summarize(self.book),summarize(self.book))
        self.assertEqual(changes,self.book.db.total_changes)

    def test_overlapping_lots_never_receive_invented_attribution(self):
        self.book.add('first',100);self.book.add('second',105)
        report=summarize(self.book)
        self.assertEqual(report['summary']['attributed_trades'],0)
        self.assertEqual(D(report['summary']['net_pnl_usd']),D('-.2'))

    def test_missing_fill_keeps_net_outcome_but_no_gross_claim(self):
        self.book.add()
        self.book.db.execute("DELETE FROM broker_activities WHERE payload LIKE '%lot1exitfill%'")
        report=summarize(self.book)
        self.assertEqual(report['summary']['attributed_trades'],0)
        self.assertEqual(D(report['summary']['net_pnl_usd']),D('-.1'))

    def test_operational_halt_is_visible_without_discarding_the_loss(self):
        self.book.add();self.book.db.execute('INSERT INTO events VALUES (?,?)',(105,'risk.halted'))
        report=summarize(self.book)
        self.assertEqual(report['summary']['halts_during_trades'],1)
        self.assertEqual(report['summary']['losses'],1)

    def test_compact_memory_is_bounded_and_full_evidence_is_retained(self):
        for i in range(100):self.book.add('lot'+str(i),100+i*20)
        report=summarize(self.book);small=compact(report)
        self.assertEqual(len(report['trades']),100)
        self.assertEqual(len(small['recent']),3)
        self.assertLess(len(encode(small).encode()),4000)
        self.assertEqual(small['evidence_hash'],report['evidence_hash'])

    def test_split_fills_have_the_same_value_attribution(self):
        self.book.add();expected=summarize(self.book)['summary']
        r=json.loads(self.book.db.execute("SELECT payload FROM broker_activities WHERE payload LIKE '%lot1fill%'").fetchone()[0])
        self.book.db.execute("DELETE FROM broker_activities WHERE payload LIKE '%lot1fill%'")
        for n in range(2):
            self.book.db.execute('INSERT INTO broker_activities VALUES (?,?)',('FILL',encode({**r,'id':'split'+str(n),'qty':'0.125'})))
        actual=summarize(self.book)['summary']
        for key in expected:
            self.assertEqual(D(actual[key]),D(expected[key]),key)

    def test_worker_refreshes_memory_after_projection_changes_and_archives_old_evidence(self):
        self.book.add();worker=Worker.__new__(Worker)
        worker.book=self.book;worker._feedback_key=None;worker._feedback=None
        with tempfile.TemporaryDirectory() as temp:
            worker.outbox=Path(temp);first=worker._outcomes()
            self.book.trades[0].update(realized_pnl='-.09',fees_provisional=0)
            self.book.meta['broker_projection_hash']='settled'
            second=worker._outcomes()
            self.assertNotEqual(first['evidence_hash'],second['evidence_hash'])
            self.assertEqual(len(list((worker.outbox/'outcome-evidence').glob('*.json'))),2)
            self.assertEqual(second['summary']['provisional_trades'],0)

    def test_feedback_diagnostics_failure_does_not_throw_into_protection_loop(self):
        worker=Worker.__new__(Worker);worker.book=self.book;worker._feedback_key=None;worker._feedback=None
        with patch('evolver.trading.feedback.summarize',side_effect=sqlite3.OperationalError('fixture')):
            self.assertEqual(worker._outcomes()['status'],'unavailable')

    def test_both_reviewers_receive_current_feedback_independently(self):
        self.book.add();p=self.book.policy;seen=[]
        intent=Intent('bar','ema@v1','BTC-USD','buy','.24','97',NOW,'fixture','100.05')
        snapshot={'quotes':{'BTC-USD':{'bid':'100','ask':'100'}},'outcome_feedback':compact(summarize(self.book))}
        def model(system,prompt):
            facts=json.loads(prompt);seen.append(facts)
            self.assertIn('Risk-limit compliance alone',system)
            self.assertEqual(facts['snapshot']['outcome_feedback'],snapshot['outcome_feedback'])
            self.assertNotIn('analyst_decision',facts)
            return encode({**facts['required_output'],'verdict':'approve','reason':'bounded experiment fixture'})
        result=process_request({'kind':'trade','body':{'policy_hash':p.fingerprint,'intent':json.loads(encode(intent.__dict__)),'snapshot':snapshot}},p,model,model,NOW)
        self.assertEqual(len(seen),2);self.assertEqual(result['reviewer']['verdict'],'approve')

    def test_break_even_accounts_for_base_and_quote_fee_compounding(self):
        p=policy(fee_bps='25');intent=Intent('bar','ema@v1','BTC-USD','buy','.25','99',NOW,'fixture','100.05')
        math=execution_math(intent,p,{'quotes':{'BTC-USD':{'bid':'100','ask':'100'}}})
        price=D(math['rate_only_break_even_exit_usd'])
        self.assertLess(abs(price*(1-D('.0025'))**2-D('100.05')),D('1e-23'))
        self.assertGreater(D(math['rate_only_break_even_rise_bps']),50)
        self.assertIn('No return forecast',math['break_even_limitations'])


if __name__=='__main__':unittest.main()
