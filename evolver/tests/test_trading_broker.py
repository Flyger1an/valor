"""Broker fault fixtures: an independent exchange balance, delayed activities, and stops."""
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_trading_runtime import NOW, approve, policy
from evolver.trading.accounting import AccountingError
from evolver.trading.alpaca import AlpacaBroker, BrokerError, recent_bars
from evolver.trading.contracts import Intent, Quote, decimal as D
from evolver.trading.costs import cost_summary, hosting_estimate
from evolver.trading.engine import Engine
from evolver.trading.ledger import Ledger
from evolver.trading.watchdog import health_reasons, ping


class Exchange:
    """No Ledger calls. Orders/activities/cash belong to this independently mutated fixture."""
    base = "https://paper-api.alpaca.markets"

    def __init__(self):
        self.now = NOW
        self.cash, self.qty = D(500), D(0)
        self.orders, self.events = {}, []
        self.posts = 0
        self.drop_post = False
        self.cancel_race = False
        self.defer_fills = False
        self.hidden = []
        self.partial_next = None
        self.price = D(100)
        self.usd_fee_at_fill = None  # Alpaca: exact-rate USD sell fee withheld at fill (e.g. D(".0025"))
        self.cents_cash = False      # Alpaca: /v2/account cash rounded to cents
        self.fill_late = False      # next market/limit order fills only after the client's lookup
        self.late_order = None

    def fill(self, order, qty, price):
        qty, price = D(qty), D(price)
        old_qty, old_value = D(order["filled_qty"]), D(order["filled_qty"])*D(order["filled_avg_price"] or 0)
        order["filled_qty"] = str(old_qty+qty)
        order["filled_avg_price"] = str((old_value+qty*price)/(old_qty+qty))
        order["status"] = "filled" if old_qty+qty == D(order["qty"]) else "partially_filled"
        sign = 1 if order["side"] == "buy" else -1
        self.cash -= sign*qty*price
        if order["side"] == "sell" and self.usd_fee_at_fill:
            self.cash -= qty*price*self.usd_fee_at_fill
        self.qty += sign*qty
        event = {"id": "fill-"+str(len(self.events)+len(self.hidden)), "activity_type": "FILL",
                 "order_id": order["id"], "symbol": "BTCUSD", "side": order["side"],
                 "qty": str(qty), "price": str(price),
                 "transaction_time": dt.datetime.fromtimestamp(self.now, dt.timezone.utc).isoformat()}
        (self.hidden if self.defer_fills else self.events).append(event)

    def fees(self, buy_qty="0.0006", sell_usd="0.05985", day=None):
        day = day or dt.datetime.fromtimestamp(NOW, dt.timezone.utc).date().isoformat()
        if D(buy_qty):
            self.qty -= D(buy_qty)
            self.events.append({"id": "base-fee", "activity_type": "CFEE", "date": day,
                                "symbol": "BTCUSD", "qty": str(-D(buy_qty)), "price": "100", "net_amount": "0"})
        if D(sell_usd):
            self.cash -= D(sell_usd)
            self.events.append({"id": "usd-fee", "activity_type": "FEE", "date": day,
                                "net_amount": str(-D(sell_usd)), "symbol": "BTCUSD"})

    def request(self, method, path, body=None, params=None, **_):
        if path == "/v2/account":
            return {"id": "fixture", "status": "ACTIVE", "crypto_status": "ACTIVE", "currency": "USD",
                    "cash": str(self.cash.quantize(D("0.01")) if self.cents_cash else self.cash)}
        if path == "/v2/positions":
            if self.late_order is not None:  # the exchange fills between lookup and account snapshot
                order, self.late_order = self.late_order, None
                self.fill(order, order["qty"], self.price)
            return [{"symbol": "BTCUSD", "qty": str(self.qty)}] if self.qty else []
        if path.startswith("/v2/assets/"):
            return {"tradable": True, "class": "crypto", "min_trade_increment": ".000000001",
                    "min_order_size": ".000000001", "price_increment": ".000000001"}
        if path == "/v2/account/activities":
            return list(self.events)
        if path == "/v2/orders:by_client_order_id":
            return self.orders.get(params["client_order_id"])
        if path == "/v2/orders" and method == "GET":
            return [o for o in self.orders.values() if o["status"] in {"new", "partially_filled"}]
        if path == "/v2/orders" and method == "POST":
            self.posts += 1
            cid = body["client_order_id"]
            if cid in self.orders:
                raise AssertionError("duplicate submission")
            order = {**body, "id": "remote-"+str(self.posts), "filled_qty": "0", "filled_avg_price": None, "status": "new"}
            self.orders[cid] = order
            if body["type"] != "stop_limit" and self.fill_late:
                self.fill_late, self.late_order = False, order
            elif body["type"] != "stop_limit":
                self.fill(order, self.partial_next or body["qty"], self.price)
                self.partial_next = None
            if self.drop_post:
                self.drop_post = False
                raise TimeoutError("response lost after exchange accepted order")
            return order
        if method == "DELETE":
            order = next(o for o in self.orders.values() if path.endswith(o["id"]))
            if self.cancel_race:
                self.cancel_race = False
                self.fill(order, D(order["qty"])-D(order["filled_qty"]), "97")
            else:
                order["status"] = "canceled"
            return None
        raise AssertionError((method, path))


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/"book.sqlite"
        self.p = policy(mode="demo", study_start_event="first_live_fill", study_end_utc=None)
        self.http = Exchange()
        self.broker = AlpacaBroker(self.p, self.http, "fixture", clock=lambda: self.http.now)
        self.book = Ledger(self.path, self.p, self.broker.identity)
        self.broker.bind(self.book)
        self.engine = Engine(self.book, self.p, self.broker, analyst=approve, reviewer=approve)
        self.engine.supervise(dict(action="resume_entries", risk_scale="1", policy_hash=self.p.fingerprint,
                                   issued_at=NOW, expires_at=NOW+600, reason="fixture"), NOW)
        self.quote = Quote("BTC-USD", "99.9", "100", NOW)
        self.buy = Intent("entry", "ema@v1", "BTC-USD", "buy", ".24", "97", NOW, "fixture", "100.05")

    def tearDown(self):
        self.book.close()
        self.tmp.cleanup()

    def enter(self):
        result = self.engine.tick({"BTC-USD": self.quote}, [self.buy], NOW)
        self.assertFalse(result["halt"])
        self.assertEqual(len(result["positions"]), 1)
        return result

    def exit(self):
        self.http.now = NOW+1
        intent = Intent("exit", "ema@v1", "BTC-USD", "sell", ".2394", "0", NOW+1, "fixture")
        return self.engine.tick({"BTC-USD": replace(self.quote, timestamp=NOW+1)}, [intent], NOW+1)

    def test_fee_accrual_native_stop_and_late_closed_trade_adjustment(self):
        first = self.enter()
        self.assertEqual(D(first["cash"]), D(476))
        self.assertEqual(D(first["positions"][0]["quantity"]), D(".2394"))
        self.assertTrue(first["protection"]["complete"])
        self.assertEqual(len(self.broker.active_protection()), 1)
        ended = self.exit()
        self.assertFalse(ended["positions"])
        self.assertEqual(D(ended["realized_pnl"]), D("-.12"))
        self.assertTrue(ended["accounting"]["fees_provisional"])
        self.http.fees()
        self.assertTrue(self.broker.reconcile(NOW+86400))
        self.assertFalse(self.book.get("accounting")["fees_provisional"])
        self.assertEqual(D(self.book.get("cash")), D("499.88015"))
        self.assertEqual(self.book.performance()["closed_trades"], 1)
        self.broker.reconcile(NOW+86401)
        self.assertEqual(D(self.book.get("cash")), D("499.88015"))
        self.assertEqual(self.book.performance()["closed_trades"], 1)
        self.assertIsNone(self.book.get("study_started_at"))

    def test_delayed_fee_does_not_reduce_a_new_position_twice(self):
        self.enter(); self.exit()
        self.http.now = NOW+2
        second = replace(self.buy, signal_id="next", timestamp=NOW+2)
        self.engine.tick({"BTC-USD": replace(self.quote, timestamp=NOW+2)}, [second], NOW+2)
        self.http.fees(buy_qty=".0012")  # Two same-day buys, explicit pro-rata allocation.
        self.broker.reconcile(NOW+86400)
        self.assertEqual(D(self.book.positions()["BTC-USD"]["quantity"]), D(".2394"))
        self.assertEqual(D(self.book.get("realized_pnl")), D("-.11985"))

    def test_duplicate_and_reordered_activities_survive_restart_and_restore(self):
        self.enter(); self.exit(); self.http.fees()
        self.http.events = list(reversed(self.http.events))
        self.broker.reconcile(NOW+86400)
        expected = self.book.get("cash")
        restore = Path(self.tmp.name)/"restored.sqlite"
        with sqlite3.connect(restore) as dest:
            self.book.db.backup(dest)
        self.book.close()
        self.book = Ledger(restore, self.p, self.broker.identity)
        self.broker.bind(self.book)
        self.broker.reconcile(NOW+86401)
        self.assertEqual(self.book.get("cash"), expected)
        self.assertEqual(self.book.performance()["closed_trades"], 1)

    def test_stop_fill_during_cancel_does_not_create_second_sell(self):
        self.enter()
        self.http.cancel_race = True
        self.exit()
        sells = [o for o in self.http.orders.values() if o["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertFalse(self.book.positions())
        self.assertGreater(D(self.book.get("cash")), D(499))

    def test_lost_post_response_is_recovered_without_duplicate(self):
        self.http.drop_post = True
        self.engine.tick({"BTC-USD": self.quote}, [self.buy], NOW)
        self.broker.reconcile(NOW+1)
        buys = [o for o in self.http.orders.values() if o["side"] == "buy"]
        self.assertEqual(len(buys), 1)
        self.assertTrue(self.book.get("protection")["complete"])

    def test_activity_lag_blocks_entries_until_fill_receipt_arrives(self):
        self.http.defer_fills = True
        result = self.engine.tick({"BTC-USD": self.quote}, [self.buy], NOW)
        self.assertEqual(result["entry_pause"], "activity_lag")
        self.assertFalse(self.book.positions())
        self.http.events.extend(self.http.hidden); self.http.hidden = []
        self.assertTrue(self.broker.reconcile(NOW+1))
        self.assertEqual(len(self.book.positions()), 1)
        self.assertEqual(len(self.broker.active_protection()), 1)

    def test_unknown_external_activity_halts_and_keeps_existing_stop(self):
        self.enter()
        self.http.events.append({"id": "foreign", "activity_type": "FILL", "order_id": "not-ours"})
        result = self.engine.tick({"BTC-USD": self.quote}, [], NOW+1)
        self.assertEqual(result["halt"], "broker_accounting_mismatch")
        self.assertEqual(len(self.broker.active_protection()), 1)

    def test_changed_activity_id_is_not_silently_overwritten(self):
        self.enter()
        self.http.events[0]["price"] = "99"
        with self.assertRaises(AccountingError):
            self.broker.reconcile(NOW+1)

    def test_stale_quote_and_model_failure_do_not_cancel_broker_stop(self):
        self.enter()
        self.engine.analyst = self.engine.reviewer = None
        result = self.engine.tick({}, [], NOW+1000)
        self.assertEqual(len(self.broker.active_protection()), 1)
        self.assertEqual(len([o for o in self.http.orders.values() if o["side"] == "buy"]), 1)

    def test_disk_failure_prevents_post(self):
        self.broker.reconcile(NOW)
        with patch.object(self.book, "reserve", side_effect=sqlite3.OperationalError("database or disk is full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.engine._submit(self.buy, self.quote, NOW)
        self.assertEqual(self.http.posts, 0)

    def test_unknown_cash_movement_cannot_hide_inside_fee_reserve(self):
        self.enter()
        self.http.cash -= D(".001")
        with self.assertRaises(AccountingError):
            self.broker.reconcile(NOW+1)

    def test_partial_buy_restores_and_broker_stop_fills_while_worker_is_offline(self):
        self.http.partial_next = D(".1")
        self.enter()
        self.assertEqual(D(self.book.positions()["BTC-USD"]["quantity"]), D(".09975"))
        self.http.now = NOW+31
        self.engine.tick({"BTC-USD": replace(self.quote, timestamp=NOW+31)}, [], NOW+31)
        self.assertEqual(self.book.order(self.buy.client_id)["status"], "cancelled")
        stop_id = self.broker.active_protection()[0]["client_id"]
        restored = Path(self.tmp.name)/"partial-restore.sqlite"
        with sqlite3.connect(restored) as target:
            self.book.db.backup(target)
        self.book.close()
        self.http.now = NOW+40
        self.http.fill(self.http.orders[stop_id], ".09975", "97")
        self.book = Ledger(restored, self.p, self.broker.identity)
        self.broker.bind(self.book)
        self.assertTrue(self.broker.reconcile(NOW+41))
        self.assertFalse(self.book.positions())
        self.assertFalse(self.broker.active_protection())
        self.assertEqual(self.book.performance()["closed_trades"], 1)
        self.assertLess(D(self.book.get("realized_pnl")), 0)

    def test_actual_broker_cent_precision_and_base_fee_rounding(self):
        self.http.price = D("85396.87")
        original = self.http.fill
        def withhold(order, qty, price):
            original(order, qty, price)
            self.http.qty -= D(".000000352")
            self.http.cash = self.http.cash.quantize(D(".01"))
        self.http.fill = withhold
        self.buy = replace(self.buy, quantity=D(".000140427"), limit_price=D("85400"), stop_price=D("84531"))
        self.quote = Quote("BTC-USD", "85395", "85400", NOW)
        self.enter()
        self.assertEqual(D(self.book.positions()["BTC-USD"]["quantity"]), D(".000140075"))
        self.assertEqual(D(self.book.get("cash")), D("488.00797373651"))
        self.assertEqual(self.book.get("accounting")["balance_match"], "fee_accrual_bridge")

    def test_api_outage_keeps_stop_and_recovers_without_new_order(self):
        self.enter()
        posts = self.http.posts
        original = self.http.request
        def unavailable(*_, **__):
            raise TimeoutError("broker unavailable")
        self.http.request = unavailable
        result = self.engine.tick({}, [], NOW+10)
        self.assertEqual(result["entry_pause"], "broker_reconciliation_unavailable")
        self.assertEqual(len(self.broker.active_protection()), 1)
        self.http.request = original
        self.assertTrue(self.broker.reconcile(NOW+11))
        self.assertEqual(self.http.posts, posts)

    def test_unmapped_fee_halts_before_touching_an_unrelated_position(self):
        self.enter()
        before = self.book.positions()
        self.http.events.append({"id": "foreign-fee", "activity_type": "CFEE", "date": "2026-09-01",
                                 "symbol": "BTCUSD", "qty": "-.0001", "net_amount": "0"})
        with self.assertRaises(AccountingError):
            self.broker.reconcile(NOW+1)
        self.assertEqual(before, self.book.positions())


class MonitorAndCostTests(unittest.TestCase):
    def test_paginated_history_keeps_both_symbols_and_rejects_repeated_cursor(self):
        stamp = dt.datetime.fromtimestamp(NOW-600, dt.timezone.utc).isoformat()
        bar = {"t": stamp, "o": 100, "h": 101, "l": 99, "c": 100, "v": 1}
        class Pages:
            def __init__(self, repeat=False): self.calls, self.repeat = [], repeat
            def request(self, *_, **kwargs):
                self.calls.append(dict(kwargs["params"]))
                return {"bars": {"BTC/USD" if len(self.calls) == 1 else "ETH/USD": [bar]},
                        "next_page_token": "next" if len(self.calls) == 1 or self.repeat else None}
        http = Pages()
        result = recent_bars(http, ("BTC-USD", "ETH-USD"), NOW)
        self.assertEqual(set(result), {"BTC-USD", "ETH-USD"})
        self.assertEqual(http.calls[1]["page_token"], "next")
        self.assertEqual(http.calls[0]["end"], http.calls[1]["end"])
        with self.assertRaises(BrokerError):
            recent_bars(Pages(True), ("BTC-USD", "ETH-USD"), NOW)

    def test_quiet_market_is_healthy_but_a_frozen_feed_or_unpriced_holding_is_not(self):
        from evolver.trading.runtime import status
        snap = {"timestamp": NOW, "broker_reconciled_at": NOW, "market_feed_received_at": NOW,
                "entry_session_open": False, "positions": [],
                "quotes": {"BTC-USD": {"instrument": "BTC-USD", "bid": "100", "ask": "100.01", "timestamp": NOW-120}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"snapshot.json"
            def check(**updates):
                path.write_text(json.dumps({**snap, **updates}))
                return status(directory, NOW)["healthy"]
            quote = lambda age: {"BTC-USD": {"instrument": "BTC-USD", "bid": "100", "ask": "100.01", "timestamp": NOW-age}}
            self.assertTrue(check())
            # 24/7 session, quiet weekend: quotes only advance on price changes (incident 2026-10-10).
            self.assertTrue(check(entry_session_open=True))
            self.assertTrue(check(entry_session_open=True, quotes=quote(899)))
            self.assertFalse(check(entry_session_open=True, quotes=quote(901)))  # provider frozen
            self.assertTrue(check(positions=[{"instrument": "BTC-USD"}]))
            self.assertFalse(check(positions=[{"instrument": "BTC-USD"}], quotes=quote(301)))  # holding unpriced
            self.assertFalse(check(market_feed_received_at=NOW-31))  # our feed is not running
            self.assertFalse(check(broker_reconciled_at=NOW-31))     # broker not reconciled

    def test_costs_exclude_deposits_and_do_not_double_count_spread(self):
        snap = {"equity": "602", "starting_cash": "500", "mode": "demo", "runtime_started_at": NOW,
                "accounting": {"net_cash_flows": "100", "fees_provisional": False}}
        costs = cost_summary(snap, {"estimated_cost_usd": ".02"}, NOW+3600)
        self.assertEqual(D(costs["net_trading_pnl_usd"]), D(2))
        self.assertEqual(D(costs["experiment_pnl_estimated_usd"]), D("1.962"))
        self.assertEqual(hosting_estimate(NOW, NOW+20*86400), D("8.64"))

    def test_external_watchdog_reports_stale_or_unprotected_runtime(self):
        healthy = {"healthy": True, "mode": "demo", "protection": {"complete": True}}
        self.assertEqual(health_reasons(healthy, NOW), [])
        self.assertTrue(health_reasons({**healthy, "healthy": False}, NOW))
        self.assertTrue(health_reasons({**healthy, "protection": {"complete": False}}, NOW))
        requests = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): pass
        def send(req, **_):
            requests.append(req)
            return Response()
        suppressed = ping("https://hc-ping.com/00000000-0000-0000-0000-000000000000", {**healthy, "healthy": False}, NOW, send)
        self.assertFalse(suppressed["sent"])
        self.assertFalse(requests)
        ping("https://hc-ping.com/00000000-0000-0000-0000-000000000000", {**healthy, "halt": "risk_incident"}, NOW, send)
        self.assertTrue(requests[0].full_url.endswith("/fail"))
        self.assertEqual(requests[0].data, b"unhealthy")
        with self.assertRaises(ValueError):
            ping("https://untrusted.example/secret", healthy, NOW, send)


if __name__ == "__main__":
    unittest.main()
