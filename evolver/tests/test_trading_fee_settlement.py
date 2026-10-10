"""Synthetic split-fill fee and monetary-rounding incident regressions; no network."""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_trading_broker as fixtures
from test_trading_runtime import NOW
from evolver.trading.accounting import AccountingError
from evolver.trading.contracts import decimal as D
from evolver.trading.ledger import Ledger


class FeeSettlementTests(unittest.TestCase):
    setUp = fixtures.BrokerTests.setUp
    tearDown = fixtures.BrokerTests.tearDown
    enter = fixtures.BrokerTests.enter
    exit = fixtures.BrokerTests.exit

    def split_exit(self, fee='.06'):
        self.enter()
        original_fill = self.http.fill
        def split_fill(order, quantity, price):
            if order['side']=='sell' and order['type']=='market':
                original_fill(order,'.2',price)
                original_fill(order,'.0394',price)
            else:
                original_fill(order,quantity,price)
        with patch.object(self.http,'fill',side_effect=split_fill):
            self.exit()
        self.http.fees(sell_usd=fee)
        self.http.now = NOW+86400

    def test_split_fee_within_total_allowance_never_exceeds_an_allocated_fill_cap(self):
        self.split_exit()
        self.assertTrue(self.broker.reconcile(self.http.now, protect=False))
        allocations = dict(self.book.db.execute("SELECT fill_id,amount FROM broker_fee_allocations WHERE activity_id='usd-fee'"))
        self.assertEqual(sorted(map(D,allocations.values())), [D('.01'), D('.05')])
        self.assertEqual(sum(map(D,allocations.values())), D('.06'))
        self.assertEqual(D(self.book.get('cash')), D('499.88'))
        self.assertFalse(self.book.get('accounting')['fees_provisional'])
        self.assertEqual(self.book.get('accounting')['fee_allocation_version'], 2)

    def test_separate_fee_records_preserve_their_original_amounts(self):
        self.split_exit()
        fee = self.http.events[-1]
        fee['net_amount'] = '-.05'
        self.http.events.append({**fee,'id':'usd-fee-2','net_amount':'-.01'})
        self.assertTrue(self.broker.reconcile(self.http.now, protect=False))
        sums = {}
        for ident, amount in self.book.db.execute('SELECT activity_id,amount FROM broker_fee_allocations'):
            sums[ident] = sums.get(ident,D(0))+D(amount)
        self.assertEqual(sums['usd-fee'],D('.05'))
        self.assertEqual(sums['usd-fee-2'],D('.01'))
        self.assertEqual(D(self.book.get('cash')),D('499.88'))

    def test_real_fee_overrun_still_halts_without_publishing_a_projection(self):
        self.split_exit('.07')
        before = self.book.get('cash'), self.book.get('broker_projection_hash')
        result = self.engine.tick({'BTC-USD':replace(self.quote,timestamp=self.http.now)}, [], self.http.now)
        self.assertEqual(result['halt'],'broker_accounting_mismatch')
        self.assertEqual((self.book.get('cash'), self.book.get('broker_projection_hash')),before)
        failure = result['reconciliation_error']
        self.assertEqual(failure['code'],'fee_budget_exceeded')
        self.assertGreater(D(failure['details']['charged']),D(failure['details']['allowed']))

    def test_duplicate_reordered_fees_and_restart_preserve_cash_and_raw_facts(self):
        self.split_exit()
        self.assertTrue(self.broker.reconcile(self.http.now,protect=False))
        before = self.book.get('broker_projection_hash'),self.book.get('cash'),self.book.get('realized_pnl')
        raw = list(map(tuple,self.book.db.execute('SELECT id,payload FROM broker_activities ORDER BY id')))
        self.book.close();self.book=Ledger(self.path,self.p,self.broker.identity);self.broker.bind(self.book)
        self.http.events=list(reversed(self.http.events))+[self.http.events[-1]]
        self.assertTrue(self.broker.reconcile(self.http.now+1,protect=False))
        self.assertEqual((self.book.get('broker_projection_hash'),self.book.get('cash'),self.book.get('realized_pnl')),before)
        self.assertEqual(list(map(tuple,self.book.db.execute('SELECT id,payload FROM broker_activities ORDER BY id'))),raw)

    def cent_settlement(self):
        self.http.price=D('100.03');self.enter()
        self.http.price=D('99.93');self.exit()
        self.http.fees(sell_usd='.06')
        # Independent exchange cash: $500 - $24.01 + $23.92 - $0.06.
        # High-precision fill arithmetic gives $499.856042, which rounds to $499.86.
        self.http.cash=D('499.85');self.http.cents_cash=True;self.http.now=NOW+86400

    def test_posted_fees_and_per_fill_cents_match_without_faking_strategy_profit(self):
        self.cent_settlement()
        self.assertTrue(self.broker.reconcile(self.http.now,protect=False))
        accounting=self.book.get('accounting')
        self.assertEqual(accounting['balance_match'],'confirmed')
        self.assertEqual(accounting['cash_match_basis'],'per_fill_cent_settlement')
        self.assertEqual(accounting['broker_cash'],'499.85')
        self.assertEqual(D(accounting['cash_precision_difference']),D('-.006042'))
        self.assertEqual(D(self.book.get('cash')),D('499.856042'))
        self.assertEqual(D(self.book.get('realized_pnl')),D('-.143958'))
        self.assertEqual(self.book.get('cash_settlement_model'),'per_fill_cent')

    def test_established_cent_model_cannot_flip_to_mask_a_later_one_cent_gap(self):
        self.cent_settlement();self.assertTrue(self.broker.reconcile(self.http.now,protect=False))
        before=self.book.get('cash'),self.book.get('broker_projection_hash')
        for bad in ('499.84','499.86','498.85'):
            with self.subTest(broker_cash=bad):
                self.http.cash=D(bad)
                with self.assertRaises(AccountingError) as caught:
                    self.broker.reconcile(self.http.now+1,protect=False)
                self.assertEqual(caught.exception.code,'balance_mismatch')
                self.assertEqual((self.book.get('cash'),self.book.get('broker_projection_hash')),before)

    def test_cent_settlement_does_not_hide_extra_inventory(self):
        self.cent_settlement();self.http.qty += D('.001')
        with self.assertRaises(AccountingError) as caught:
            self.broker.reconcile(self.http.now,protect=False)
        self.assertFalse(caught.exception.details['quantity_matches'])
        self.assertIsNone(self.book.get('cash_settlement_model'))

    def test_diagnostics_count_repeat_failures_without_log_flood_and_never_clear_halt(self):
        self.split_exit('.07')
        for at in (self.http.now,self.http.now+1,self.http.now+2):
            self.engine.tick({'BTC-USD':replace(self.quote,timestamp=at)}, [], at)
        self.assertEqual(self.book.get('reconciliation_error')['occurrences'],3)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM events WHERE event='broker.reconciliation_failed'").fetchone()[0],1)
        with patch.object(self.broker,'reconcile',return_value=True):
            self.assertTrue(self.engine._reconcile(self.http.now+3))
        self.assertFalse(self.book.get('reconciliation_error')['active'])
        self.assertEqual(self.book.get('halt'),'broker_accounting_mismatch')

    def test_generic_transport_exception_never_exposes_credential_text(self):
        with patch.object(self.broker,'reconcile',side_effect=RuntimeError('SECRET_MUST_NOT_BE_RECORDED')):
            result=self.engine.tick({'BTC-USD':self.quote},[],NOW)
        self.assertEqual(result['reconciliation_error']['code'],'broker_unavailable')
        self.assertNotIn('SECRET_MUST_NOT_BE_RECORDED',json.dumps(result,default=str))
        self.assertFalse(result['halt'])


if __name__ == '__main__':
    unittest.main()
