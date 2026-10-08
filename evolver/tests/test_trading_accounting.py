"""Synthetic Trading API schema fixtures; not a replay of any actual broker account.

No credentials, network, orders or production database access are used here.
"""
import datetime as dt
import json
from dataclasses import replace

import test_trading_broker as broker_fixture
from test_trading_runtime import NOW
from evolver.trading.accounting import AccountingError, fee_components
from evolver.trading.contracts import decimal as D


# Reuse fixture setup/helpers without inheriting the existing test methods.
import unittest


class AccountingParserTests(unittest.TestCase):
    setUp = broker_fixture.BrokerTests.setUp
    tearDown = broker_fixture.BrokerTests.tearDown
    enter = broker_fixture.BrokerTests.enter
    exit = broker_fixture.BrokerTests.exit

    def journal(self, amount="500", **changes):
        return {"id": "synthetic-journal", "activity_type": "JNLC", "net_amount": amount,
                "date": "2026-10-04", "status": "executed", **changes}

    def review(self, item, classification="opening_capital", **changes):
        self.broker.journal.ingest([item], NOW+86400)
        args = dict(activity_id=item["id"], classification=classification,
                    evidence_sha256="a"*64, reviewed_by="synthetic-test-reviewer", now=NOW+86400)
        args.update(changes)
        self.broker.journal.review_cash_journal(**args)

    def test_cash_cfee_without_quantity_replays_exactly_once_after_close(self):
        self.enter(); self.exit(); self.http.fees()
        usd = self.http.events[-1]
        usd.update(activity_type="CFEE", symbol="USD", currency="USD")
        self.assertNotIn("qty", usd)
        self.assertTrue(self.broker.reconcile(NOW+86400))
        self.assertEqual(D(self.book.get("cash")), D("499.88015"))
        self.assertEqual(D(self.book.get("realized_pnl")), D("-.11985"))
        before = self.book.get("broker_projection_hash")
        self.http.events = list(reversed(self.http.events)) + [usd]
        self.assertTrue(self.broker.reconcile(NOW+86401))
        self.assertEqual(self.book.get("broker_projection_hash"), before)
        row = self.book.db.execute("SELECT currency,method,amount FROM broker_fee_allocations WHERE activity_id='usd-fee'").fetchone()
        self.assertEqual(tuple(row), ("USD", "daily_pro_rata", "0.05985"))
        self.assertEqual(self.book.performance()["closed_trades"], 1)

    def test_cash_cfee_does_not_charge_a_later_buy(self):
        self.enter(); self.exit()
        self.http.now = NOW+2
        self.engine.tick({"BTC-USD": replace(self.quote, timestamp=NOW+2)},
                         [replace(self.buy, signal_id="later", timestamp=NOW+2)], NOW+2)
        self.http.fees(buy_qty=".0012")
        self.http.events[-1].update(activity_type="CFEE", symbol="USD")
        self.assertTrue(self.broker.reconcile(NOW+86400))
        self.assertEqual(D(self.book.positions()["BTC-USD"]["quantity"]), D(".2394"))
        self.assertEqual(D(self.book.get("realized_pnl")), D("-.11985"))

    def test_cash_cfee_stays_provisional_same_day(self):
        self.enter(); self.exit(); self.http.fees()
        self.http.events[-1].update(activity_type="CFEE", symbol="USD")
        self.assertTrue(self.broker.reconcile(NOW+2))
        self.assertTrue(self.book.get("accounting")["fees_provisional"])
        self.assertEqual(D(self.book.get("cash")), D("499.88"))

    def test_fee_components_reject_ambiguous_shapes(self):
        fee = {"activity_type": "CFEE", "net_amount": "-.01", "symbol": "USD"}
        for change in [{"qty": "-.001"}, {"qty": ".001"}, {"net_amount": "0"},
                       {"net_amount": ".01"}, {"currency": "EUR"}, {"symbol": "DOGEUSD"},
                       {"net_amount": "NaN"}, {"qty": None}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                fee_components({**fee, **change}, self.p)
        for change in [{"net_amount": "0", "qty": "-.001", "symbol": "USD"},
                       {"net_amount": "0", "qty": "-.001", "symbol": "BTCUSD", "currency": "EUR"},
                       {"activity_type": "FEE", "net_amount": "0", "qty": "-.001", "symbol": "BTCUSD"}]:
            with self.subTest(change=change), self.assertRaises(AccountingError):
                fee_components({**fee, **change}, self.p)

    def late_fees(self):
        day = dt.datetime.fromtimestamp(NOW+86400, dt.timezone.utc).date().isoformat()
        self.http.fees(day=day)
        for fee in self.http.events[-2:]:
            fee.update(activity_type="CFEE", currency="USD",
                       created_at=dt.datetime.fromtimestamp(NOW+10, dt.timezone.utc).isoformat())
        self.http.events[-1].pop("symbol")
        self.broker.journal.ingest(self.http.events, NOW+86400)

    def review_fee(self, activity_id, fill_ids=None, **changes):
        side = "buy" if activity_id == "base-fee" else "sell"
        ids = fill_ids if fill_ids is not None else [f["id"] for f in self.http.events
                                                    if f["activity_type"] == "FILL" and f["side"] == side]
        args = dict(activity_id=activity_id, fill_ids=ids, evidence_sha256="b"*64,
                    reviewed_by="synthetic-test-reviewer", now=NOW+86400)
        args.update(changes)
        self.broker.journal.review_fee_allocation(**args)

    def test_actual_base_shape_uses_quantity_despite_usd_account_currency(self):
        self.assertEqual(fee_components({"activity_type": "CFEE", "net_amount": "0",
                          "qty": "-.000000352", "symbol": "BTCUSD", "currency": "USD"}, self.p),
                         (True, D(".000000352"), "BTC-USD"))

    def test_next_day_fee_is_not_automatically_assigned_from_amount_or_creation_date(self):
        self.enter(); self.exit(); self.late_fees()
        before = self.book.get("broker_projection_hash"), self.book.get("cash")
        with self.assertRaisesRegex(AccountingError, "attributed"):
            self.broker.reconcile(NOW+86400)
        self.assertEqual((self.book.get("broker_projection_hash"), self.book.get("cash")), before)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM broker_fee_reviews").fetchone()[0], 0)

    def test_reviewed_settlement_fees_survive_duplicate_restart_and_replay(self):
        from evolver.trading.ledger import Ledger
        self.enter(); self.exit(); self.late_fees()
        for fid in ["base-fee", "usd-fee"]:
            self.review_fee(fid); self.review_fee(fid)
        self.assertTrue(self.broker.reconcile(NOW+86400, protect=False))
        self.assertEqual(self.book.get("accounting")["balance_match"], "confirmed")
        self.assertFalse(self.book.get("accounting")["fees_provisional"])
        self.assertEqual(D(self.book.get("cash")), D("499.88015"))
        before = self.book.get("broker_projection_hash"), self.book.get("realized_pnl")
        raw = list(self.book.db.execute("SELECT payload FROM broker_activities ORDER BY id"))
        self.book.close(); self.book = Ledger(self.path, self.p, self.broker.identity); self.broker.bind(self.book)
        self.http.events = list(reversed(self.http.events)) + [self.http.events[-1]]
        self.assertTrue(self.broker.reconcile(NOW+86401, protect=False))
        self.assertEqual((self.book.get("broker_projection_hash"), self.book.get("realized_pnl")), before)
        self.assertEqual([tuple(r) for r in self.book.db.execute("SELECT payload FROM broker_activities ORDER BY id")],
                         [tuple(r) for r in raw])
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM broker_fee_reviews").fetchone()[0], 2)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM events WHERE event='accounting.fee_allocation_reviewed'").fetchone()[0], 2)
        self.assertEqual({r[0] for r in self.book.db.execute("SELECT method FROM broker_fee_allocations")},
                         {"reviewed_fill_set_pro_rata"})

    def test_fee_review_rejects_wrong_fill_or_changed_evidence(self):
        self.enter(); self.exit(); self.late_fees()
        buy = next(f["id"] for f in self.http.events if f.get("side") == "buy")
        for ids in [[buy], ["missing"], [], [buy, buy]]:
            with self.subTest(ids=ids), self.assertRaises(AccountingError):
                self.review_fee("usd-fee", ids)
        self.review_fee("usd-fee")
        with self.assertRaisesRegex(AccountingError, "immutable"):
            self.review_fee("usd-fee", evidence_sha256="c"*64)
        self.review_fee("base-fee")
        with self.book.db:
            self.book.db.execute("DELETE FROM events WHERE event='accounting.fee_allocation_reviewed'")
        with self.assertRaisesRegex(AccountingError, "audit decision"):
            self.broker.reconcile(NOW+86400)

    def test_reviewed_old_fee_does_not_settle_later_fill(self):
        self.enter(); self.exit(); self.late_fees()
        for fid in ["base-fee", "usd-fee"]:
            self.review_fee(fid)
        self.assertTrue(self.broker.reconcile(NOW+86400))
        self.http.now = NOW+86400
        self.engine.supervise(dict(action="resume_entries", risk_scale="1", policy_hash=self.p.fingerprint,
                                   issued_at=NOW+86400, expires_at=NOW+87000, reason="fixture"), NOW+86400)
        self.engine.tick({"BTC-USD": replace(self.quote, timestamp=NOW+86400)},
                         [replace(self.buy, signal_id="next-day", timestamp=NOW+86400)], NOW+86400)
        self.assertEqual(D(self.book.positions()["BTC-USD"]["quantity"]), D(".2394"))
        self.assertEqual(D(self.book.get("realized_pnl")), D("-.11985"))
        self.assertTrue(self.book.get("accounting")["fees_provisional"])

    def test_unknown_journal_halts_without_projecting_or_inventing_funding(self):
        self.enter(); self.exit()
        before = self.book.get("broker_projection_hash")
        self.http.events.append(self.journal())
        result = self.engine.tick({"BTC-USD": self.quote}, [], NOW+2)
        self.assertEqual(result["halt"], "broker_accounting_mismatch")
        self.assertEqual(self.book.get("broker_projection_hash"), before)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM broker_cash_journal_reviews").fetchone()[0], 0)

    def test_reviewed_opening_journal_neither_doubles_cash_nor_becomes_profit(self):
        self.enter(); self.exit(); self.http.fees()
        self.http.events[-1].update(activity_type="CFEE", symbol="USD")
        item = self.journal()
        self.http.events.append(item)
        self.review(item)
        self.book.halt("broker_accounting_mismatch", NOW+2)
        self.assertTrue(self.broker.reconcile(NOW+86400))
        self.assertEqual(D(self.book.get("cash")), D("499.88015"))
        self.assertEqual(D(self.book.get("realized_pnl")), D("-.11985"))
        self.assertEqual(D(self.book.get("accounting")["net_cash_flows"]), 0)
        self.assertEqual(self.book.get("accounting")["opening_cash_journal_ids"], [item["id"]])
        self.assertEqual(self.book.get("halt"), "broker_accounting_mismatch")
        self.review(item)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM broker_cash_journal_reviews").fetchone()[0], 1)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM events WHERE event='accounting.cash_journal_reviewed'").fetchone()[0], 1)
        self.assertTrue(self.broker.reconcile(NOW+86401))

    def test_external_funding_changes_net_flow_and_cash_but_not_profit(self):
        self.enter(); self.exit(); self.http.fees()
        item = self.journal("100")
        self.http.events.append(item); self.http.cash += D(100)
        self.review(item, "external_funding")
        self.assertFalse(self.broker.reconcile(NOW+86400))
        self.assertEqual(self.book.get("entry_pause"), "external_cash_flow_review_required")
        self.assertEqual(D(self.book.get("cash")), D("599.88015"))
        self.assertEqual(D(self.book.get("accounting")["net_cash_flows"]), D(100))
        self.assertEqual(D(self.book.get("realized_pnl")), D("-.11985"))

    def test_conflicting_or_missing_evidence_cannot_reclassify_raw_journal(self):
        self.broker.initialize(NOW)
        item = self.journal()
        for change in [{"evidence_sha256": ""}, {"reviewed_by": " "}, {"classification": "profit"}]:
            with self.subTest(change=change), self.assertRaises(AccountingError):
                self.review(item, **change)
        self.review(item)
        with self.assertRaises(AccountingError):
            self.review(item, "external_funding")
        with self.assertRaises(AccountingError):
            self.broker.journal.ingest([{**item, "net_amount": "100"}], NOW+86401)
        self.assertEqual(json.loads(self.book.db.execute("SELECT payload FROM broker_activities WHERE id=?", (item["id"],)).fetchone()[0]), item)

    def test_opening_aggregate_prevents_duplicate_starting_capital(self):
        self.http.events.append({"id": "prior-deposit", "activity_type": "CSD", "net_amount": "500"})
        self.broker.initialize(NOW)
        item = self.journal(); self.http.events.append(item)
        self.review(item)
        with self.assertRaisesRegex(AccountingError, "double count"):
            self.broker.reconcile(NOW+86400)

    def test_multiple_opening_journals_cannot_exceed_starting_cash(self):
        self.broker.initialize(NOW)
        for ident in ["first", "second"]:
            item = self.journal("300", id=ident)
            self.review(item); self.http.events.append(item)
        with self.assertRaisesRegex(AccountingError, "double count"):
            self.broker.reconcile(NOW+86400)

    def test_invalid_pending_or_foreign_currency_journal_stays_blocked(self):
        self.broker.initialize(NOW)
        for change in [{"status": "pending"}, {"currency": "EUR"}, {"qty": "1"}, {"symbol": "BTCUSD"}]:
            item = self.journal(id=str(change), **change)
            with self.subTest(change=change), self.assertRaises(AccountingError):
                self.review(item)

    def test_review_is_bound_to_admission_evidence_and_book_identity(self):
        self.broker.initialize(NOW)
        item = self.journal(); self.http.events.append(item)
        self.review(item)
        self.book.set("identity", {"broker": "other", "policy": self.p.fingerprint})
        self.book.db.commit()
        with self.assertRaises(AccountingError):
            self.broker.reconcile(NOW+86400)

    def test_review_survives_recorded_policy_migration_but_not_an_unrecorded_one(self):
        self.broker.initialize(NOW)
        item = self.journal(); self.http.events.append(item)
        self.review(item)
        old = self.book.get("identity")
        migrated = {**old, "policy": "9"*64}
        self.book.set("identity", migrated)
        self.book.set("session_history", [{"from_policy": old["policy"], "to_policy": "9"*64}])
        self.book.db.commit()
        self.assertTrue(self.broker.reconcile(NOW+86400))  # recorded boundary: the review stays valid
        self.book.set("identity", {**migrated, "policy": "8"*64})
        self.book.db.commit()
        with self.assertRaises(AccountingError):
            self.broker.reconcile(NOW+86401)  # unrecorded policy change: still fails closed
        self.book.set("identity", {**migrated, "broker": "other"})
        self.book.db.commit()
        with self.assertRaises(AccountingError):
            self.broker.reconcile(NOW+86402)  # broker change is never accepted

    def test_wrong_opening_classification_cannot_reconcile_new_money(self):
        self.broker.initialize(NOW)
        item = self.journal(); self.http.events.append(item); self.http.cash += D(500)
        self.review(item)
        before = self.book.get("broker_projection_hash"), self.book.get("cash"), self.book.get("realized_pnl")
        with self.assertRaisesRegex(AccountingError, "balance differs"):
            self.broker.reconcile(NOW+86400)
        self.assertEqual((self.book.get("broker_projection_hash"), self.book.get("cash"), self.book.get("realized_pnl")), before)

    def test_review_and_cash_fees_survive_restart(self):
        from evolver.trading.ledger import Ledger
        self.enter(); self.exit(); self.http.fees()
        self.http.events[-1].update(activity_type="CFEE", symbol="USD")
        item = self.journal(); self.http.events.append(item); self.review(item)
        self.assertTrue(self.broker.reconcile(NOW+86400))
        before = self.book.get("broker_projection_hash"), self.book.get("cash")
        self.book.close()
        self.book = Ledger(self.path, self.p, self.broker.identity)
        self.broker.bind(self.book)
        self.assertTrue(self.broker.reconcile(NOW+86401))
        self.assertEqual((self.book.get("broker_projection_hash"), self.book.get("cash")), before)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM broker_cash_journal_reviews").fetchone()[0], 1)

    def test_malformed_fee_hard_halts_without_overwriting_projection(self):
        self.enter(); self.exit()
        before = self.book.get("broker_projection_hash")
        self.http.events.append({"id": "bad-fee", "activity_type": "CFEE", "date": "2026-10-04", "symbol": "USD"})
        result = self.engine.tick({"BTC-USD": self.quote}, [], NOW+2)
        self.assertEqual(result["halt"], "broker_accounting_mismatch")
        self.assertEqual(self.book.get("broker_projection_hash"), before)

    def test_cash_cfee_cap_stays_blocked(self):
        self.enter(); self.exit(); self.http.fees()
        self.http.events[-1].update(activity_type="CFEE", symbol="USD", net_amount="-1")
        with self.assertRaisesRegex(AccountingError, "approved fee"):
            self.broker.reconcile(NOW+86400)

    def test_cash_cfee_wrong_order_side_stays_blocked(self):
        self.enter(); self.exit()
        other = {"id": "wrong-side", "activity_type": "CFEE", "net_amount": "-.01",
                 "symbol": "USD", "date": "2026-10-04", "order_id": self.http.events[0]["order_id"]}
        self.http.events.append(other)
        with self.assertRaisesRegex(AccountingError, "attributed"):
            self.broker.reconcile(NOW+86400)

    def test_order_id_fee_attribution_is_exact_and_keeps_raw_amount(self):
        self.enter(); self.exit(); self.http.fees()
        sell = next(e for e in self.http.events if e["activity_type"] == "FILL" and e["side"] == "sell")
        self.http.events[-1].update(activity_type="CFEE", symbol="USD", order_id=sell["order_id"])
        self.assertTrue(self.broker.reconcile(NOW+86400))
        allocation = self.book.db.execute("SELECT fill_id,amount,method FROM broker_fee_allocations WHERE activity_id='usd-fee'").fetchone()
        self.assertEqual(tuple(allocation), (sell["id"], "0.05985", "order_id"))

    def test_missing_review_evidence_or_changed_classification_fails_replay(self):
        self.broker.initialize(NOW)
        item = self.journal(); self.http.events.append(item); self.review(item)
        original = self.book.db.execute("SELECT payload FROM broker_cash_journal_reviews").fetchone()[0]
        for field in ["evidence_sha256", "reviewed_by", "classification"]:
            changed = json.loads(original)
            changed.pop(field)
            with self.book.db:
                self.book.db.execute("UPDATE broker_cash_journal_reviews SET payload=?", (json.dumps(changed),))
            with self.subTest(field=field), self.assertRaises(AccountingError):
                self.broker.reconcile(NOW+86400)
        changed = json.loads(original); changed["classification"] = "external_funding"
        with self.book.db:
            self.book.db.execute("UPDATE broker_cash_journal_reviews SET payload=?", (json.dumps(changed),))
        with self.assertRaisesRegex(AccountingError, "audit decision"):
            self.broker.reconcile(NOW+86400)

    def test_missing_admission_evidence_cannot_create_review(self):
        self.broker.initialize(NOW)
        with self.book.db:
            self.book.db.execute("DELETE FROM events WHERE event='broker.admitted'")
        with self.assertRaisesRegex(AccountingError, "admission evidence"):
            self.review(self.journal())
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM broker_cash_journal_reviews").fetchone()[0], 0)
