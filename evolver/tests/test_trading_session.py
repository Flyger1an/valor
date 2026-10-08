"""24/7 session expansion: only hours, weekdays, entry pace and call budget widen; nothing resets."""
import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

from evolver.trading.contracts import Policy, decimal as D
from evolver.trading.engine import write_snapshot
from evolver.trading.experiment import Experiment, IntegrityError
from evolver.trading.experiment_runner import capture
from evolver.trading.ledger import Ledger
from evolver.trading.broker import PaperBroker
from evolver.trading.quote_admission import publish
from evolver.trading.session import migrate_ledger, record_feed_transition, validate_session_expansion
from evolver.trading.telegram_alerts import PaperOrderAlerts, migrate_config
from test_trading_experiment import NOW, SPEC, SYMBOLS, frame
from test_trading_evidence_v2 import activate
from test_trading_runtime import policy as runtime_policy
from test_trading_universe import SIX, expand, expanded_policy, six_frame

REPO = Path(__file__).resolve().parents[2]


def widen(p):
    return replace(p, trading_hours_utc=tuple(range(24)), trading_weekdays_utc=tuple(range(7)),
                   max_model_calls_per_day=200, max_trades_per_day=24)


class SessionPolicyTests(unittest.TestCase):
    def test_repository_policies_differ_only_in_session_fields(self):
        old = Policy.from_dict(json.loads((REPO/"infra/trading/policy.demo.weekday.json").read_text()))
        new = Policy.from_dict(json.loads((REPO/"infra/trading/policy.demo.json").read_text()))
        self.assertEqual(old.fingerprint, "b8cae8cf555abb5a4b2b4bf59b90d40ca02aa4437bfa4fca626e9021aff93a68")
        change = validate_session_expansion(old, new)
        self.assertEqual(change["added_weekdays_utc"], [5, 6])
        self.assertEqual(len(change["added_hours_utc"]), 16)
        self.assertEqual(change["entry_attempts_per_day"], [6, 24])
        for key in ("max_position_notional", "max_total_notional", "max_loss_per_trade", "daily_loss_limit",
                    "max_drawdown", "max_spread_bps", "max_quote_age_seconds", "max_model_cost_per_day", "mode"):
            self.assertEqual(getattr(old, key), getattr(new, key), key)

    def test_only_widening_is_allowed_and_every_risk_limit_is_locked(self):
        old = runtime_policy()
        new = widen(old)
        validate_session_expansion(old, new)
        for bad in (replace(new, max_loss_per_trade=D(16)), replace(new, daily_loss_limit=D(40)),
                    replace(new, max_position_notional=D(30)), replace(new, max_model_cost_per_day=D(1)),
                    replace(new, mode="live"), replace(new, max_trades_per_day=49),
                    replace(new, max_model_calls_per_day=501), replace(new, max_trades_per_day=old.max_trades_per_day-1),
                    replace(new, trading_hours_utc=(0,)), old):
            with self.assertRaises(ValueError):
                validate_session_expansion(old, bad)

    def test_ledger_migration_preserves_accounting_supervisor_and_event_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"book.sqlite"
            old = runtime_policy()
            new = widen(old)
            book = Ledger(path, old, PaperBroker.identity)
            with book.db:
                book.set("cash", "499.9")
                book.set("realized_pnl", "-.1")
                book.event(NOW, "fixture", {"class": "engineering"})
            before = dict(book.db.execute("SELECT key,value FROM meta"))
            prefix = list(book.db.execute("SELECT * FROM events"))
            book.close()
            boundary = migrate_ledger(path, old, new, NOW+1)
            self.assertFalse(boundary["accounting_reset"])
            book = Ledger(path, new, PaperBroker.identity)
            for key in before:
                if key != "identity":
                    self.assertEqual(book.get(key), json.loads(before[key]))
            events = list(book.db.execute("SELECT * FROM events"))
            self.assertEqual(events[:len(prefix)], prefix)
            self.assertEqual(events[-1][2], "policy.session_changed")
            book.close()
            with self.assertRaises(ValueError):
                migrate_ledger(path, old, new, NOW+2)  # identity already moved; no double migration


class SessionFeedTests(unittest.TestCase):
    def batch(self, policy_hash, at):
        return {"timestamp": at, "source": "test_fixture", "venue": "test_fixture", "policy_hash": policy_hash,
                "quotes": six_frame(at)["quotes"]}

    def test_quote_guard_accepts_only_the_declared_transition(self):
        old = expanded_policy()
        new = widen(old)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            publish(root, self.batch(old.fingerprint, NOW), SIX)
            with self.assertRaises(ValueError):
                publish(root, self.batch(new.fingerprint, NOW+5), SIX)  # undeclared: still rejected
            stray = replace(new, max_model_calls_per_day=150)
            record_feed_transition(root, old, new, NOW+6)
            record_feed_transition(root, old, new, NOW+6)  # idempotent
            self.assertEqual(len(json.loads((root/"policy-transitions.json").read_text())["transitions"]), 1)
            with self.assertRaises(ValueError):
                publish(root, self.batch(stray.fingerprint, NOW+7), SIX)  # other target: rejected
            publish(root, self.batch(new.fingerprint, NOW+8), SIX)
            self.assertEqual(json.loads((root/"quotes.json").read_text())["policy_hash"], new.fingerprint)
            with self.assertRaises(ValueError):
                publish(root, self.batch(old.fingerprint, NOW+9), SIX)  # no rollback of provenance


class SessionExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"experiment.sqlite"
        from test_trading_experiment import policy as experiment_policy
        self.exp = Experiment(self.path, experiment_policy(), epoch=NOW-120, strategy=SPEC.version)
        self.exp.apply(frame(NOW-120))
        activate(self.exp, NOW-119)
        expand(self.exp)
        self.exp.close()
        self.exp = Experiment(self.path, expanded_policy())
        self.old, self.new = expanded_policy(), widen(expanded_policy())

    def tearDown(self):
        self.exp.close()
        self.temp.cleanup()

    def event(self, new=None, at=NOW-110, from_policy=None):
        new = new or self.new
        return {"type": "session_policy_update", "id": "session:"+new.fingerprint, "observed_at": at,
                "from_policy": from_policy or self.old.fingerprint, "to_policy": new.fingerprint,
                "new_policy": asdict(new)}

    def write_source(self, policy_hash):
        root = Path(self.temp.name)/"source"
        f = six_frame()
        header = {"policy_hash": policy_hash, "source": "alpaca", "venue": "us", "timestamp": NOW}
        rules = {s: {k: f["quotes"][s][k] for k in ("increment", "minimum_quantity", "price_increment")} for s in SIX}
        for path, value in {"market/quotes.json": {**header, "quotes": f["quotes"]},
                            "market/signals.json": {**header, "histories": f["bars"], "instrument_rules": rules},
                            "outbox/snapshot.json": {}}.items():
            write_snapshot(root/path, value)
        return root

    def test_books_move_to_the_shared_session_and_restart_replays(self):
        before = copy.deepcopy(self.exp.state())
        identity = copy.deepcopy(self.exp.identity)
        prefix = list(self.exp.db.execute("SELECT * FROM experiment_events ORDER BY seq"))
        self.exp.apply(self.event())
        after = self.exp.state()
        self.assertEqual(self.exp.identity, identity)
        self.assertEqual(after["universe"]["policy_hash"], self.new.fingerprint)
        for name, book in before["books"].items():
            for k in ("cash", "positions", "pending", "fills", "lots"):
                self.assertEqual(after["books"][name][k], book[k])
        self.assertEqual(after["evidence"]["blocks"], before["evidence"]["blocks"])
        self.assertIn("session_boundary_partial_day", after["evidence"]["invalid_reasons"])
        self.assertEqual(list(self.exp.db.execute("SELECT * FROM experiment_events ORDER BY seq"))[:len(prefix)], prefix)
        self.exp.close()
        with self.assertRaises(ValueError):
            Experiment(self.path, self.old)  # the old policy can no longer open the migrated study
        self.exp = Experiment(self.path, self.new)  # restart with the shared 24/7 policy
        self.assertTrue(self.exp.verify_replay()["verified"])
        captured = capture(self.exp, self.write_source(self.new.fingerprint), NOW)
        self.assertEqual(captured["policy_hash"], self.new.fingerprint)
        with self.assertRaises(ValueError):
            capture(self.exp, self.write_source(self.old.fingerprint), NOW)  # stale-policy source refused

    def test_malformed_boundary_is_refused_by_dry_projection(self):
        for bad in (self.event(new=replace(self.new, max_loss_per_trade=D(14))),
                    self.event(from_policy="0"*64)):
            with self.assertRaises((ValueError, IntegrityError)):
                self.exp._activate_session(copy.deepcopy(self.exp.state()), bad, NOW)
        self.assertFalse(self.exp.state()["halt"])

    def test_runner_command_applies_the_session_boundary(self):
        from evolver.trading.experiment_runner import main
        self.exp.close()
        policies = Path(self.temp.name)
        (policies/"old.json").write_text(json.dumps(asdict(self.old), default=str))
        (policies/"new.json").write_text(json.dumps(asdict(self.new), default=str))
        self.assertEqual(main(["expand-session", "--root", self.temp.name, "--policy", str(policies/"old.json"),
                               "--new-policy", str(policies/"new.json")]), 0)
        self.exp = Experiment(self.path, self.new)
        self.assertEqual(self.exp.state()["universe"]["policy_hash"], self.new.fingerprint)


class SessionNotifierTests(unittest.TestCase):
    def test_same_symbols_policy_change_preserves_cursor(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)/"alerts.sqlite"
            old = {"schema_version": 1, "policy_hash": "a"*64, "broker_identity": "alpaca:demo:"+"b"*16,
                   "recipient_hash": "c"*64, "bot_username": "fixture_bot", "instruments": list(SIX),
                   "acceptance_identity_policy_hash": "e"*64}
            notifier = PaperOrderAlerts(Path(temp)/"source.sqlite", state, old)
            with notifier.db:
                notifier.set("cursor", 31)
            notifier.close()
            new = {**old, "policy_hash": "d"*64}
            migrate_config(state, old, new, NOW)
            notifier = PaperOrderAlerts(Path(temp)/"source.sqlite", state, new)
            self.assertEqual(notifier.get("cursor"), 31)
            notifier.close()
            with self.assertRaises(Exception):
                migrate_config(state, new, {**new, "policy_hash": "f"*64, "recipient_hash": "9"*64}, NOW)


if __name__ == "__main__":
    unittest.main()
