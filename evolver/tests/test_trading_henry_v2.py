import json
import math
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

from evolver.trading import henry_v2_runner
from evolver.trading.experiment import BOOKS, RULES as THREE_BOOK_RULES
from evolver.trading.henry_v2 import HENRY_V2_RULES, HenryV2, IntegrityError, hourly_bars
from evolver.trading.strategies import CATALOG

EPOCH = 1_800_000_000.0  # an exact hour


def bar(t, close, wiggle="0.0008", volume="1000000"):
    c, w = D(str(close)), D(wiggle)
    return {"timestamp": t, "open": str(c), "high": str(c*(1+w)), "low": str(c*(1-w)), "close": str(c), "volume": volume}


class Market:
    """Drives closed 5m bars and a fresh quote; a follow() quote lets queued orders fill."""

    def __init__(self, book, symbols=("BTC-USD", "ETH-USD")):
        self.book, self.symbols, self.t, self.n = book, symbols, EPOCH, 0
        self.prices = {s: 100.0 for s in symbols}

    def _quotes(self, now, spread="0.0002"):
        return {s: {"bid": str(D(str(round(p, 6)))), "ask": str(D(str(round(p, 6)))*(1+D(spread))),
                    "timestamp": now-1, "capacity": "1000000", "capacity_bucket": self.t, "increment": "0.0001"}
                for s, p in self.prices.items()}

    def step(self, moves):
        self.t += 300
        for s, m in moves.items():
            self.prices[s] *= m
        now = self.t+300+5
        self.n += 1
        self.book.apply({"type": "frame", "id": f"f{self.n}", "observed_at": now, "quotes": self._quotes(now),
                         "bars": {s: [bar(self.t, round(p, 6))] for s, p in self.prices.items()}})
        now += 10
        self.n += 1
        return self.book.apply({"type": "frame", "id": f"f{self.n}", "observed_at": now,
                                "quotes": self._quotes(now), "bars": {}})

    def run(self, n, btc, eth=lambda i: 1.0):
        r = None
        for i in range(n):
            r = self.step({"BTC-USD": btc(i), "ETH-USD": eth(i)})
        return r


