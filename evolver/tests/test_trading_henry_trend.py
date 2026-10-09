import json
import math
import tempfile
import unittest
from pathlib import Path

from evolver.trading import henry_lab as L
from evolver.trading import henry_trend as T
from evolver.trading import henry_trend_runner as R

DAY = 86400
D0 = 1_767_225_600  # 2026-01-01


def seed_bars(days=200, drift=0.004, p0=100.0, end=D0):
    out, p = [], p0
    for k in range(days):
        t = end-(days-k)*DAY
        o = p
        p *= math.exp(drift)
        out.append([t, o, p*1.01, o*0.99, p, 1000.0])
    return out


class Feed:
    """Drives the book with 5m bars and quotes, day after day."""

    def __init__(self, book, prices):
        self.book, self.prices, self.n = book, dict(prices), 0

    def run_day(self, day, moves=None, every=1800):
        moves = moves or {}
        for k in range(0, DAY, every):
            t = day+k
            for s in self.prices:
                self.prices[s] *= math.exp(moves.get(s, 0.0)*every/DAY)
            self.frame(t+every)

    def frame(self, now, bid_mult=1.0):
        bars = {s: [{"timestamp": now-600, "open": p, "high": p*1.001, "low": p*0.999, "close": p, "volume": 1.0}]
                for s, p in self.prices.items()}
        quotes = {s: {"bid": p*bid_mult, "ask": p*bid_mult*1.0004, "timestamp": now-1} for s, p in self.prices.items()}
        self.n += 1
        return self.book.apply({"type": "frame", "id": f"f{self.n}", "observed_at": now, "quotes": quotes, "bars": bars})


class TrendBookTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name)/"henry_trend.sqlite"
        self.syms = ["BTC-USD", "ETH-USD", "ADA-USD"]
        self.book = T.TrendBook(self.path, symbols=self.syms, epoch=D0-DAY)
        daily = {"BTC-USD": seed_bars(), "ETH-USD": seed_bars(p0=50.0), "ADA-USD": seed_bars(drift=-0.004)}
        self.book.apply({"type": "seed", "id": "seed1", "observed_at": D0+60, "source": "test", "daily": daily})
        self.feed = Feed(self.book, {s: daily[s][-1][4] for s in self.syms})

    def tearDown(self):
        self.book.close()
        self.dir.cleanup()

    def test_decides_once_at_the_daily_close_and_buys_only_uptrends(self):
        self.feed.run_day(D0)
        self.feed.frame(D0+DAY+600)
        st = self.book.state()
        self.assertEqual(st["last_decided_day"], D0)
        tg = st["decisions"][-1]["targets"]
        self.assertGreater(tg["BTC-USD"]["pos"], 0)
        self.assertGreater(tg["ETH-USD"]["pos"], 0)
        self.assertEqual(tg["ADA-USD"]["pos"], 0, "a downtrend is not bought")
        self.feed.frame(D0+DAY+900)
        st = self.book.state()
        self.assertEqual(set(st["positions"]), {"BTC-USD", "ETH-USD"})
        for p in st["positions"].values():
            self.assertLessEqual(p["cost"], 500/3+1, "each coin is one equal sleeve at most")
        days = [d["day"] for d in st["decisions"]]
        self.assertEqual(days, [D0-DAY, D0], "decides on the seeded day at start, then once per close")

    def test_live_decision_matches_the_lab_simulator_exactly(self):
        self.feed.run_day(D0)
        self.feed.frame(D0+DAY+600)
        st = self.book.state()
        bars = [b[:6] for b in st["daily"]["ETH-USD"]]
        leader = L.Leader([b[:6] for b in st["daily"]["BTC-USD"]])
        rows, _ = L.simulate(T.RULE, "ETH-USD", bars, [], leader)
        self.assertAlmostEqual(st["decisions"][-1]["targets"]["ETH-USD"]["pos"], round(rows[-1][2], 6))

    def test_protective_stop_sells_intraday(self):
        self.feed.run_day(D0)
        self.feed.frame(D0+DAY+600)
        self.feed.frame(D0+DAY+900)
        stop = self.book.state()["positions"]["BTC-USD"]["stop"]
        self.feed.prices["BTC-USD"] = stop*0.97
        self.feed.frame(D0+DAY+1200)
        self.feed.frame(D0+DAY+1500)
        st = self.book.state()
        self.assertNotIn("BTC-USD", st["positions"])
        self.assertEqual(st["trades"][-1]["reason"], "stop")

    def test_kill_switch_liquidates_and_halts(self):
        self.feed.run_day(D0)
        self.feed.frame(D0+DAY+600)
        self.feed.frame(D0+DAY+900)
        st = self.book.state()
        for p in st["positions"].values():
            p["stop"] = None  # isolate the kill switch from the stop
        st["peak"] = 1000.0
        self.book.db.execute("INSERT OR REPLACE INTO meta VALUES ('state',?)", (json.dumps(st),))
        self.book.db.commit()
        self.feed.frame(D0+DAY+1200)
        self.feed.frame(D0+DAY+1500)
        st = self.book.state()
        self.assertEqual(st["halt"], "max_drawdown_kill_switch")
        self.assertEqual(st["positions"], {})

    def test_missing_day_becomes_flat_then_a_later_seed_repairs_it(self):
        self.feed.run_day(D0)
        self.feed.frame(D0+3*DAY+600)  # the book was down for two days: no 5m bars
        st = self.book.state()
        flats = [b for b in st["daily"]["BTC-USD"] if b[5] == 0]
        self.assertTrue(flats)
        fix = {"BTC-USD": [[int(b[0]), b[4], b[4]*1.02, b[4]*0.98, b[4]*1.01, 500.0] for b in flats]}
        self.book.apply({"type": "seed", "id": "seed2", "observed_at": D0+3*DAY+700, "source": "test", "daily": fix})
        st = self.book.state()
        self.assertFalse([b for b in st["daily"]["BTC-USD"] if b[5] == 0])
        self.assertEqual(st["skips"]["repaired_days"], len(flats))

    def test_shadow_agreement_and_divergence_flag(self):
        self.feed.run_day(D0)
        self.feed.frame(D0+DAY+600)
        st = self.book.state()
        day = str(int(st["decisions"][-1]["day"]))
        agree = {"days": {day: {s: t["pos"] for s, t in st["decisions"][-1]["targets"].items()}}}
        self.assertEqual(T.shadow_check(st, agree)["status"], "ok")
        st["decisions"] = [dict(st["decisions"][-1], day=st["decisions"][-1]["day"]+k*DAY) for k in range(3)]
        disagree = {"days": {str(int(d["day"])): {"ADA-USD": 1.0} for d in st["decisions"]}}
        res = T.shadow_check(st, disagree)
        self.assertTrue(res["flag"])
        self.assertEqual(res["divergence_streak"], 3)

    def test_replay_and_immutability(self):
        self.feed.run_day(D0)
        self.feed.frame(D0+DAY+600)
        self.assertEqual(self.book.verify_replay()["replay"], "ok")
        self.book.close()
        self.book = T.TrendBook(self.path)

    def test_seed_never_adds_future_days(self):
        fresh = T.TrendBook(Path(self.dir.name)/"x"/"henry_trend.sqlite", symbols=["BTC-USD"], epoch=D0)
        bars = seed_bars(end=D0+5*DAY)
        fresh.apply({"type": "seed", "id": "s", "observed_at": D0+60, "daily": {"BTC-USD": bars}})
        self.assertLess(fresh.state()["daily"]["BTC-USD"][-1][0], D0)
        fresh.close()

    def test_report_scorecard(self):
        self.feed.run_day(D0)
        self.feed.frame(D0+DAY+600)
        self.feed.frame(D0+DAY+900)
        r = self.book.report()
        self.assertIn("equal_weight_hold_pct", r["vs"])
        self.assertIn("btc_hold_pct", r["vs"])
        self.assertEqual(r["rule"], T.RULE["id"])


class RunnerTest(unittest.TestCase):
    def test_capture_sends_only_new_closed_bars(self):
        with tempfile.TemporaryDirectory() as d:
            root, src = Path(d)/"h", Path(d)/"src"
            (src/"market").mkdir(parents=True)
            book = T.TrendBook(root/"henry_trend.sqlite", symbols=["BTC-USD"], epoch=D0)
            now = D0+3500
            (src/"market"/"quotes.json").write_text(json.dumps({"timestamp": now-5, "quotes": {"BTC-USD": {"bid": 1, "ask": 1.001, "timestamp": now-5}}}))
            hist = [{"timestamp": D0+k*300, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for k in range(12)]
            (src/"market"/"signals.json").write_text(json.dumps({"histories": {"BTC-USD": hist}}))
            f = R.capture(book, src, now)
            self.assertEqual(len(f["bars"]["BTC-USD"]), 11, "the bar still open at `now` is excluded")
            book.apply(f)
            f2 = R.capture(book, src, now+30)
            self.assertNotIn("BTC-USD", f2["bars"])
            book.close()


if __name__ == "__main__":
    unittest.main()
