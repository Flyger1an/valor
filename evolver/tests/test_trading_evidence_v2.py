"""Forward evidence tests: no refreshed old prices, censored losses or retroactive reclassification."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from evolver.trading.experiment import Experiment, digest
from evolver.trading.experiment_runner import capture, main
from evolver.trading.shadow_sizing import KELLY_RULES, KELLY_RULES_V2, recommend
from test_trading_experiment import NOW, ROOT, SPEC, SYMBOLS, blocks, frame, histories, policy


START = dt.datetime(2026, 10, 6, tzinfo=dt.timezone.utc).timestamp()


def activate(exp, at):
    return exp.apply({"type": "evidence_policy_update", "id": "evidence-policy:"+KELLY_RULES_V2["version"],
                      "observed_at": at, "from_version": KELLY_RULES["version"],
                      "to_version": KELLY_RULES_V2["version"], "rules_hash": digest(KELLY_RULES_V2)})


class EvidenceV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/"experiment.sqlite"
        self.exp = Experiment(self.path, policy(), epoch=START-5, strategy=SPEC.version)
        self.exp.apply(frame(START-5))
        activate(self.exp, START-4)

    def tearDown(self):
        self.exp.close()
        self.tmp.cleanup()

    def cash_day(self, gap=False):
        for minute in range(1441):
            if gap and minute == 600:
                continue
            at = START+minute*60
            event = frame(at)
            event["quotes"]["ETH-USD"]["timestamp"] = START-5
            self.exp.apply(event)
            if minute == 1439:
                closing = frame(at+55)
                closing["quotes"]["ETH-USD"]["timestamp"] = START-5
                self.exp.apply(closing)
        self.exp.apply(frame(START+86400+5))
        return next(b for b in self.exp.state()["evidence"]["blocks"] if b["day"] == "2026-10-06")

    def test_observed_cash_day_remains_known_without_fabricating_fresh_quotes(self):
        block = self.cash_day()
        self.assertTrue(block["complete"])
        self.assertTrue(block["settled"])
        self.assertEqual(block["pnl"], dict.fromkeys(SYMBOLS, "0"))
        self.assertGreater(block["coverage"]["stale_by_asset"]["ETH-USD"], 1400)
        self.assertEqual(block["coverage"]["held_stale_by_asset"]["ETH-USD"], 0)
        self.assertEqual(block["coverage"]["observations"], 1441)
        self.assertEqual(block["start_valuation"]["held_assets"], {})
        self.assertTrue(block["end_valuation"]["valid"])
        self.assertEqual(self.exp.report()["usable_evidence_blocks"], 1)
        self.assertEqual([b["fills"] for b in self.exp.report()["books"]], [0, 0, 0])
        self.assertTrue(self.exp.verify_replay()["verified"])

    def test_real_observation_outage_still_invalidates_cash_day(self):
        block = self.cash_day(gap=True)
        self.assertFalse(block["complete"])
        self.assertIn("observation_gap", block["invalid_reasons"])
        self.assertEqual(block["coverage"]["gaps_over_60_seconds"], 1)
        self.assertEqual(self.exp.report()["usable_evidence_blocks"], 0)

    def test_stale_quote_still_blocks_every_entry_after_version_boundary(self):
        at = START+13*3600
        event = frame(at, bars=histories(at=at))
        event["quotes"]["ETH-USD"]["timestamp"] = at-31
        self.exp.apply(event)
        for book in self.exp.state()["books"].values():
            self.assertFalse(book["pending"])
            self.assertFalse(book["fills"])
            self.assertEqual(book["skips"].get("stale_quote"), 1)
        self.assertFalse(self.exp.report()["marks_fresh"])

    def test_fresh_receipt_never_refreshes_old_exchange_price(self):
        at = START+13*3600
        quotes = frame(at)["quotes"]
        quotes["ETH-USD"]["timestamp"] = at-240
        files = {"market/quotes.json": {"policy_hash": policy().fingerprint, "source": "test_fixture", "timestamp": at, "quotes": quotes},
                 "market/signals.json": {"policy_hash": policy().fingerprint, "source": "test_fixture", "timestamp": at, "histories": histories(at=at)},
                 "outbox/snapshot.json": {"policy_hash": policy().fingerprint, "timestamp": at,
                     "supervisor": {"action": "resume_entries", "scale": "1", "expires_at": at+60},
                     "news": {"entry_blocked": False, "fetched_at": at}}}
        def read(path, *args, **kwargs):
            return files.get("/".join(Path(path).parts[-2:]), {})
        with patch("evolver.trading.experiment_runner.read_object", side_effect=read):
            event = capture(self.exp, "/source", at)
        self.assertEqual(event["input_provenance"]["market_received_at"], at)
        self.assertEqual(event["quotes"]["ETH-USD"]["timestamp"], at-240)
        report = self.exp.apply(event)
        self.assertFalse(report["marks_fresh"])
        self.assertEqual(report["evidence_quality"]["coverage"]["stale_by_asset"]["ETH-USD"], 1)
        self.assertEqual([b["fills"] for b in report["books"]], [0, 0, 0])

    def test_boundary_preserves_history_identity_cash_and_cli_is_idempotent(self):
        self.exp.apply(frame(START))
        before = self.exp.state()
        identity = copy.deepcopy(self.exp.identity)
        count = self.exp.verify_replay()["events"]
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["upgrade-evidence", "--root", self.tmp.name,
                                   "--policy", str(ROOT/"infra/trading/policy.demo.initial.json")]), 0)
        self.assertEqual(self.exp.state(), before)
        self.assertEqual(self.exp.identity, identity)
        self.assertEqual(self.exp.verify_replay()["events"], count)
        history = self.exp.report()["evidence_policy_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["reclassified_blocks"], 0)
        self.assertFalse(self.exp.state()["evidence"]["blocks"][0]["complete"])
        self.exp.close()
        self.exp = Experiment(self.path, policy())
        self.assertEqual(self.exp.state(), before)

    def test_legacy_daily_blocks_are_retained_without_reclassification(self):
        self.exp.close()
        self.exp = Experiment(Path(self.tmp.name)/"legacy"/"experiment.sqlite", policy(), epoch=START-5, strategy=SPEC.version)
        self.exp.apply(frame(START-5))
        self.exp.apply(frame(START))
        old = copy.deepcopy(self.exp.state()["evidence"]["blocks"])
        identity = digest(self.exp.identity)
        report = activate(self.exp, START+1)
        self.assertEqual(self.exp.state()["evidence"]["blocks"], old)
        self.assertEqual(report["identity_hash"], identity)
        self.assertEqual(report["usable_evidence_blocks"], 0)
        self.assertEqual(report["evidence_quality"]["legacy_blocks_retained"], 1)
        self.assertEqual(report["evidence_quality"]["invalid_reasons"], ["policy_boundary_partial_day"])
        self.assertTrue(self.exp.verify_replay()["verified"])

    def test_upgrade_just_after_midnight_still_excludes_activation_day(self):
        self.exp.close()
        self.exp = Experiment(Path(self.tmp.name)/"midnight"/"experiment.sqlite", policy(), epoch=START-5, strategy=SPEC.version)
        self.exp.apply(frame(START-5))
        activate(self.exp, START+1)
        self.exp.apply(frame(START+5))
        evidence = self.exp.state()["evidence"]
        self.assertEqual(evidence["day"], "2026-10-06")
        self.assertFalse(evidence["complete"])
        self.assertIn("policy_boundary_partial_day", evidence["invalid_reasons"])
        self.assertEqual(self.exp.report()["evidence_quality"]["first_full_utc_day_start"], START+86400)
        self.assertTrue(self.exp.verify_replay()["verified"])

    def test_entirely_missing_calendar_days_remain_visible_not_zero_returns(self):
        self.exp.apply(frame(START))
        self.exp.apply(frame(START+3*86400+10))
        quality = self.exp.report()["evidence_quality"]
        self.assertEqual(quality["expected_completed_full_days"], 3)
        self.assertEqual(quality["unobserved_full_days"], 2)
        self.assertEqual(self.exp.report()["usable_evidence_blocks"], 0)

    def holding_experiment(self):
        self.exp.close()
        other = Path(self.tmp.name)/"held"/"experiment.sqlite"
        self.exp = Experiment(other, policy(), epoch=NOW, strategy=SPEC.version)
        self.exp.apply(frame(NOW, bars=histories()))
        self.exp.apply(frame(NOW+5))
        activate(self.exp, NOW+6)
        self.exp.apply(frame(START-5))

    def test_held_stale_endpoints_are_invalid_and_next_day_price_does_not_repair_them(self):
        self.holding_experiment()
        for minute in range(1441):
            event = frame(START+minute*60)
            for q in event["quotes"].values():
                q["timestamp"] = START-5
            self.exp.apply(event)
            if minute == 1439:
                closing = frame(START+minute*60+55)
                for q in closing["quotes"].values():
                    q["timestamp"] = START-5
                self.exp.apply(closing)
        block = next(b for b in self.exp.state()["evidence"]["blocks"] if b["day"] == "2026-10-06")
        self.assertFalse(block["complete"])
        self.assertIn("unpriced_held_inventory_at_end", block["invalid_reasons"])
        self.assertFalse(block["end_valuation"]["valid"])
        old_block = copy.deepcopy(block)
        self.exp.apply(frame(START+86400+5, price="50"))
        self.assertEqual(next(b for b in self.exp.state()["evidence"]["blocks"] if b["day"] == "2026-10-06"), old_block)
        self.assertIn("unpriced_held_inventory_at_start", self.exp.state()["evidence"]["invalid_reasons"])

    def test_intraday_staleness_does_not_censor_a_later_realized_loss(self):
        self.holding_experiment()
        for minute in range(1441):
            at = START+minute*60
            event = frame(at, price="100" if minute<120 else "95")
            if minute<120:
                for q in event["quotes"].values():
                    q["timestamp"] = START-5
            self.exp.apply(event)
            if minute == 120:
                self.exp.apply(frame(at+5, price="95"))
            if minute == 1439:
                self.exp.apply(frame(at+55, price="95"))
        block = next(b for b in self.exp.state()["evidence"]["blocks"] if b["day"] == "2026-10-06")
        self.assertTrue(block["complete"])
        self.assertTrue(block["settled"])
        self.assertLess(float(block["pnl"]["BTC-USD"]), 0)
        self.assertGreater(block["coverage"]["held_stale_by_asset"]["BTC-USD"], 100)
        self.assertFalse(self.exp.state()["books"]["baseline"]["positions"])
        self.assertTrue(self.exp.verify_replay()["verified"])

    def test_pending_order_cannot_fill_on_stale_portfolio_mark(self):
        at = START+13*3600
        event = frame(at+5)
        # Retain chronological timestamps: make the earlier ETH quote already 29 seconds old.
        # A fresh quote can expire between order creation and the later fill observation.
        self.exp.close()
        path = Path(self.tmp.name)/"pending"/"experiment.sqlite"
        self.exp = Experiment(path, policy(), epoch=at, strategy=SPEC.version)
        initial = frame(at, bars=histories(at=at))
        initial["quotes"]["ETH-USD"]["timestamp"] = at-29
        self.exp.apply(initial)
        activate(self.exp, at+1)
        event["quotes"]["ETH-USD"]["timestamp"] = at-29
        self.exp.apply(event)
        for name in ("baseline", "henry"):
            b = self.exp.state()["books"][name]
            self.assertFalse(b["fills"])
            self.assertIn("stale_portfolio_mark_before_fill", b["skips"])


class EvidenceCohortTests(unittest.TestCase):
    def test_v2_never_admits_v1_or_future_or_unsettled_days(self):
        legacy = blocks(NOW)
        self.assertEqual(recommend(legacy, SYMBOLS, SYMBOLS, {}, NOW, KELLY_RULES_V2["version"])["blocks"], 0)
        rows = copy.deepcopy(legacy)
        for b in rows:
            b["evidence_version"] = KELLY_RULES_V2["version"]
        rows[0]["available_at"] = NOW+1
        rows[1]["settled"] = False
        result = recommend(rows, SYMBOLS, SYMBOLS, {}, NOW, KELLY_RULES_V2["version"])
        self.assertEqual(result["blocks"], 28)
        self.assertEqual(result["reason"], "insufficient_forward_evidence")

    def test_numerical_estimator_and_risk_assumptions_are_unchanged(self):
        rows = blocks(NOW)
        before = recommend(rows, SYMBOLS, SYMBOLS, {}, NOW)
        for b in rows:
            b["evidence_version"] = KELLY_RULES_V2["version"]
        after = recommend(rows, SYMBOLS, SYMBOLS, {}, NOW, KELLY_RULES_V2["version"])
        for key in ("raw_fraction", "fractional_fraction", "adjusted_mean", "raw_incremental_log_growth", "reason"):
            self.assertEqual(before[key], after[key])

    def test_thirty_observed_cash_days_do_not_establish_an_edge(self):
        rows = blocks(NOW, returns=("0", "0"))
        for b in rows:
            b.update(evidence_version=KELLY_RULES_V2["version"], active=dict.fromkeys(SYMBOLS, False))
        result = recommend(rows, SYMBOLS, SYMBOLS, {}, NOW, KELLY_RULES_V2["version"])
        self.assertEqual(result["blocks"], 30)
        self.assertEqual(result["reason"], "no_supported_positive_net_edge")
        self.assertEqual(result["fractional_fraction"], dict.fromkeys(SYMBOLS, 0))


if __name__ == "__main__":
    unittest.main()
