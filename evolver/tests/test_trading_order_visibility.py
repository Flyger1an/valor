"""Offline broker visibility faults: cancelled-but-listed and pending-cancel-but-absent.

The October 10 archive records one unknown_open_orders error during a stop
cancellation, resolved seven seconds later. Its original endpoint bodies were
not retained, so both directions are synthetic regressions, not exact replay.
"""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_trading_broker as fixtures
from test_trading_runtime import NOW
from evolver.trading.alpaca import AlpacaBroker
from evolver.trading.contracts import Intent
from evolver.trading.engine import Engine
from evolver.trading.ledger import Ledger


class OrderVisibilityTests(unittest.TestCase):
    setUp = fixtures.BrokerTests.setUp
    tearDown = fixtures.BrokerTests.tearDown
    enter = fixtures.BrokerTests.enter

    def tick(self, seconds, intents=()):
        self.http.now = NOW+seconds
        return self.engine.tick({"BTC-USD": replace(self.quote, timestamp=self.http.now)},
                                list(intents), self.http.now)

    def exit_intent(self, seconds):
        return Intent("exit", "ema@v1", "BTC-USD", "sell", ".2394", "0", NOW+seconds, "fixture")

    def stale_cancelled_list(self):
        self.enter()
        cid = self.broker.active_protection()[0]["client_id"]
        self.broker.cancel(cid)
        original = self.http.request
        listed = {cid}

        def request(method, path, body=None, params=None, **kwargs):
            result = original(method, path, body, params, **kwargs)
            if method == "GET" and path == "/v2/orders":
                present = {o["client_order_id"] for o in result}
                result += [dict(self.http.orders[c]) for c in listed-present]
            return result
        return cid, listed, request

    def test_cancelled_stop_still_listed_defers_exit_and_then_recovers(self):
        cid, listed, request = self.stale_cancelled_list()
        before = self.book.get("cash"), self.book.get("broker_projection_hash")
        with patch.object(self.http, "request", side_effect=request):
            waiting = self.tick(1, [self.exit_intent(1)])
            self.assertEqual(waiting["halt"], "")
            self.assertEqual(waiting["entry_pause"], "order_state_refresh")
            self.assertTrue(waiting["order_visibility"]["active"])
            self.assertEqual(self.http.posts, 2)  # no exit or replacement while observations disagree
            self.assertEqual((self.book.get("cash"), self.book.get("broker_projection_hash")), before)
            listed.clear()
            settled = self.tick(8, [self.exit_intent(8)])
        self.assertEqual(settled["halt"], "")
        self.assertFalse(settled["positions"])
        self.assertFalse(settled["pending_orders"])
        self.assertFalse(settled["order_visibility"]["active"])
        self.assertEqual(self.book.get("accounting")["fills"], 2)
        self.assertEqual(self.http.posts, 4)  # entry, old stop, replacement stop, one exit

    def test_pending_cancel_missing_from_list_does_not_latch_halt(self):
        self.enter()
        cid = self.broker.active_protection()[0]["client_id"]
        original = self.http.request
        delaying = [True]

        def request(method, path, body=None, params=None, **kwargs):
            if method == "DELETE" and delaying[0]:
                self.http.orders[cid]["status"] = "pending_cancel"
                return None
            return original(method, path, body, params, **kwargs)

        with patch.object(self.http, "request", side_effect=request):
            waiting = self.tick(1, [self.exit_intent(1)])
            self.assertEqual(waiting["halt"], "")
            self.assertEqual(waiting["entry_pause"], "order_state_refresh")
            self.assertEqual(self.http.posts, 2)
            delaying[0] = False
            self.http.orders[cid]["status"] = "canceled"
            settled = self.tick(8, [self.exit_intent(8)])
        self.assertEqual(settled["halt"], "")
        self.assertFalse(settled["positions"])
        self.assertEqual(self.http.posts, 4)

    def test_persistent_disagreement_halts_at_thirty_seconds(self):
        cid, listed, request = self.stale_cancelled_list()
        with patch.object(self.http, "request", side_effect=request):
            self.assertFalse(self.tick(1)["halt"])
            self.assertFalse(self.tick(30.9)["halt"])
            failed = self.tick(31)
        self.assertEqual(failed["halt"], "broker_accounting_mismatch")
        self.assertEqual(failed["reconciliation_error"]["code"], "order_visibility_timeout")
        self.assertEqual(failed["reconciliation_error"]["details"]["known_client_ids"], [cid])
        self.assertEqual(self.http.posts, 2)

    def test_restarts_and_changing_order_ids_cannot_reset_wait_budget(self):
        cid, listed, request = self.stale_cancelled_list()
        with patch.object(self.http, "request", side_effect=request):
            self.tick(1)
            self.book.close()
            self.broker = AlpacaBroker(self.p, self.http, "fixture", clock=lambda: self.http.now)
            self.book = Ledger(self.path, self.p, self.broker.identity)
            self.broker.bind(self.book)
            self.engine = Engine(self.book, self.p, self.broker)
            listed.clear()
            listed.add(self.buy.client_id)  # a different known terminal order is now listed
            self.assertFalse(self.tick(10)["halt"])
            failed = self.tick(31)
        self.assertEqual(failed["reconciliation_error"]["code"], "order_visibility_timeout")
        self.assertEqual(self.book.get("broker_order_visibility")["first_seen_at"], NOW+1)

    def test_genuinely_unknown_order_still_halts_immediately_without_logging_id(self):
        self.enter()
        original = self.http.request

        def request(method, path, body=None, params=None, **kwargs):
            result = original(method, path, body, params, **kwargs)
            if method == "GET" and path == "/v2/orders":
                result.append({"client_order_id": "UNTRUSTED_EXTERNAL_TEXT"})
            return result

        with patch.object(self.http, "request", side_effect=request):
            failed = self.tick(1)
        self.assertEqual(failed["halt"], "broker_accounting_mismatch")
        self.assertEqual(failed["reconciliation_error"]["code"], "unknown_open_orders")
        self.assertEqual(failed["reconciliation_error"]["details"]["unknown_order_count"], 1)
        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", json.dumps(failed, default=str))

    def test_changed_identity_never_qualifies_as_visibility_lag(self):
        cid, listed, request = self.stale_cancelled_list()
        self.http.orders[cid]["id"] = "different-remote-order"
        # The next lookup normally happens before the account list. Keep this
        # test focused on the extra-listed terminal order by publishing its cancellation first.
        self.broker.journal.rebuild(NOW+1)
        with patch.object(self.http, "request", side_effect=request):
            failed = self.tick(1)
        self.assertEqual(failed["halt"], "broker_accounting_mismatch")
        self.assertEqual(failed["reconciliation_error"]["code"], "order_identity_mismatch")

    def test_terminal_order_cannot_be_resurrected(self):
        cid, listed, request = self.stale_cancelled_list()
        self.broker.journal.rebuild(NOW+1)
        self.http.orders[cid]["status"] = "new"
        with patch.object(self.http, "request", side_effect=request):
            failed = self.tick(1)
        self.assertEqual(failed["halt"], "broker_accounting_mismatch")
        self.assertEqual(failed["reconciliation_error"]["code"], "order_state_regressed")
        report = json.loads(self.book.db.execute("SELECT report FROM alpaca_receipts WHERE client_id=?", (cid,)).fetchone()[0])
        self.assertEqual(report["status"], "cancelled")

    def test_disappeared_known_order_is_not_silently_accepted(self):
        cid, listed, request = self.stale_cancelled_list()
        self.broker.journal.rebuild(NOW+1)
        original_lookup = self.broker.lookup
        with patch.object(self.http, "request", side_effect=request), patch.object(
                self.broker, "lookup", side_effect=lambda c: None if c == cid else original_lookup(c)):
            failed = self.tick(1)
        self.assertEqual(failed["halt"], "broker_accounting_mismatch")
        self.assertEqual(failed["reconciliation_error"]["code"], "unknown_open_orders")

    def test_terminal_receipt_is_preserved_before_ledger_projection_catches_up(self):
        cid, listed, request = self.stale_cancelled_list()
        self.assertEqual(self.book.order(cid)["status"], "open")
        self.http.orders[cid]["status"] = "new"
        with patch.object(self.http, "request", side_effect=request):
            failed = self.tick(1)
        self.assertEqual(failed["halt"], "broker_accounting_mismatch")
        self.assertEqual(failed["reconciliation_error"]["code"], "order_state_regressed")
        report = json.loads(self.book.db.execute("SELECT report FROM alpaca_receipts WHERE client_id=?", (cid,)).fetchone()[0])
        self.assertEqual(report["status"], "cancelled")

    def test_one_visibility_event_per_incident_and_no_existing_halt_cleared(self):
        cid, listed, request = self.stale_cancelled_list()
        self.book.halt("operator_kill_switch", NOW)
        with patch.object(self.http, "request", side_effect=request):
            for second in (1, 2, 3):
                self.tick(second)
            self.assertEqual(self.book.db.execute("SELECT count(*) FROM events WHERE event='broker.order_visibility_wait'").fetchone()[0], 1)
            listed.clear()
            self.http.now = NOW+8
            self.assertTrue(self.broker.reconcile(self.http.now))
        self.assertEqual(self.book.get("halt"), "operator_kill_switch")

    def test_fill_during_cancellation_is_replayed_before_any_exit_submission(self):
        self.enter()
        cid = self.broker.active_protection()[0]["client_id"]
        self.http.cancel_race = True
        self.broker.cancel(cid)  # independent exchange fills the entire stop during cancellation
        original = self.http.request
        lagging = [True]

        def request(method, path, body=None, params=None, **kwargs):
            result = original(method, path, body, params, **kwargs)
            if method == "GET" and path == "/v2/orders" and lagging[0]:
                result.append(dict(self.http.orders[cid]))
            return result

        with patch.object(self.http, "request", side_effect=request):
            waiting = self.tick(1, [self.exit_intent(1)])
            self.assertFalse(waiting["halt"])
            self.assertEqual(self.http.posts, 2)
            lagging[0] = False
            settled = self.tick(8, [self.exit_intent(8)])
        self.assertFalse(settled["halt"])
        self.assertFalse(settled["positions"])
        self.assertEqual(self.http.posts, 2)  # the stop fill already sold it; never double-sell
        self.assertEqual(self.book.get("accounting")["fills"], 2)


if __name__ == "__main__":
    unittest.main()
