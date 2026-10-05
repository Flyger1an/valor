"""Six-symbol admission and migration invariants; synthetic data, no external clients."""
import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from evolver.trading.contracts import Intent, Quote, encode, decimal as D
from evolver.trading.experiment import Experiment, digest, metrics, fee
from evolver.trading.experiment_runner import capture
from evolver.trading.shadow_sizing import KELLY_RULES_V3, recommend
from evolver.trading.universe import migrate_ledger, validate_expansion
from evolver.trading.ledger import Ledger
from evolver.trading.broker import PaperBroker
from evolver.trading.engine import Engine, write_snapshot
from evolver.trading.runtime import Feed, research_tick
from evolver.trading.telegram_alerts import PaperOrderAlerts, migrate_config, digest as alert_digest
from test_trading_experiment import NOW, SPEC, SYMBOLS, frame, histories, policy
from test_trading_evidence_v2 import activate
from test_trading_runtime import approve, policy as runtime_policy


SIX = ("ADA-USD", "BTC-USD", "ETH-USD", "SHIB-USD", "SKY-USD", "WIF-USD")


def expanded_policy():
    return replace(policy(), allowed_instruments=SIX)


def expand(exp, at=NOW-118):
    return exp.apply({"type": "universe_policy_update", "id": "six-symbol-boundary",
        "observed_at": at, "from_policy": policy().fingerprint, "new_policy": asdict(expanded_policy()),
        "rules_hash": digest(KELLY_RULES_V3)})


def six_frame(at=NOW, signals=(), capacity="1000"):
    f = frame(at, capacity=capacity)
    f["policy_hash"] = expanded_policy().fingerprint
    q = f["quotes"]["BTC-USD"]
    f["quotes"] = {s: {**q, "minimum_quantity": ".000001", "price_increment": ".000000001"} for s in SIX}
    neutral = histories((), at=at)["BTC-USD"]
    signal = histories(("BTC-USD",), at=at)["BTC-USD"]
    f["bars"] = {s: copy.deepcopy(signal if s in signals else neutral) for s in SIX}
    return f


class UniverseExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"experiment.sqlite"
        self.exp = Experiment(self.path, policy(), epoch=NOW-120, strategy=SPEC.version)
        self.exp.apply(frame(NOW-120))
        activate(self.exp, NOW-119)
        self.prefix = [tuple(r) for r in self.exp.db.execute("SELECT * FROM experiment_events ORDER BY seq")]
        self.origin = copy.deepcopy(self.exp.identity)
        expand(self.exp)

    def tearDown(self):
        self.exp.close()
        self.temp.cleanup()

    def test_append_only_migration_restart_replay_and_no_reset(self):
        self.exp.apply(six_frame(signals=SIX))
        self.exp.apply(six_frame(NOW+5, signals=SIX))
        before = self.exp.state()
        self.assertEqual(self.exp.identity, self.origin)
        self.assertEqual([tuple(r) for r in self.exp.db.execute("SELECT * FROM experiment_events ORDER BY seq")][:len(self.prefix)], self.prefix)
        self.assertEqual(self.exp.report()["epoch"], NOW-120)
        self.assertEqual(self.exp.report()["evaluation_end"], NOW-120+90*86400)
        self.assertEqual(self.exp.report()["symbols"], list(SIX))
        self.assertTrue(self.exp.verify_replay()["verified"])
        self.exp.close()
        self.exp = Experiment(self.path, expanded_policy())
        self.assertEqual(self.exp.state(), before)
        self.assertTrue(self.exp.verify_replay()["verified"])
        with self.assertRaises(ValueError):
            Experiment(self.path, policy())
        with self.assertRaises(ValueError):
            Experiment(self.path, replace(expanded_policy(), max_total_notional=D(60)))

    def test_unused_stale_symbol_does_not_block_fresh_entries_or_later_fills(self):
        f = six_frame(signals=SIX)
        f["quotes"]["ETH-USD"]["timestamp"] = NOW-60
        self.exp.apply(f)
        books = self.exp.state()["books"]
        self.assertEqual(list(books["henry"]["pending"]), ["ADA-USD"])
        self.assertEqual(set(books["baseline"]["pending"]), {"ADA-USD", "BTC-USD"})
        f = six_frame(NOW+5, signals=SIX)
        f["quotes"]["ETH-USD"]["timestamp"] = NOW-60
        report = self.exp.apply(f)
        # The first fill's costs reduce equity and may shrink the cap before the second fill.
        self.assertEqual([b["fills"] for b in report["books"]], [1, 0, 1])
        self.assertNotIn("stale_portfolio_mark_before_fill", self.exp.state()["books"]["baseline"]["skips"])
        self.assertTrue(report["marks_fresh"])
        self.assertFalse(report["quote_status"]["ETH-USD"]["fresh"])
        self.assertTrue(self.exp.verify_replay()["verified"])

    def test_six_signals_share_cash_exposure_loss_and_kelly_cold_start(self):
        self.exp.apply(six_frame(signals=SIX))
        state = self.exp.state()
        baseline = state["books"]["baseline"]
        pending = list(baseline["pending"].values())
        self.assertLessEqual(sum(D(o["quantity"])*D(o["limit"]) for o in pending), D(50))
        self.assertTrue(all(D(o["quantity"])*D(o["limit"]) <= 25 for o in pending))
        self.assertLessEqual(sum(D(o["planned_loss"]) for o in pending), D(50))
        self.assertEqual(state["books"]["kelly"]["pending"], {})
        self.assertEqual(state["books"]["kelly"]["joint_sizing"]["reason"], "insufficient_forward_evidence")
        self.assertEqual(len(state["books"]["henry"]["pending"]), 1)
        self.exp.apply(six_frame(NOW+5, signals=SIX))
        for b in self.exp.state()["books"].values():
            self.assertGreaterEqual(D(b["cash"]), 0)
            self.assertLessEqual(metrics(b, self.exp.state()["quotes"])["exposure"], 500)

    def test_wide_spreads_and_catalog_minima_remain_entry_gates(self):
        f = six_frame(signals=SIX)
        f["quotes"]["ADA-USD"]["ask"] = "101"
        f["quotes"]["SHIB-USD"]["minimum_quantity"] = "1000"
        self.exp.apply(f)
        for b in self.exp.state()["books"].values():
            self.assertNotIn("ADA-USD", b["pending"])
            self.assertNotIn("SHIB-USD", b["pending"])
        self.assertEqual(list(self.exp.state()["books"]["henry"]["pending"]), ["BTC-USD"])

    def test_zero_volume_still_produces_no_fabricated_fills(self):
        self.exp.apply(six_frame(signals=SIX, capacity="0"))
        self.exp.apply(six_frame(NOW+5, signals=SIX, capacity="0"))
        for b in self.exp.state()["books"].values():
            self.assertEqual(b["fills"], {})
            self.assertEqual(D(b["cash"]), 500)

    def test_stale_held_mark_blocks_new_risk_and_is_reported_honestly(self):
        self.exp.apply(six_frame(signals=("BTC-USD",)))
        self.exp.apply(six_frame(NOW+5, signals=("BTC-USD",)))
        state = self.exp.state()
        state["last_at"] = NOW+50
        state["quotes"]["ADA-USD"]["timestamp"] = NOW+50
        book = state["books"]["baseline"]
        reason = self.exp._entry(book, {"instrument": "ADA-USD", "id": "new-observation"}, [],
            six_frame(NOW+50), state, NOW+50)
        self.assertEqual(reason, "stale_quote")
        self.assertFalse(self.exp.report(state)["marks_fresh"])
        self.exp._exits(book, state, NOW+50)
        self.assertNotIn("BTC-USD", book["pending"])

    def test_tick_grid_does_not_raise_entry_slippage_limit(self):
        f = six_frame(signals=("ADA-USD",))
        f["quotes"]["ADA-USD"]["price_increment"] = ".03"
        self.exp.apply(f)
        order = self.exp.state()["books"]["baseline"]["pending"]["ADA-USD"]
        self.assertEqual(D(order["limit"]) % D(".03"), 0)
        self.assertLessEqual(D(order["limit"]), D(f["quotes"]["ADA-USD"]["ask"])*D("1.0005"))
        stop = D(order["stop"])*D(".9995")
        from decimal import ROUND_DOWN
        stop = (stop/D(".03")).to_integral_value(rounding=ROUND_DOWN)*D(".03")
        qty, limit = D(order["quantity"]), D(order["limit"])
        self.assertEqual(D(order["planned_loss"]), (limit-stop)*qty+fee(limit*qty, 25)+fee(stop*qty, 25))

    def test_missing_unheld_quote_is_captured_without_blocking_other_symbols(self):
        root = Path(self.temp.name)/"source"
        event = six_frame()
        quotes = event["quotes"].copy()
        del quotes["WIF-USD"]
        header = {"policy_hash": expanded_policy().fingerprint, "source": "alpaca", "venue": "us", "timestamp": NOW}
        rules = {s: {k: event["quotes"][s][k] for k in ("increment", "minimum_quantity", "price_increment")} for s in SIX}
        for path, value in {"market/quotes.json": {**header, "quotes": quotes},
            "market/signals.json": {**header, "histories": event["bars"], "instrument_rules": rules},
            "outbox/snapshot.json": {}}.items():
            write_snapshot(root/path, value)
        self.exp.policy = expanded_policy()
        captured = capture(self.exp, root, NOW)
        self.assertEqual(set(captured["quotes"]), set(SIX)-{"WIF-USD"})
        self.assertIn("WIF-USD", captured["unavailable_symbols"])

    def test_legacy_positions_and_fees_survive_boundary_without_rebalancing(self):
        path = Path(self.temp.name)/"held"/"experiment.sqlite"
        exp = Experiment(path, policy(), epoch=NOW-120, strategy=SPEC.version)
        try:
            exp.apply(frame(NOW-120, bars=histories(at=NOW-120)))
            exp.apply(frame(NOW-115))
            activate(exp, NOW-114)
            before = exp.state()
            expand(exp, NOW-113)
            after = exp.state()
            for name in ("baseline", "kelly", "henry"):
                for key in ("cash", "positions", "fills", "lots", "pending", "attempts", "day_start_equity", "peak"):
                    self.assertEqual(after["books"][name][key], before["books"][name][key])
            self.assertTrue(exp.verify_replay()["verified"])
        finally:
            exp.close()

    def test_old_daily_classifications_are_retained_and_boundary_day_excluded(self):
        path = Path(self.temp.name)/"cohorts"/"experiment.sqlite"
        exp = Experiment(path, policy(), epoch=NOW-120, strategy=SPEC.version)
        next_day = (NOW//86400+1)*86400+1
        try:
            exp.apply(frame(NOW-120))
            activate(exp, NOW-119)
            exp.apply(frame(next_day))
            prior = copy.deepcopy(exp.state()["evidence"]["blocks"])
            expand(exp, next_day+1)
            self.assertEqual(exp.state()["evidence"]["blocks"], prior)
            report = exp.report()
            self.assertFalse(report["evidence_quality"]["complete_so_far"])
            self.assertEqual(report["evidence_quality"]["legacy_blocks_retained"], len(prior))
            self.assertEqual(report["usable_evidence_blocks"], 0)
            self.assertTrue(exp.verify_replay()["verified"])
        finally:
            exp.close()


class UniverseRiskAndFeedTests(unittest.TestCase):
    def test_source_ledger_migration_preserves_all_accounting_and_supervisor(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"book.sqlite"
            old = runtime_policy()
            new = replace(old, allowed_instruments=SIX)
            book = Ledger(path, old, PaperBroker.identity)
            with book.db:
                book.set("cash", "499.9")
                book.set("realized_pnl", "-.1")
                book.event(NOW, "fixture", {"class": "engineering"})
            before = dict(book.db.execute("SELECT key,value FROM meta"))
            prefix = list(book.db.execute("SELECT * FROM events"))
            supervisor = book.get("supervisor")
            book.close()
            migrate_ledger(path, old, new, NOW+1)
            book = Ledger(path, new, PaperBroker.identity)
            for key in before:
                if key != "identity":self.assertEqual(book.get(key), json.loads(before[key]))
            self.assertEqual(book.get("supervisor"), supervisor)
            self.assertEqual(list(book.db.execute("SELECT * FROM events"))[:len(prefix)], prefix)
            book.close()
            for changed in (replace(new, max_total_notional=D(60)), replace(new, mode="live"), old):
                with self.assertRaises(ValueError):validate_expansion(old, changed)

    def test_source_engine_shares_caps_across_six_and_rejects_stale_symbol_only(self):
        with tempfile.TemporaryDirectory() as temp:
            p = runtime_policy(allowed_instruments=SIX)
            book = Ledger(Path(temp)/"book.sqlite", p, PaperBroker.identity)
            engine = Engine(book, p, PaperBroker(book, p), analyst=approve, reviewer=approve)
            engine.supervise({"action": "resume_entries", "risk_scale": "1", "policy_hash": p.fingerprint,
                "issued_at": NOW, "expires_at": NOW+600, "reason": "synthetic fixture"}, NOW)
            quotes = {s: Quote(s, "99.9", "100", NOW-31 if s == "ETH-USD" else NOW) for s in SIX}
            intents = [Intent(s, "ema@v1", s, "buy", ".24", "97", NOW, "fixture", "100.05") for s in SIX]
            result = engine.tick(quotes, intents, NOW)
            self.assertLessEqual(D(result["exposure"]), 50)
            self.assertLessEqual(len(result["positions"]), 2)
            self.assertNotIn("ETH-USD", book.positions())
            self.assertGreater(len(result["positions"]), 0)
            book.close()

    def test_quote_refresh_does_not_wait_for_bar_bootstrap(self):
        with tempfile.TemporaryDirectory() as temp:
            entered, release = threading.Event(), threading.Event()
            class Provider:
                source, venue = "alpaca", "us"
                def quotes(self, instruments):return {s: Quote(s, 99, 100, NOW) for s in instruments}
                def instrument_rules(self, instruments):return {s: {"increment": ".001", "minimum_quantity": ".001", "price_increment": ".01"} for s in instruments}
                def bars(self, instruments, now):entered.set(); release.wait(2); return {s: [] for s in instruments}
            with patch.dict("os.environ", {"VALOR_DATA_SOURCE": "alpaca"}), patch("evolver.trading.market.AlpacaData", return_value=Provider()), patch("evolver.trading.alpaca.AlpacaHTTP"):
                feed = Feed(expanded_policy(), Path(temp), background=True)
            try:
                feed.tick(NOW)
                self.assertTrue(entered.wait(1))
                feed.tick(NOW+5)
                data = json.loads((Path(temp)/"quotes.json").read_text())
                self.assertEqual(set(data["quotes"]), set(SIX))
                self.assertTrue(feed.bar_thread.is_alive())
            finally:
                release.set()
                feed.bar_thread.join(3)

    def test_research_retains_old_classification_and_starts_new_forward_cutoff(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {s: root/s for s in ("research", "market", "outbox")}
            prior = {"policy_hash": policy().fingerprint, "status": "review_required", "evidence_hash": "old-proof", "end": NOW-300}
            write_snapshot(paths["research"]/"assessment.json", prior)
            write_snapshot(paths["market"]/"history.json", {"source": "alpaca", "policy_hash": expanded_policy().fingerprint, "timestamp": NOW, "histories": {}})
            with patch("evolver.trading.learning.evaluate", return_value={"status": "collecting_forward_evidence"}) as evaluate:
                research_tick(expanded_policy(), paths, NOW)
            self.assertEqual(evaluate.call_args.args[2]["cutoff"], NOW+300)
            self.assertEqual([json.loads(p.read_text()) for p in paths["research"].glob("assessment-*.json")], [prior])
            current = json.loads((paths["research"]/"assessment.json").read_text())
            self.assertEqual(current["prior_policy_hash"], policy().fingerprint)


class JointKellyUniverseTests(unittest.TestCase):
    def rows(self, missing=False):
        return [{"complete": True, "settled": True, "available_at": NOW-1, "end": NOW-86400*(i+1),
            "reference_notional": "25", "evidence_version": KELLY_RULES_V3["version"], "cohort": "six",
            "pnl": {s: str(D(".02")*25) for s in (SIX[:-1] if missing else SIX)},
            "active": {s: s in ("BTC-USD", "ETH-USD") for s in SIX}} for i in range(30)]

    def test_new_assets_without_active_evidence_remain_cash_and_total_is_joint(self):
        result = recommend(self.rows(), SIX, set(SIX), {}, NOW, KELLY_RULES_V3["version"], "six")
        self.assertEqual(result["reason"], "positive_stress_adjusted_growth")
        self.assertLessEqual(sum(result["raw_fraction"].values()), 1+1e-12)
        self.assertLessEqual(sum(result["fractional_fraction"].values()), .25+1e-12)
        for s in set(SIX)-{"BTC-USD", "ETH-USD"}:self.assertEqual(result["fractional_fraction"][s], 0)
        self.assertEqual(result, recommend(self.rows(), tuple(reversed(SIX)), set(SIX), {}, NOW, KELLY_RULES_V3["version"], "six"))

    def test_missing_new_returns_or_old_cohort_are_not_filled_with_zeros(self):
        self.assertEqual(recommend(self.rows(True), SIX, set(SIX), {}, NOW, KELLY_RULES_V3["version"], "six")["reason"], "missing_synchronized_asset_evidence")
        self.assertEqual(recommend(self.rows(), SIX, set(SIX), {}, NOW, KELLY_RULES_V3["version"], "other")["blocks"], 0)

    def test_held_weights_remain_fixed_and_leave_cash_budget(self):
        result = recommend(self.rows(), SIX, set(SIX), {"BTC-USD": .8}, NOW, KELLY_RULES_V3["version"], "six")
        self.assertEqual(result["raw_fraction"]["BTC-USD"], 0)
        self.assertLessEqual(sum(result["raw_fraction"].values()), .2+1e-12)


class NotificationUniverseTests(unittest.TestCase):
    def test_config_boundary_preserves_cursor_and_deduplication_namespace(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)/"alerts.sqlite"
            old = {"schema_version": 1, "policy_hash": "a"*64, "broker_identity": "alpaca:demo:"+"b"*16,
                   "recipient_hash": "c"*64, "bot_username": "fixture_bot", "instruments": SYMBOLS}
            notifier = PaperOrderAlerts(Path(temp)/"source.sqlite", state, old)
            with notifier.db:
                notifier.set("cursor", 31)
                notifier.set("cursor_hash", "saved-source-event-hash")
                notifier.db.execute("INSERT INTO historical_orders VALUES (?)", (alert_digest(old["policy_hash"]+":accepted:old-order"),))
            notifier.close()
            new = {**old, "policy_hash": "d"*64, "instruments": list(SIX), "acceptance_identity_policy_hash": old["policy_hash"]}
            migrate_config(state, old, new, NOW)
            notifier = PaperOrderAlerts(Path(temp)/"source.sqlite", state, new)
            self.assertEqual(notifier.get("cursor"), 31)
            self.assertEqual(notifier.get("cursor_hash"), "saved-source-event-hash")
            self.assertEqual(notifier.db.execute("SELECT id FROM historical_orders").fetchone()[0], alert_digest("a"*64+":accepted:old-order"))
            notifier.close()


if __name__ == "__main__":unittest.main()
