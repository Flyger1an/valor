import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from evolver.trading.contracts import Policy, decimal as D
from evolver.trading.experiment import Experiment, IntegrityError, metrics, RULES
from evolver.trading.experiment_runner import capture, main
from evolver.trading.news import evidence_hash
from evolver.trading.shadow_sizing import recommend
from evolver.trading.strategies import CATALOG


ROOT = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 10, 5, 13, 5, tzinfo=dt.timezone.utc).timestamp()
SPEC = next(s for s in CATALOG if s.family == "breakout")
SYMBOLS = ["BTC-USD", "ETH-USD"]


def policy():
    values = json.loads((ROOT/"infra/trading/policy.demo.initial.json").read_text())
    values["allowed_instruments"] = SYMBOLS  # Immutable legacy replay fixtures.
    return Policy.from_dict(values)


def histories(symbols=("BTC-USD",), at=NOW):
    latest = int(at//300)*300-300
    result = {}
    for symbol in SYMBOLS:
        bars = [{"timestamp": latest-(99-i)*300, "open": "100", "low": "100", "high": "100",
                 "close": "100", "volume": "100000"} for i in range(100)]
        if symbol in symbols:
            bars[-1].update(high="101", close="101")
        result[symbol] = bars
    return result


def frame(at=NOW, *, bars=None, price="100", capacity="1000", event_id=None, context=None):
    return {"type": "frame", "id": event_id or f"frame:{at}", "observed_at": at,
            "policy_hash": policy().fingerprint, "strategy": SPEC.version, "source": "test_fixture",
            "quotes": {s: {"bid": price, "ask": str(D(price)*D("1.0005")), "timestamp": at,
                           "buy_capacity": capacity, "sell_capacity": capacity, "increment": "0.000001"} for s in SYMBOLS},
            "bars": bars or {}, "liquidity_basis": "explicit independent fixture capacity",
            "context": context or {"data_entry_allowed": True, "supervisor_entry_allowed": True, "supervisor_scale": "1"},
            "shared_operating_estimate": "10"}


def blocks(now, count=30, returns=("0.012", "0.011")):
    return [{"day": str(i), "start": now-(count-i+1)*86400, "end": now-(count-i)*86400,
             "available_at": now-(count-i)*86400+60, "complete": True, "settled": True,
             "reference_notional": "25", "pnl": {s: str(D(r)*25) for s, r in zip(SYMBOLS, returns)},
             "active": dict.fromkeys(SYMBOLS, True)} for i in range(count)]


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/"experiment.sqlite"
        self.exp = Experiment(self.path, policy(), epoch=NOW, strategy=SPEC.version)

    def tearDown(self):
        self.exp.close()
        self.tmp.cleanup()

    def enter(self, **kwargs):
        self.exp.apply(frame(bars=histories(), **kwargs))
        self.exp.apply(frame(NOW+5, **kwargs))

    def book(self, name):
        return self.exp.state()["books"][name]

    def test_common_epoch_separate_500_books_and_cold_kelly_abstention(self):
        report = self.exp.apply(frame(bars=histories()))
        self.assertEqual({b["equity"] for b in report["books"]}, {"500"})
        self.assertEqual(report["epoch"], NOW)
        self.assertEqual(len(self.book("baseline")["pending"]), 1)
        self.assertEqual(len(self.book("henry")["pending"]), 1)
        self.assertEqual(self.book("kelly")["pending"], {})
        self.assertIn("insufficient_forward_evidence", self.book("kelly")["skips"])
        self.assertEqual(report["incremental_model_api_calls"], 0)
        self.assertIn("not an operational", report["approval_basis"])

    def test_order_waits_for_later_quote_and_cash_stays_nonnegative(self):
        self.exp.apply(frame(bars=histories()))
        self.assertEqual(self.book("henry")["fills"], {})
        repeated_quote = frame(NOW+1)
        for q in repeated_quote["quotes"].values():
            q["timestamp"] = NOW
        self.exp.apply(repeated_quote)
        self.assertEqual(self.book("henry")["fills"], {})
        self.exp.apply(frame(NOW+5))
        h = self.book("henry")
        self.assertGreater(D(next(iter(h["fills"].values()))["value"]), 490)
        self.assertGreaterEqual(D(h["cash"]), 0)
        self.assertLessEqual(D(next(iter(self.book("baseline")["fills"].values()))["value"]), 25)
        for b in self.exp.report()["books"]:
            self.assertAlmostEqual(float(b["equity"]), 500+float(b["realized_pnl"])+float(b["unrealized_pnl"]), places=8)

    def test_simultaneous_signals_select_btc_for_henry_and_record_eth_skip(self):
        self.exp.apply(frame(bars=histories(SYMBOLS)))
        self.assertEqual(list(self.book("henry")["pending"]), ["BTC-USD"])
        self.assertEqual(self.book("henry")["skips"]["henry_one_concentrated_position"], 1)
        self.assertEqual(self.exp.state()["opportunities"][1]["decisions"]["henry"], "henry_one_concentrated_position")

    def test_partial_fill_does_not_fill_remainder_or_reuse_signal(self):
        self.enter(capacity="0.1")
        self.assertEqual(D(next(iter(self.book("henry")["fills"].values()))["quantity"]), D(".1"))
        self.assertTrue(next(iter(self.book("henry")["fills"].values()))["partial"])
        self.exp.apply(frame(NOW+10))
        self.assertEqual(len(self.book("henry")["fills"]), 1)

    def test_unfilled_limit_order_is_observation_not_profit(self):
        self.exp.apply(frame(bars=histories()))
        self.exp.apply(frame(NOW+5, price="101"))
        for name in ("baseline", "henry"):
            self.assertEqual(self.book(name)["fills"], {})
            self.assertEqual(self.book(name)["cash"], "500")
            self.assertIn("unfilled_price_or_liquidity", self.book(name)["skips"])

    def test_gap_exit_uses_later_adverse_quote_not_stop_price(self):
        self.enter()
        self.exp.apply(frame(NOW+10, price="97"))
        self.exp.apply(frame(NOW+15, price="96"))
        sells = [f for f in self.book("baseline")["fills"].values() if f["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(D(sells[0]["price"]), D("95.952"))
        self.assertLess(D(self.exp.report()["books"][0]["realized_pnl"]), D("-.9"))

    def test_ordinary_target_exit_is_shared_without_borrowing_ai_approval(self):
        self.enter()
        self.exp.apply(frame(NOW+10, price="103"))
        self.exp.apply(frame(NOW+15, price="103"))
        for name in ("baseline", "henry"):
            self.assertEqual(self.book(name)["positions"], {})
            self.assertEqual([f["reason"] for f in self.book(name)["fills"].values() if f["side"] == "sell"], ["strategy_exit"])

    def test_fee_correction_after_close_is_isolated_and_conserves_equity(self):
        self.enter()
        self.exp.apply(frame(NOW+10, price="103"))
        self.exp.apply(frame(NOW+15, price="103"))
        before = self.exp.report()
        fid, fill = next((i, f) for i, f in self.book("baseline")["fills"].items() if f["side"] == "buy")
        old_fee = D(fill["fee"])
        self.exp.apply({"id": "late-fee", "type": "fee_settlement", "observed_at": NOW+20,
                        "book": "baseline", "fill_id": fid, "total_fee": ".20"})
        after = self.exp.report()
        self.assertEqual(after["books"][1:], before["books"][1:])
        self.assertEqual(D(after["books"][0]["realized_pnl"])-D(before["books"][0]["realized_pnl"]), old_fee-D(".20"))
        self.assertEqual(D(after["books"][0]["equity"])-D(before["books"][0]["equity"]), old_fee-D(".20"))
        self.assertTrue(self.exp.verify_replay()["verified"])

    def test_fee_reserve_prevents_henry_from_spending_fee_money(self):
        self.enter()
        fid, fill = next(iter(self.book("henry")["fills"].items()))
        self.exp.apply({"id": "henry-fee", "type": "fee_settlement", "observed_at": NOW+10,
                        "book": "henry", "fill_id": fid, "total_fee": fill["reserve"]})
        self.assertGreaterEqual(D(self.book("henry")["cash"]), 0)
        self.assertEqual(self.book("kelly")["cash"], "500")

    def test_duplicate_events_restart_and_replay_do_not_refill_cash(self):
        event = frame(bars=histories())
        self.exp.apply(event)
        self.exp.apply(frame(NOW+5))
        before = self.exp.report()
        self.exp.close()
        self.exp = Experiment(self.path, policy())
        self.assertEqual(self.exp.apply(event), before)
        self.assertEqual(self.exp.verify_replay()["events"], 2)
        self.assertEqual(self.exp.report(), before)

    def test_completed_service_restart_idles_without_new_inputs_or_reset(self):
        self.exp.apply(frame(NOW+90*86400+1))
        before = self.exp.report()
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        with patch("evolver.trading.experiment_runner.threading.Event", return_value=stop), \
                patch("evolver.trading.experiment_runner.signal.signal"), \
                patch("evolver.trading.experiment_runner.capture") as read, redirect_stdout(StringIO()):
            self.assertEqual(main(["run", "--root", self.tmp.name, "--source-root", self.tmp.name+"-source",
                "--policy", str(ROOT/"infra/trading/policy.demo.initial.json"), "--stay-running-after-completion"]), 0)
        read.assert_not_called()
        stop.wait.assert_called_once_with(30)
        self.assertEqual(self.exp.report(), before)
        self.assertEqual(self.exp.verify_replay()["events"], 1)

    def test_conflicting_duplicate_is_durable_terminal_integrity_failure(self):
        event = frame(bars=histories())
        self.exp.apply(event)
        event["context"]["supervisor_scale"] = ".5"
        with self.assertRaises(IntegrityError):
            self.exp.apply(event)
        self.assertEqual(self.exp.state()["halt"], "conflicting_duplicate_event")
        self.assertTrue(self.exp.verify_replay()["verified"])
        with self.assertRaises(IntegrityError):
            self.exp.apply(frame(NOW+10))

    def test_future_bars_and_chronological_rewinds_stop_without_account_mutation(self):
        bad = frame(bars=histories(at=NOW+300))
        with self.assertRaises(IntegrityError):
            self.exp.apply(bad)
        self.assertEqual(self.book("henry")["cash"], "500")
        self.assertTrue(self.exp.verify_replay()["verified"])

    def test_source_bar_revision_is_detected(self):
        self.exp.apply(frame(bars=histories()))
        altered = histories()
        altered["BTC-USD"][-1]["volume"] = "99999"
        with self.assertRaisesRegex(IntegrityError, "closed_bar_revision"):
            self.exp.apply(frame(NOW+1, bars=altered))

    def test_no_refill_or_policy_change_or_live_policy(self):
        with self.assertRaises(ValueError):
            Experiment(self.path, policy(), epoch=NOW+1)
        value = json.loads((ROOT/"infra/trading/policy.demo.initial.json").read_text())
        value["mode"] = "live"
        with self.assertRaisesRegex(ValueError, "paper/demo"):
            Experiment(Path(self.tmp.name)/"other"/"experiment.sqlite", Policy.from_dict(value), epoch=NOW)
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            main(["init", "--root", self.tmp.name, "--policy", str(ROOT/"infra/trading/policy.demo.initial.json")])

    def test_below_minimum_is_skipped_not_rounded_up(self):
        context = {"data_entry_allowed": True, "supervisor_entry_allowed": True, "supervisor_scale": ".2"}
        self.exp.apply(frame(bars=histories(), context=context))
        self.assertEqual(self.book("baseline")["pending"], {})
        self.assertIn("below_minimum_no_round_up", self.book("baseline")["skips"])
        self.assertEqual(len(self.book("henry")["pending"]), 1)

    def test_caps_rechecked_after_supervisor_reduction(self):
        self.exp.apply(frame(bars=histories()))
        event = frame(NOW+5)
        event["context"]["supervisor_scale"] = ".5"
        self.exp.apply(event)
        self.assertEqual(self.book("baseline")["fills"], {})
        self.assertIn("exposure_changed_before_fill", self.book("baseline")["skips"])

    def test_henry_has_no_twenty_percent_halt_and_can_lose_whole_fictional_bankroll(self):
        self.enter()
        self.exp.apply(frame(NOW+10, price="1"))
        self.assertEqual(self.book("henry")["halt"], "")
        self.assertFalse(self.book("henry")["daily_halt"])
        self.exp.apply(frame(NOW+15, price=".5"))
        self.assertEqual(self.book("henry")["halt"], "bankroll_no_longer_executable")
        self.assertLess(D(self.exp.report()["books"][2]["equity"]), 10)
        self.assertGreaterEqual(D(self.book("henry")["cash"]), 0)
        self.assertEqual(self.book("kelly")["cash"], "500")
        self.exp.apply(frame(NOW+20, price="100", bars=histories(at=NOW)))
        self.assertEqual(self.book("henry")["positions"], {})

    def test_outage_and_stale_marks_never_invent_fill_or_usable_evidence(self):
        self.exp.apply(frame(bars=histories()))
        event = frame(NOW+100)
        for q in event["quotes"].values():
            q["timestamp"] = NOW
        report = self.exp.apply(event)
        self.assertFalse(report["marks_fresh"])
        self.assertEqual(self.book("henry")["fills"], {})
        self.assertFalse(self.exp.state()["evidence"]["complete"])

    def test_operating_costs_are_shared_once_not_deducted_three_times(self):
        self.exp.apply(frame())
        event = frame(NOW+5)
        event["shared_operating_estimate"] = "10.30"
        report = self.exp.apply(event)
        self.assertEqual(D(report["shared_operating_estimate"]), D(".30"))
        self.assertEqual([D(b["after_allocated_operating_estimate"]) for b in report["books"]], [D("-.10")]*3)
        self.assertEqual([b["cash"] for b in report["books"]], ["500"]*3)
        self.assertIsNone(report["actual_shared_operating_cost"])

    def test_data_gate_and_supervision_are_distinct_hypothetical_policies(self):
        event = frame(bars=histories())
        event["context"]["supervisor_entry_allowed"] = False
        self.exp.apply(event)
        self.assertEqual(self.book("baseline")["pending"], {})
        self.assertEqual(len(self.book("henry")["pending"]), 1)

    def test_final_fee_conflict_stops_without_accepting_favorable_rewrite(self):
        self.enter()
        fid, fill = next(iter(self.book("henry")["fills"].items()))
        event = {"id": "settled", "type": "fee_settlement", "observed_at": NOW+10,
                 "book": "henry", "fill_id": fid, "total_fee": fill["fee"]}
        self.exp.apply(event)
        original_cash = self.book("henry")["cash"]
        event.update(id="rewrite", observed_at=NOW+15, total_fee="0")
        with self.assertRaises(IntegrityError):
            self.exp.apply(event)
        self.assertEqual(self.book("henry")["cash"], original_cash)

    def test_chronological_rewind_is_terminal_and_preserves_balances(self):
        self.enter()
        cash = self.book("henry")["cash"]
        with self.assertRaisesRegex(IntegrityError, "chronological_boundary"):
            self.exp.apply(frame(NOW+2))
        self.assertEqual(self.book("henry")["cash"], cash)
        self.assertTrue(self.exp.verify_replay()["verified"])

    def test_identical_quote_timestamp_cannot_change_price(self):
        self.exp.apply(frame())
        event = frame(NOW+1, price="110")
        for q in event["quotes"].values():
            q["timestamp"] = NOW
        with self.assertRaisesRegex(IntegrityError, "quote_revision"):
            self.exp.apply(event)

    def test_daily_settlement_is_modeled_once_and_replayable(self):
        self.enter()
        next_day = dt.datetime(2026, 10, 6, 0, 0, tzinfo=dt.timezone.utc).timestamp()
        self.exp.apply(frame(next_day))
        fill = next(iter(self.book("baseline")["fills"].values()))
        self.assertTrue(fill["settled"])
        self.assertEqual(fill["settlement_basis"], "modeled_not_broker_actual")
        self.assertEqual(self.exp.report()["usable_evidence_blocks"], 0)  # partial day + long gap
        self.assertTrue(self.exp.verify_replay()["verified"])

    def test_partial_exit_fee_revision_splits_realized_and_unrealized(self):
        self.enter()
        self.exp.apply(frame(NOW+10, price="103"))
        self.exp.apply(frame(NOW+15, price="103", capacity=".1"))
        before = metrics(self.book("baseline"), self.exp.state()["quotes"])
        fid, buy = next((i, f) for i, f in self.book("baseline")["fills"].items() if f["side"] == "buy")
        change = D(buy["fee"])-D(".20")
        self.exp.apply({"type": "fee_settlement", "id": "partial-entry-fee", "observed_at": NOW+20,
                        "book": "baseline", "fill_id": fid, "total_fee": ".20"})
        after = metrics(self.book("baseline"), self.exp.state()["quotes"])
        self.assertAlmostEqual(float(after["realized_pnl"]-before["realized_pnl"]), float(change*D(".1")/D(buy["quantity"])), places=10)
        self.assertEqual(after["equity"]-before["equity"], change)

    def test_liquidity_is_not_reused_on_every_quote_in_same_volume_bucket(self):
        initial = frame(bars=histories())
        self.exp.apply(initial)
        event = frame(NOW+5, capacity=".1")
        for q in event["quotes"].values():
            q["capacity_bucket"] = "same-five-minute-volume"
        self.exp.apply(event)
        self.exp.apply(frame(NOW+10, price="103"))
        event = frame(NOW+15, price="103", capacity=".1")
        for q in event["quotes"].values():
            q["capacity_bucket"] = "same-five-minute-volume"
        self.exp.apply(event)
        self.assertEqual(len(self.book("henry")["fills"]), 1)

    def test_kelly_synthetic_sizing_fixture_is_clipped_by_baseline_caps(self):
        state = self.exp.initial_state()
        state["quotes"] = frame()["quotes"]
        state["evidence"]["blocks"] = blocks(NOW)
        op = {"id": "synthetic-sizing-only", "instrument": "BTC-USD"}
        result = self.exp._entry(state["books"]["kelly"], op, [op], frame(), state, NOW)
        self.assertEqual(result, "queued_virtual_order")
        sizing = state["books"]["kelly"]["sizing"]["BTC-USD"]
        self.assertGreater(D(sizing["fractional_notional"]), 25)
        self.assertLessEqual(D(sizing["capped_notional"]), 25)
        self.assertEqual(self.exp.state()["books"]["kelly"]["cash"], "500")  # fixture never enters journal

    def test_smaller_loss_ceiling_reduces_baseline_to_nonexecutable_size(self):
        value = json.loads((ROOT/"infra/trading/policy.demo.initial.json").read_text())
        value["max_loss_per_trade"] = ".1"
        other = Experiment(Path(self.tmp.name)/"small-loss"/"experiment.sqlite", Policy.from_dict(value), epoch=NOW, strategy=SPEC.version)
        try:
            event = frame(bars=histories())
            event["policy_hash"] = other.policy.fingerprint
            other.apply(event)
            self.assertEqual(other.state()["books"]["baseline"]["pending"], {})
            self.assertIn("below_minimum_no_round_up", other.state()["books"]["baseline"]["skips"])
        finally:
            other.close()


class KellyTests(unittest.TestCase):
    def test_unsettled_future_negative_and_small_samples_abstain(self):
        for rows in ([], blocks(NOW, 29), blocks(NOW, returns=("-.001", "-.002"))):
            self.assertTrue(all(v == 0 for v in recommend(rows, SYMBOLS, SYMBOLS, {}, NOW)["fractional_fraction"].values()))
        rows = blocks(NOW)
        rows[0]["settled"] = False
        rows[1]["available_at"] = NOW+1
        self.assertEqual(recommend(rows, SYMBOLS, SYMBOLS, {}, NOW)["blocks"], 28)

    def test_supported_synthetic_edge_gets_fractional_auditable_allocation(self):
        r = recommend(blocks(NOW), SYMBOLS, SYMBOLS, {}, NOW)
        self.assertEqual(r["reason"], "positive_stress_adjusted_growth")
        self.assertAlmostEqual(sum(r["fractional_fraction"].values()), .25)
        self.assertEqual(len(r["evidence_hash"]), 64)
        self.assertLess(r["evidence_cutoff"], NOW)

    def test_existing_btc_holding_is_fixed_not_independently_rebet(self):
        r = recommend(blocks(NOW), SYMBOLS, SYMBOLS, {"BTC-USD": .05}, NOW)
        self.assertEqual(r["raw_fraction"]["BTC-USD"], 0)
        self.assertLessEqual(sum(r["raw_fraction"].values())+.05, 1)

    def test_correlated_bad_blocks_reduce_or_remove_allocation(self):
        good = recommend(blocks(NOW), SYMBOLS, SYMBOLS, {}, NOW)
        rows = blocks(NOW)
        for row in rows[:8]:
            row["pnl"] = dict.fromkeys(SYMBOLS, "-1.5")
        bad = recommend(rows, SYMBOLS, SYMBOLS, {}, NOW)
        self.assertLess(sum(bad["fractional_fraction"].values()), sum(good["fractional_fraction"].values()))


class CaptureTests(unittest.TestCase):
    def test_raw_news_integrity_gate_applies_to_henry_without_supervision(self):
        p = policy()
        good_news = {"schema_version": 1, "timestamp": NOW, "items": [], "evidence_hash": evidence_hash([]),
                     "sources": {s: {"ok": True, "checked_at": NOW} for s in ("alpaca_crypto", "federal_reserve")}}
        for case in ("current", "bad_hash", "stale", "future", "failed_crypto"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                news = copy.deepcopy(good_news)
                if case == "bad_hash":
                    news["evidence_hash"] = "tampered"
                elif case == "stale":
                    news["timestamp"] = NOW-601
                elif case == "future":
                    news["timestamp"] = NOW+1
                elif case == "failed_crypto":
                    news["sources"]["alpaca_crypto"]["ok"] = False
                files = {"market/quotes.json": {"policy_hash": p.fingerprint, "source": "alpaca", "timestamp": NOW, "quotes": frame()["quotes"]},
                         "market/signals.json": {"policy_hash": p.fingerprint, "source": "alpaca", "timestamp": NOW, "histories": histories()},
                         "outbox/snapshot.json": {"policy_hash": p.fingerprint, "timestamp": NOW-61 if case == "current" else NOW,
                             "supervisor": {"action": "pause_entries", "scale": "1", "expires_at": 0},
                             "news": {"entry_blocked": False, "fetched_at": NOW}},
                         "news/snapshot.json": news}
                exp = Experiment(Path(tmp)/"experiment.sqlite", p, epoch=NOW, strategy=SPEC.version)
                try:
                    def read(path, *args, **kwargs):
                        return files.get("/".join(Path(path).parts[-2:]), {})
                    with patch("evolver.trading.experiment_runner.read_object", side_effect=read):
                        event = capture(exp, "/source", NOW)
                    self.assertEqual(event["context"]["data_entry_allowed"], case == "current")
                    self.assertFalse(event["context"]["supervisor_entry_allowed"])
                    exp.apply(event)
                    self.assertEqual(bool(exp.state()["books"]["henry"]["pending"]), case == "current")
                    self.assertEqual(exp.state()["books"]["baseline"]["pending"], {})
                    self.assertEqual(exp.state()["books"]["kelly"]["pending"], {})
                finally:
                    exp.close()

    def test_capture_is_read_only_uses_prior_volume_and_never_reuses_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/"source"
            for name in ("market", "outbox", "news"):
                (source/name).mkdir(parents=True)
            p = policy()
            quotes = frame()["quotes"]
            files = {"market/quotes.json": {"policy_hash": p.fingerprint, "source": "alpaca", "timestamp": NOW, "quotes": quotes},
                     "market/signals.json": {"policy_hash": p.fingerprint, "source": "alpaca", "timestamp": NOW, "histories": histories()},
                     "outbox/snapshot.json": {"policy_hash": p.fingerprint, "timestamp": NOW, "equity": "999999",
                         "supervisor": {"action": "resume_entries", "scale": "1", "expires_at": NOW+60},
                         "news": {"entry_blocked": False, "fetched_at": NOW, "evidence_hash": "test"}}}
            for name, value in files.items():
                (source/name).write_text(json.dumps(value))
            original = {name: (source/name).read_bytes() for name in files}
            exp = Experiment(Path(tmp)/"experiment"/"experiment.sqlite", p, epoch=NOW, strategy=SPEC.version)
            try:
                event = capture(exp, source, NOW)
                self.assertFalse(event["context"]["operational_approval_reused"])
                self.assertEqual(D(event["quotes"]["BTC-USD"]["buy_capacity"]), 1000)
                exp.apply(event)
                self.assertEqual(exp.report()["books"][0]["equity"], "500")
                self.assertEqual(original, {name: (source/name).read_bytes() for name in files})
            finally:
                exp.close()


if __name__ == "__main__":
    unittest.main()
