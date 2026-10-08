"""The supervisor must not deadlock paper entries by pausing whenever no candidate exists right now."""
import json
import unittest

from test_trading_runtime import NOW, policy
from evolver.trading.agent_worker import SUPERVISOR_PAPER, process_request
from evolver.trading.agents import SYSTEM
from evolver.trading.contracts import encode


class SupervisorPromptTests(unittest.TestCase):
    def run_supervision(self, p, action="resume_entries"):
        seen = {}
        def model(system, prompt):
            seen["system"] = system
            return encode({"action": action, "risk_scale": "1", "reason": "fixture"})
        body = {"policy_hash": p.fingerprint, "snapshot": {"supervisor": {"scale": "1"}}}
        command = process_request({"kind": "supervision", "body": body}, p, model, model, NOW)
        return command, seen["system"]

    def test_shared_system_prompt_no_longer_orders_a_blanket_hold(self):
        self.assertNotIn("Hold when no setup exists", SYSTEM)
        self.assertIn("exact-trade\nreview, reject when no setup exists", SYSTEM)

    def test_paper_supervisor_is_told_a_lease_spans_future_signals(self):
        p = policy()
        self.assertNotEqual(p.mode, "live")
        command, system = self.run_supervision(p)
        self.assertIn(SUPERVISOR_PAPER, system)
        self.assertIn("no current candidate is not a reason to pause", system)
        self.assertEqual(command["action"], "resume_entries")
        self.assertEqual(command["expires_at"], NOW + p.supervisor_ttl_seconds)

    def test_pause_remains_available_and_scale_is_still_bounded(self):
        command, _ = self.run_supervision(policy(), action="pause_entries")
        self.assertEqual(command["action"], "pause_entries")


if __name__ == "__main__":
    unittest.main()
