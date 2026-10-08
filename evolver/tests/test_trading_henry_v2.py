import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

from evolver.trading import henry_v2_runner
from evolver.trading.henry_v2 import HENRY_V2_RULES, HenryV2, IntegrityError
from evolver.trading.experiment import RULES as THREE_BOOK_RULES, BOOKS

EPOCH = 1_800_000_000.0  # divisible by 300


def bar(t, close, volume="1000000"):
    c = D(str(close))
    return {"timestamp": t, "open": str(c), "high": str(c*D("1.0005")), "low": str(c*D("0.9995")),
            "close": str(c), "volume": volume}


class Market:
    """Drives 5-minute closed bars plus a fresh quote each frame."""

    def __init__(self, book, symbols=("BTC-USD", "ETH-USD")):
        self.book, self.symbols, self.t, self.n = book, symbols, EPOCH, 0
        self.prices = {s: 100.0 for s in symbols}

    def step(self, moves, *, spread="0.0002"):
        self.t += 300
        for s, m in moves.items():
            self.prices[s] *= m
        now = self.t+300+5  # bar at self.t is closed
        bars = {s: [bar(self.t, round(self.prices[s], 6))] for s in self.symbols}
        quotes = {}
        for s in self.symbols:
            p = D(str(round(self.prices[s], 6)))
            quotes[s] = {"bid": str(p), "ask": str(p*(1+D(spread))), "timestamp": now-1,
                         "capacity": "1000000", "capacity_bucket": self.t, "increment": "0.0001"}
        self.n += 1
        return self.book.apply({"type": "frame", "id": f"f{self.n}", "observed_at": now, "quotes": quotes, "bars": bars})

    def follow(self, moves):
        """One more quote on the same bar so pending orders fill on the next observed quote."""
        now = self.t+300+15
        quotes = {s: {"bid": str(D(str(round(self.prices[s], 6)))), "ask": str(D(str(round(self.prices[s], 6)))*D("1.0002")),
                      "timestamp": now-1, "capacity": "1000000", "capacity_bucket": self.t, "increment": "0.0001"}
                  for s in self.symbols}
        self.n += 1
        return self.book.apply({"type": "frame", "id": f"f{self.n}", "observed_at": now, "quotes": quotes, "bars": {}})


