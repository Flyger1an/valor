"""Production incident 2026-10-08 10:31 UTC: a market exit filled between the order lookup and the
account snapshot; reconciliation compared a stale 'open' receipt with the live open-order list and
halted with broker_accounting_mismatch although the broker was consistent."""
import unittest
from dataclasses import replace

import test_trading_broker as broker_fixture
from test_trading_runtime import NOW
from evolver.trading.contracts import Intent, decimal as D


class OrderRaceTests(unittest.TestCase):
    setUp = broker_fixture.BrokerTests.setUp
    tearDown = broker_fixture.BrokerTests.tearDown
    enter = broker_fixture.BrokerTests.enter

    def test_exit_filling_between_lookup_and_account_snapshot_does_not_halt(self):
        self.enter()
        self.http.fill_late = True
        self.http.now = NOW+1
        intent = Intent("exit", "ema@v1", "BTC-USD", "sell", ".2394", "0", NOW+1, "fixture")
        first = self.engine.tick({"BTC-USD": replace(self.quote, timestamp=NOW+1)}, [intent], NOW+1)
        self.assertEqual(first["halt"], "")
        self.http.now = NOW+2
        settled = self.engine.tick({"BTC-USD": replace(self.quote, timestamp=NOW+2)}, [], NOW+2)
        self.assertEqual(settled["halt"], "")
        self.assertFalse(settled["positions"])
        self.assertFalse(settled["pending_orders"])
        self.assertEqual(self.book.get("accounting")["fills"], 2)
        self.assertEqual(self.http.posts, 3)  # entry, protective stop, exit: never resubmitted

    def _exit(self, at):
        self.http.now = at
        intent = Intent("exit", "ema@v1", "BTC-USD", "sell", ".2394", "0", at, "fixture")
        return self.engine.tick({"BTC-USD": replace(self.quote, timestamp=at)}, [intent], at)

    def test_unposted_exact_rate_sell_fee_with_cent_rounded_cash_reconciles(self):
        # Production 2026-10-08: broker $499.76 vs reserve-rounded estimate $499.7547 -> halted.
        self.enter()
        self.http.usd_fee_at_fill = self.p.fee_bps / 10000
        self.http.cents_cash = True
        self.http.price = D("90.07")  # exact fee and rounded-up reserve straddle a cent (old rule halts)
        result = self._exit(NOW+1)
        self.assertEqual(result["halt"], "")
        self.assertFalse(result["positions"])
        self.assertEqual(self.book.get("accounting")["balance_match"], "fee_accrual_bridge")
        self.assertTrue(self.book.get("accounting")["fees_provisional"])

    def test_cash_gap_beyond_declared_fee_reserves_still_halts(self):
        self.enter()
        self.http.usd_fee_at_fill = self.p.fee_bps / 10000
        self.http.cents_cash = True
        self._exit(NOW+1)
        self.http.cash -= D("1")  # an unexplained dollar is never a fee accrual
        self.http.now = NOW+2
        result = self.engine.tick({"BTC-USD": replace(self.quote, timestamp=NOW+2)}, [], NOW+2)
        self.assertEqual(result["halt"], "broker_accounting_mismatch")

    def test_genuinely_unknown_open_order_still_halts(self):
        self.enter()
        stray = {"client_order_id": "not-ours", "symbol": "BTC/USD", "side": "sell", "qty": "1", "type": "limit",
                 "id": "remote-stray", "filled_qty": "0", "filled_avg_price": None, "status": "new"}
        self.http.orders["not-ours"] = stray
        self.http.now = NOW+1
        result = self.engine.tick({"BTC-USD": replace(self.quote, timestamp=NOW+1)}, [], NOW+1)
        self.assertEqual(result["halt"], "broker_accounting_mismatch")


if __name__ == "__main__":
    unittest.main()
