"""Offline execution invariants. No network, credentials, old runtime state or trading side effects."""
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from evolver.trading.agents import intent_digest, review_entry
from evolver.trading.broker import PaperBroker
from evolver.trading.contracts import Intent, OrderReport, Policy, Quote, decimal as D
from evolver.trading.engine import Engine
from evolver.trading.ledger import Ledger


NOW = 1_791_129_600.0


def policy(**changes):
    values = dict(mode="paper", quote_currency="USD", starting_cash="500", max_position_notional="25",
                  max_total_notional="50", max_loss_per_trade="15", daily_loss_limit="50", max_drawdown="50",
                  max_trades_per_day=6, max_spread_bps="20", max_quote_age_seconds=30,
                  supervisor_ttl_seconds=600, fee_bps="25", slippage_bps="5",
                  allowed_instruments=("BTC-USD", "ETH-USD"), approved_strategies=("ema@v1",),
                  trading_hours_utc=tuple(range(24)), trading_weekdays_utc=tuple(range(7)))
    values.update(changes)
    return Policy.from_dict(values)


def approve(system, prompt):
    output = json.loads(prompt)["required_output"]
    return json.dumps({**output, "verdict": "approve", "reason": "Fixture approval, not a model call"})


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = policy()
        self.path = Path(self.tmp.name) / "book.sqlite"
        self.book = Ledger(self.path, self.p, PaperBroker.identity)
        self.broker = PaperBroker(self.book, self.p)
        self.engine = Engine(self.book, self.p, self.broker, analyst=approve, reviewer=approve)
        self.quotes = {"BTC-USD": Quote("BTC-USD", D("99.9"), D("100"), NOW)}
        self.buy = Intent("bar-1", "ema@v1", "BTC-USD", "buy", D("0.24"), D("97"), NOW,
                          "test", D("100.05"))
        self.resume()

    def tearDown(self):
        self.book.close()
        self.tmp.cleanup()

    def resume(self, at=NOW):
        self.engine.supervise(dict(action="resume_entries", risk_scale="1", policy_hash=self.p.fingerprint,
                                   issued_at=at, expires_at=at + 600, reason="test lease"), at)

    def test_fill_accounting_and_replay_after_restart(self):
        first = self.engine.tick(self.quotes, [self.buy], NOW)
        self.assertEqual(len(first["positions"]), 1)
        self.assertEqual(D(first["cash"]), D("475.92797"))
        self.book.close()
        self.book = Ledger(self.path, self.p, PaperBroker.identity)
        self.engine = Engine(self.book, self.p, PaperBroker(self.book, self.p), analyst=approve, reviewer=approve)
        second = self.engine.tick(self.quotes, [self.buy], NOW)
        self.assertEqual(first["cash"], second["cash"])
        self.assertEqual(len(self.book.orders()), 1)
        sell = Intent("exit-1", "ema@v1", "BTC-USD", "sell", D("0.24"), D(0), NOW+1, "exit")
        quotes = {"BTC-USD": Quote("BTC-USD", D("105"), D("105.1"), NOW+1)}
        closed = self.engine.tick(quotes, [sell], NOW+1)
        self.assertEqual(closed["positions"], [])
        self.assertEqual(D(closed["cash"]) - D(500), D(closed["realized_pnl"]))

    def test_oversized_and_unapproved_entries_never_reach_broker(self):
        for intent in (replace(self.buy, quantity=D("1")), replace(self.buy, strategy="hacker@v1"),
                       replace(self.buy, stop_price=D(0)), replace(self.buy, limit_price=D(150))):
            self.engine.tick(self.quotes, [intent], NOW)
        self.assertEqual(self.book.orders(), [])

    def test_stale_and_future_quotes_block_entries(self):
        for timestamp in (NOW-31, NOW+1):
            self.engine.tick({"BTC-USD": replace(self.quotes["BTC-USD"], timestamp=timestamp)}, [self.buy], NOW)
        self.assertFalse(self.book.orders())

    def test_reviewer_veto_invalid_and_unavailable_fail_closed(self):
        for fn in (None, lambda *_: '{}', lambda *_: '{"verdict":"approve","risk_limit":1000000}',
                   lambda s, p: approve(s, p).replace('approve', 'reject')):
            self.engine.reviewer = fn
            self.engine.tick(self.quotes, [self.buy], NOW)
        self.assertEqual(self.book.orders(), [])

    def test_supervisor_cannot_change_policy_raise_size_or_replay(self):
        current = self.book.get("supervisor")
        command = dict(action="resume_entries", risk_scale="2", policy_hash=self.p.fingerprint,
                       issued_at=NOW+1, expires_at=NOW+600, reason="grow profit")
        with self.assertRaises(ValueError):
            self.engine.supervise(command, NOW+1)
        with self.assertRaises(ValueError):
            self.resume()
        with self.assertRaises(ValueError):
            self.engine.supervise({**command, "risk_scale": "1", "max_leverage": 10}, NOW+1)
        self.assertEqual(self.book.get("supervisor"), current)

    def test_protective_exit_survives_expired_ai_lease(self):
        self.engine.tick(self.quotes, [self.buy], NOW)
        self.engine.analyst = self.engine.reviewer = None
        later = NOW + 601
        quotes = {"BTC-USD": Quote("BTC-USD", D(90), D("90.1"), later)}
        result = self.engine.tick(quotes, [], later)
        self.assertEqual(result["positions"], [])
        self.assertLess(D(result["realized_pnl"]), 0)

    def test_unknown_submit_recovers_without_second_order(self):
        submit = self.broker.submit
        def dropped_reply(intent, quote):
            submit(intent, quote)
            raise TimeoutError("exchange filled but reply was lost")
        self.broker.submit = dropped_reply
        first = self.engine.tick(self.quotes, [self.buy], NOW)
        self.assertEqual(first["halt"], "unresolved_order")
        self.broker.submit = submit
        self.engine.tick(self.quotes, [self.buy], NOW+1)
        buys = [o for o in self.book.orders() if json.loads(o["intent"])["side"] == "buy"]
        self.assertEqual(len(buys), 1)

    def test_missing_receipt_never_resubmits(self):
        self.book.reserve(self.buy, NOW)
        result = self.engine.tick(self.quotes, [self.buy], NOW)
        self.assertEqual(result["halt"], "unresolved_order")
        self.assertIsNone(self.broker.lookup(self.buy.client_id))

    def test_partial_fills_duplicate_reports_and_base_fees(self):
        self.book.reserve(self.buy, NOW)
        first = OrderReport(self.buy.client_id, "partial", D(".1"), D(10), D(0), D(".00025"))
        self.book.apply_report(first, NOW)
        self.book.apply_report(first, NOW)
        self.assertEqual(D(self.book.positions()["BTC-USD"]["quantity"]), D(".09975"))
        second = OrderReport(self.buy.client_id, "filled", D(".24"), D(24), D(0), D(".0006"))
        self.book.apply_report(second, NOW)
        self.assertEqual(D(self.book.positions()["BTC-USD"]["quantity"]), D(".2394"))
        self.assertEqual(D(self.book.get("cash")), D(476))
        with self.assertRaises(ValueError):
            self.book.apply_report(first, NOW)

    def test_broker_mismatch_halts_new_entries(self):
        self.broker.account = lambda: {"cash": D(450), "positions": {}, "open_order_ids": []}
        result = self.engine.tick(self.quotes, [self.buy], NOW)
        self.assertEqual(result["halt"], "broker_ledger_mismatch")
        self.assertEqual(self.book.orders(), [])

    def test_model_latency_cannot_use_stale_quotes(self):
        calls = iter([0, 35])
        self.engine.monotonic = lambda: next(calls)
        self.engine.tick(self.quotes, [self.buy], NOW)
        self.assertEqual(self.book.orders(), [])

    def test_book_cannot_switch_mode_account_or_risk_limits(self):
        for changed in (replace(self.p, mode="live"), replace(self.p, daily_loss_limit=D(40))):
            with self.assertRaises(ValueError):
                Ledger(self.path, changed, PaperBroker.identity)
        with self.assertRaises(ValueError):
            Ledger(self.path, self.p, "another-account")

    def test_bad_numbers_and_leverage_are_rejected(self):
        for bad in ("NaN", "Infinity", -1, True):
            with self.assertRaises(ValueError):
                policy(starting_cash=bad)
        with self.assertRaises(ValueError):
            policy(max_total_notional="1000")

    def test_paper_does_not_start_live_study_and_remains_time_bounded(self):
        p = policy(study_start_event="first_live_fill", study_end_utc=None)
        book = Ledger(Path(self.tmp.name) / "preflight.sqlite", p, PaperBroker.identity)
        try:
            engine = Engine(book, p, PaperBroker(book, p))
            first = engine.tick(self.quotes, [], NOW)
            self.assertIsNone(first["study_started_at"])
            self.assertIsNone(first["study_deadline"])
            self.assertEqual(first["paper_deadline"], NOW + 90*86400)
            end = NOW + 90*86400
            result = engine.tick({"BTC-USD": Quote("BTC-USD", 100, 101, end)}, [], end)
            self.assertEqual(result["halt"], "paper_preflight_complete")
        finally:
            book.close()

    def test_live_clock_requires_broker_timestamp_and_survives_replay(self):
        # Pure ledger fixture; no live adapter, credentials or network operations.
        p = policy(mode="live", study_start_event="first_live_fill", study_end_utc=None)
        book = Ledger(Path(self.tmp.name) / "clock-fixture.sqlite", p, "synthetic-live-clock")
        try:
            book.reserve(self.buy, NOW)
            report = OrderReport(self.buy.client_id, "filled", D(".24"), D(24), D(".06"))
            with self.assertRaises(ValueError):
                book.apply_report(report, NOW+5)
            self.assertEqual(book.get("cash"), "500")
            report = replace(report, first_fill_timestamp=NOW+2)
            book.apply_report(report, NOW+5)
            book.apply_report(report, NOW+10)
            self.assertEqual(book.get("study_started_at"), NOW+2)
            fake = type("Broker", (), {"mode": "live"})()
            engine = Engine(book, p, fake)
            self.assertEqual(engine._study_deadline(), NOW+2+90*86400)
            self.assertEqual(engine._expiry_reason(NOW+2+90*86400), "study_complete")
        finally:
            book.close()


if __name__ == "__main__":
    unittest.main()