def zigzag(i):  # a range: alternating legs that go nowhere
    return 1.003 if (i // 6) % 2 == 0 else 1/1.003


def grind_up(i):  # steady uptrend with shallow pullbacks
    return 1.0012 if i % 5 else 0.999


def grind_down(i):
    return 1/1.0012 if i % 5 else 1/0.999


class HenryV2Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name)/"henry_v2.sqlite"
        self.book = HenryV2(self.path, symbols=["BTC-USD", "ETH-USD"], fee_bps="25", slippage_bps="5", epoch=EPOCH)
        self.m = Market(self.book)

    def tearDown(self):
        self.book.close()
        self.dir.cleanup()

    def trades(self):
        return self.book.state()["trades"]

    # ---------- isolation and rules ----------
    def test_three_book_study_is_untouched(self):
        self.assertEqual(BOOKS, ("baseline", "kelly", "henry"))
        self.assertNotIn("henry_v2", json.dumps(THREE_BOOK_RULES))

    def test_rules_are_unbounded_but_never_leveraged(self):
        for gate in ("supervisor", "news", "session_hours", "daily_loss_limit", "fixed_target", "max_hold"):
            self.assertIn(gate, HENRY_V2_RULES["gates_removed"])
        self.assertEqual(HENRY_V2_RULES["leverage"], "none")
        self.assertEqual(HENRY_V2_RULES["regime_routes"]["downtrend"], [])

    # ---------- regime reading ----------
    def test_hourly_bars_never_use_a_truncated_or_open_hour(self):
        bars = [bar(EPOCH+1800+i*300, 100) for i in range(30)]  # starts mid-hour
        hours = hourly_bars(bars, EPOCH+1800+30*300+600)
        self.assertTrue(hours)
        self.assertTrue(all(h["timestamp"] >= EPOCH+3600 for h in hours))
        self.assertTrue(all(h["bars"] == 12 for h in hours))

    def test_warming_up_takes_no_trades(self):
        r = self.m.run(300, grind_up)  # 25 hours < 30 required
        self.assertEqual(r["regimes"]["BTC-USD"], "warming_up")
        self.assertIsNone(r["position"])
        self.assertEqual(r["closed_trades"], 0)

    def test_regime_labels(self):
        self.m.run(420, grind_up, grind_down)
        r = self.book.report()
        self.assertEqual(r["regimes"]["BTC-USD"], "uptrend")
        self.assertEqual(r["regimes"]["ETH-USD"], "downtrend")
        other = HenryV2(Path(self.dir.name)/"range"/"henry_v2.sqlite", symbols=["BTC-USD", "ETH-USD"],
                        fee_bps="25", slippage_bps="5", epoch=EPOCH)
        Market(other).run(420, zigzag, zigzag)
        self.assertEqual(other.report()["regimes"]["BTC-USD"], "consolidation")
        other.close()

    # ---------- behavior by regime ----------
    def test_downtrend_stays_in_cash(self):
        # Falling market with sharp dips that would trigger dip buys in a range.
        self.m.run(480, lambda i: 0.985 if i % 40 == 39 else grind_down(i), grind_down)
        r = self.book.report()
        self.assertEqual(r["regimes"]["BTC-USD"], "downtrend")
        self.assertEqual(r["closed_trades"], 0)
        self.assertIsNone(r["position"])
        self.assertEqual(r["equity_usd"], "500.00")

    def test_uptrend_trades_momentum_presses_and_banks_the_run(self):
        self.m.run(400, grind_up)
        self.m.run(60, lambda i: 1.004)  # acceleration: breakouts fire inside an uptrend
        r = self.book.report()
        self.assertIsNotNone(r["position"], "momentum entry in an uptrend")
        self.assertEqual(r["position"]["entry_regime"], "uptrend")
        self.assertIn(self.book.state()["position"]["family"], {"breakout", "ema_trend"})
        self.assertTrue(r["position"]["pressed"], "winner pressed in an uptrend")
        self.m.run(40, lambda i: 0.993)  # reversal
        t = max(self.trades(), key=lambda x: D(x["pnl_usd"]))
        self.assertTrue(t["pressed"])
        self.assertGreater(t["hold_minutes"], 240, "rode the trend for hours")
        self.assertEqual(t["entry_regime"], "uptrend")
        self.assertGreater(D(t["pnl_usd"]), 0, "trailing stop banks the run")
        self.assertIn(t["reason"], {"trailing_stop", "trend_break", "regime_downtrend"})
        self.assertNotIn("mean_reversion", {x["family"] for x in self.trades()}, "no dip buys outside a range")

    def test_consolidation_only_buys_dips_and_takes_profit_at_the_middle(self):
        self.m.run(420, zigzag)
        self.assertEqual(self.book.report()["regimes"]["BTC-USD"], "consolidation")
        self.m.run(3, lambda i: 0.99)   # flush below the range
        self.m.run(12, lambda i: 1.006)  # snap back
        trades = self.trades()
        self.assertTrue(trades, "a dip buy round trip happened")
        for t in trades:
            self.assertEqual(t["family"], "mean_reversion")
            self.assertEqual(t["entry_regime"], "consolidation")
            self.assertFalse(t["pressed"], "dip buys are never pressed")
        self.assertIn("range_target", {t["reason"] for t in trades})

    def test_stops_scale_with_volatility(self):
        calm = self.book._atr_pct([bar(EPOCH+i*300, 100, "0.0005") for i in range(20)])
        wild = self.book._atr_pct([bar(EPOCH+i*300, 100, "0.01") for i in range(20)])
        self.assertLess(self.book._clamp_pct(calm*D("2.5")), self.book._clamp_pct(wild*D("2.5")))
        self.assertEqual(self.book._clamp_pct(D("0.01")), D(HENRY_V2_RULES["stop_pct_min"]))
        self.assertEqual(self.book._clamp_pct(D("50")), D(HENRY_V2_RULES["stop_pct_max"]))

    def test_picks_the_strongest_coin(self):
        self.m.run(400, grind_up, lambda i: 1.0006 if i % 5 else 0.9995)
        self.m.run(30, lambda i: 1.004, lambda i: 1.004)  # both break out together
        pos = self.book.report()["position"] or {}
        first = self.trades()[0]["symbol"] if self.trades() else pos.get("symbol")
        self.assertEqual(first, "BTC-USD", "stronger 24h coin wins the tie")

    def test_broken_quotes_are_ignored(self):
        self.m.run(400, grind_up)
        state = self.book.state()
        now = state["last_at"]+5
        for s in state["quotes"]:
            state["quotes"][s] = dict(state["quotes"][s], ask=str(D(state["quotes"][s]["bid"])*D("1.05")), timestamp=now-1)
        self.assertIsNone(self.book._best_signal(state, now))

    # ---------- execution ----------
    def _open(self, family="breakout", budget="250"):
        self.m.run(400, grind_up)
        state = self.book.state()
        state.update(position=None, cash="500", trades=[], halt="", floor_breached=False, peak="500")
        now = state["last_at"]+10
        q = dict(state["quotes"]["BTC-USD"], timestamp=now-1)
        state["quotes"]["BTC-USD"] = q
        state["pending"] = {"side": "buy", "symbol": "BTC-USD", "budget": budget, "created": state["last_at"],
                            "reason": "entry:"+family, "regime": "uptrend", "atr_pct": "0.2", "stop_pct": "1",
                            "spread_bps": "2", "strategy": next(s.version for s in CATALOG if s.family == family)}
        return state, now, q

    def test_exit_fills_whole_position_despite_thin_volume(self):
        state, now, q = self._open()
        self.book._execute_pending(state, now)
        self.assertIsNotNone(state["position"])
        state["pending"] = {"side": "sell", "symbol": "BTC-USD", "strategy": state["position"]["strategy"],
                            "created": now, "reason": "stop_loss"}
        state["quotes"]["BTC-USD"] = dict(q, timestamp=now+9, capacity="0.0000001")
        self.book._execute_pending(state, now+10)
        self.assertIsNone(state["position"], "a stop must exit in one fill")
        self.assertEqual(len(state["trades"]), 1)
        self.assertEqual(state["trades"][0]["entry_regime"], "uptrend")

    def test_entry_uses_liquidity_floor_on_thin_volume(self):
        state, now, q = self._open()
        state["quotes"]["BTC-USD"] = dict(q, capacity="0.0001")
        self.book._execute_pending(state, now)
        self.assertIsNotNone(state["position"], "entry fills up to the $2,000 floor even when bar volume is thin")
        self.assertGreater(D(state["position"]["cost"]), D("240"))

    def test_floor_halts_and_liquidates(self):
        state, now, q = self._open(budget="400")  # floor only bites once he is pressed
        self.book._execute_pending(state, now)
        crash = dict(q, bid=str(D(q["bid"])*D("0.001")), ask=str(D(q["ask"])*D("0.001")), timestamp=now+20)
        state["quotes"]["BTC-USD"] = crash
        self.book._mark(state)
        self.assertTrue(state["floor_breached"])
        self.book._decide(state, now+21)
        self.assertEqual(state["pending"]["reason"], "floor_liquidation")
        state["quotes"]["BTC-USD"] = dict(crash, timestamp=now+25)
        self.book._execute_pending(state, now+26)
        self.assertIsNone(state["position"])
        self.assertGreater(D(state["cash"]), 0)

    # ---------- journal ----------
    def test_replay_matches_and_journal_is_immutable(self):
        self.m.run(380, grind_up)
        self.assertEqual(self.book.verify_replay()["replay"], "ok")
        self.book.close()
        reopened = HenryV2(self.path)
        self.assertEqual(reopened.identity["rules"], HENRY_V2_RULES)
        self.book = reopened

    def test_conflicting_duplicate_halts(self):
        self.m.run(3, grind_up)
        state = self.book.state()
        with self.assertRaises(IntegrityError):
            self.book.apply({"type": "frame", "id": "f1", "observed_at": state["last_at"]+1, "quotes": {}, "bars": {}})

    def test_runner_seeds_hourly_history_from_history_file(self):
        root, src = Path(self.dir.name)/"runner", Path(self.dir.name)/"src"
        policy = Path(__file__).resolve().parents[2]/"infra"/"trading"/"policy.demo.json"
        self.assertEqual(henry_v2_runner.main(["init", "--policy", str(policy), "--root", str(root)]), 0)
        with self.assertRaises(SystemExit):
            henry_v2_runner.main(["init", "--policy", str(policy), "--root", str(root)])
        book = HenryV2(root/"henry_v2.sqlite")
        now = math.floor(book.identity["epoch"]/3600)*3600+1800
        deep = {s: [bar(now-48*3600+i*300, 100*(1.0005**i)) for i in range(48*12-3)] for s in book.identity["symbols"]}
        (src/"market").mkdir(parents=True)
        (src/"market"/"history.json").write_text(json.dumps({"histories": deep}))
        frame = henry_v2_runner.hourly_seed(book, book.state(), src, now)
        self.assertEqual(set(frame), set(book.identity["symbols"]))
        self.assertGreaterEqual(len(frame["BTC-USD"]), HENRY_V2_RULES["regime"]["min_hours"])
        book.close()
        snap = json.loads((root/"snapshot.json").read_text())
        self.assertEqual(snap["rules_version"], "henry-raging-bull-v3")
        self.assertEqual(snap["equity_usd"], "500.00")


if __name__ == "__main__":
    unittest.main()
