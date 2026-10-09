"""Offline regressions for stale protection claims after an uncertain demo exit."""
import json
import unittest

import test_trading_broker as fixtures
from evolver.trading.readiness import assess
from evolver.trading.contracts import decimal as D


class ProtectionFreshnessTests(unittest.TestCase):
    def setUp(self):
        fixtures.BrokerTests.setUp(self)
        self.engine.monotonic = lambda: 0

    tearDown = fixtures.BrokerTests.tearDown
    enter = fixtures.BrokerTests.enter

    def current(self, now=fixtures.NOW):
        return assess(self.book, now)["gates"]["broker_protection_current"]

    def test_fresh_broker_stop_is_current(self):
        self.enter()
        self.assertTrue(self.current())

    def test_old_complete_flag_is_not_current_after_reconciliation_outage(self):
        self.enter()
        self.assertFalse(self.current(fixtures.NOW + 12641))

    def test_old_reconciliation_is_not_rescued_by_a_fresh_protection_timestamp(self):
        self.enter()
        with self.book.db:
            self.book.set("broker_reconciled_at", fixtures.NOW - 31)
        self.assertFalse(self.current())

    def test_stop_cancelled_before_failed_exit_is_not_current(self):
        self.enter()
        self.broker.cancel(self.broker.active_protection()[0]["client_id"])
        self.broker.journal.rebuild(fixtures.NOW)
        # prepare() can cancel a stop while its previous successful coverage stays cached.
        self.assertTrue(self.book.get("protection")["complete"])
        self.assertFalse(self.current())

    def test_insufficient_remaining_stop_quantity_is_not_current(self):
        self.enter()
        order = self.broker.active_protection()[0]
        with self.book.db:
            self.book.db.execute("UPDATE orders SET quantity=? WHERE client_id=?",
                                 (str(D(json.loads(order["intent"])["quantity"])/2), order["client_id"]))
        self.assertFalse(self.current())

    def test_a_stop_for_another_position_does_not_prove_coverage(self):
        self.enter()
        value = self.book.get("protection")
        value["positions"]["BTC-USD"]["client_id"] = "another-client-id"
        with self.book.db:
            self.book.set("protection", value)
        self.assertFalse(self.current())

    def test_future_or_missing_timestamp_is_not_current(self):
        self.enter()
        for stamp in (fixtures.NOW + 1, None):
            with self.subTest(stamp=stamp):
                value = self.book.get("protection")
                value["checked_at"] = stamp
                with self.book.db:
                    self.book.set("protection", value)
                self.assertFalse(self.current())

    def test_fresh_flat_reconciliation_is_current(self):
        self.engine.tick({"BTC-USD": self.quote}, [], fixtures.NOW)
        self.assertTrue(self.current())

    def test_readiness_does_not_change_orders_or_send_broker_requests(self):
        self.enter()
        before = self.book.orders(include_protection=True)
        posts = self.http.posts
        self.assertFalse(self.current(fixtures.NOW + 31))
        self.assertEqual(self.book.orders(include_protection=True), before)
        self.assertEqual(self.http.posts, posts)


if __name__ == "__main__":
    unittest.main()