class HenryV2Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name)/"henry_v2.sqlite"
        self.book = HenryV2(self.path, symbols=["BTC-USD", "ETH-USD"], fee_bps="25", slippage_bps="5", epoch=EPOCH)
        self.m = Market(self.book)

    def tearDown(self):
        self.book.close()
        self.dir.cleanup()

    def warm(self, n=300):
        for i in range(n):
            self.m.step({"BTC-USD": 1.0 + (0.0003 if i % 2 else -0.0003), "ETH-USD": 1.0})

    def rally(self, until):
        r = None
        for _ in range(until):
            r = self.m.step({"BTC-USD": 1.004, "ETH-USD": 1.0})
            self.m.follow({})
            r = self.book.report()
        return r

    def test_three_book_study_is_untouched(self):
        self.assertEqual(BOOKS, ("baseline", "kelly", "henry"))
        self.assertNotIn("henry_v2", json.dumps(THREE_BOOK_RULES))

    def test_rules_are_unbounded_but_never_leveraged(self):
        for gate in ("supervisor", "news", "session_hours", "daily_loss_limit", "fixed_target", "max_hold"):
            self.assertIn(gate, HENRY_V2_RULES["gates_removed"])
        self.assertEqual(HENRY_V2_RULES["leverage"], "none")

    def test_probes_presses_and_lets_the_winner_run(self):
        self.warm()
        r = self.rally(1)
        self.assertIsNotNone(r["position"], "breakout should open a probe")
        probe_cash = D(r["cash_usd"])
        self.assertGreater(probe_cash, D("230"), "probe uses about half the bankroll")
        r = self.rally(40)
        self.assertTrue(r["position"] and r["position"]["pressed"], "winner should be pressed with the rest of the cash")
        self.assertLess(D(r["cash_usd"]), D("10"))
        # Held well past the v1 2% target and 12h clock would allow? It's still in at +40 bars of rally.
        self.assertGreater(D(r["equity_usd"]), D("550"))
        self.assertEqual(r["closed_trades"], 0, "no fixed take-profit cut the run short")
        # Reversal: trailing stop locks the gain.
        for _ in range(30):
            self.m.step({"BTC-USD": 0.99, "ETH-USD": 1.0})
            self.m.follow({})
        r = self.book.report()
        self.assertGreaterEqual(r["closed_trades"], 1)
        runner = self.book.state()["trades"][0]
        self.assertTrue(runner["pressed"])
        self.assertIn(runner["reason"], {"trailing_stop", "trend_break"})
        self.assertGreater(D(runner["pnl_usd"]), D("40"))
        self.assertGreater(runner["hold_minutes"], 720, "held past the v1 12-hour clock")
        # Dip buys during the selloff exit on their own stop, never instantly on trend break.
        self.assertNotIn("trend_break", {t["reason"] for t in self.book.state()["trades"] if t["strategy"].startswith("mean_reversion")})

    def test_floor_halts_and_liquidates(self):
        self.warm()
        self.rally(1)
        # Crash repeatedly through re-entries until the floor is breached.
        for i in range(400):
            self.m.step({"BTC-USD": 0.97 if i % 3 else 1.03, "ETH-USD": 1.0})
            self.m.follow({})
            if self.book.report()["floor_breached"]:
                break
        for _ in range(5):
            self.m.step({"BTC-USD": 1.0, "ETH-USD": 1.0})
            self.m.follow({})
        r = self.book.report()
        if r["floor_breached"]:
            self.assertIsNone(r["position"])
            self.assertIn(r["halt"], {"equity_floor_breached", "bankroll_no_longer_executable"})
        self.assertGreaterEqual(D(r["cash_usd"]), 0)

    def test_replay_matches_and_journal_is_immutable(self):
        self.warm(250)
        self.rally(5)
        self.assertEqual(self.book.verify_replay()["replay"], "ok")
        self.book.close()
        reopened = HenryV2(self.path)
        self.assertEqual(reopened.identity["rules"], HENRY_V2_RULES)
        self.book = reopened

    def test_exit_fills_whole_position_despite_thin_volume(self):
        self.warm()
        self.rally(1)
        self.assertIsNotNone(self.book.report()["position"])
        state = self.book.state()
        now = state["last_at"]+10
        symbol = state["position"]["symbol"]
        q = dict(state["quotes"][symbol])
        state["pending"] = {"side": "sell", "symbol": symbol, "strategy": state["position"]["strategy"],
                            "created": state["last_at"], "reason": "stop_loss"}
        q.update(timestamp=now-1, capacity="0.0000001")  # almost no modeled volume
        state["quotes"][symbol] = q
        self.book._execute_pending(state, now)
        self.assertIsNone(state["position"], "a stop must exit in one fill")
        self.assertEqual(len(state["trades"]), 1)

    def test_entry_uses_liquidity_floor_on_thin_volume(self):
        self.m = Market(self.book)
        for i in range(300):
            self.m.step({"BTC-USD": 1.0 + (0.0003 if i % 2 else -0.0003), "ETH-USD": 1.0})
        state = self.book.state()
        now = state["last_at"]+10
        q = dict(state["quotes"]["BTC-USD"], timestamp=now-1, capacity="0.0001")
        state["quotes"]["BTC-USD"] = q
        state["pending"] = {"side": "buy", "symbol": "BTC-USD", "strategy": next(s.version for s in __import__("evolver.trading.strategies", fromlist=["CATALOG"]).CATALOG if s.family == "breakout"),
                            "budget": "250", "created": state["last_at"], "reason": "entry:breakout"}
        self.book._execute_pending(state, now)
        self.assertIsNotNone(state["position"], "entry fills up to the $2,000 floor even when bar volume is thin")
        self.assertGreater(D(state["position"]["cost"]), D("240"))

    def test_conflicting_duplicate_halts(self):
        self.warm(5)
        state = self.book.state()
        with self.assertRaises(IntegrityError):
            self.book.apply({"type": "frame", "id": "f1", "observed_at": state["last_at"]+1, "quotes": {}, "bars": {}})

    def test_runner_init_refuses_reset(self):
        root = Path(self.dir.name)/"runner"
        policy = Path(__file__).resolve().parents[2]/"infra"/"trading"/"policy.demo.json"
        self.assertEqual(henry_v2_runner.main(["init", "--policy", str(policy), "--root", str(root)]), 0)
        with self.assertRaises(SystemExit):
            henry_v2_runner.main(["init", "--policy", str(policy), "--root", str(root)])
        snap = json.loads((root/"snapshot.json").read_text())
        self.assertEqual(snap["book"], "henry_v2")
        self.assertEqual(snap["equity_usd"], "500.00")


if __name__ == "__main__":
    unittest.main()
