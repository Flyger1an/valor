import copy
import json
import math
import random
import tempfile
import unittest
from pathlib import Path

from evolver.trading import henry_lab as L

START = 1_640_995_200  # 2022-01-01, an exact day


def market(seed=7, years=2.5, drift=0.0006, coins=("BTC-USD", "ETH-USD")):
    """Hourly bars with regime cycles (bull, range, bear, range) or pure noise when drift=0."""
    random.seed(seed)
    n = int(years*365*24)
    phases, k = [], 0
    cycle = [("bull", 70), ("range", 50), ("bear", 60), ("range", 50)]
    while len(phases) < n:
        name, days = cycle[k % 4]
        k += 1
        phases += [name]*int(days*24*random.uniform(0.6, 1.4))
    shocks = [random.gauss(0, 0.006) for _ in range(n)]
    out = {}
    for j, sym in enumerate(coins):
        beta = 1.0+0.3*j
        p, anchor, bars, fund = 100.0*(j+1), 100.0*(j+1), [], []
        for i in range(n):
            name, t = phases[i], START+i*3600
            d = {"bull": drift, "bear": -drift}.get(name, 0.0)
            ret = beta*(d+shocks[i])+random.gauss(0, 0.004*beta)
            if name == "range":
                ret += -0.01*math.log(p/anchor)
            else:
                anchor = p
            o = p
            p *= math.exp(ret)
            bars.append([t, o, max(o, p)*1.002, min(o, p)*0.998, p, 1000.0])
            if t % 28800 == 0:
                fund.append([t, 0.0001])
        out[sym] = {"bars": bars, "funding": fund}
    return out


class DataTest(unittest.TestCase):
    def test_resample_keeps_only_complete_buckets(self):
        bars = [[START+h*3600, 1, 2, 0.5, 1+h, 1] for h in range(30)]
        del bars[5]  # a missing hour ruins the first day
        daily = L.resample(bars, 86400)
        self.assertEqual(daily, [])
        four = L.resample(bars, 4*3600)
        self.assertNotIn(START+4*3600, [b[0] for b in four])
        self.assertIn(START, [b[0] for b in four])

    def test_shuffle_keeps_bar_sizes_but_destroys_order(self):
        bars = market(years=0.3)["BTC-USD"]["bars"]
        sh = L.shuffled(bars, seed=1)
        self.assertEqual(len(sh), len(bars))
        orig = sorted(round(bars[k][4]/bars[k-1][4], 9) for k in range(1, len(bars)))
        new = sorted(round(sh[k][4]/sh[k-1][4], 9) for k in range(1, len(sh)))
        self.assertEqual(orig, new)
        self.assertNotEqual([b[4] for b in bars[:50]], [b[4] for b in sh[:50]])
        for b in sh:
            self.assertTrue(b[3] <= min(b[1], b[4]) <= max(b[1], b[4]) <= b[2])

    def test_merge_unions_files_without_duplicates(self):
        a = {"symbols": {"BTC-USD": {"bars": [[1, 1, 1, 1, 1, 1], [2, 2, 2, 2, 2, 2]], "funding": [[1, 0.1]]}}}
        b = {"symbols": {"BTC-USD": {"bars": [[2, 2, 2, 2, 2, 2], [3, 3, 3, 3, 3, 3]], "funding": [[2, 0.2]]}}}
        m = L.merge([a, b])
        self.assertEqual([x[0] for x in m["BTC-USD"]["bars"]], [1, 2, 3])
        self.assertEqual(len(m["BTC-USD"]["funding"]), 2)


class SimulationTest(unittest.TestCase):
    def setUp(self):
        self.data = market()
        self.leader = L.Leader(L.resample(self.data["BTC-USD"]["bars"], 86400))

    def test_no_lookahead(self):
        cfg = next(c for c in L.library() if c["family"] == "donchian_breakout" and c["tf"] == "1d")
        bars = L.resample(self.data["ETH-USD"]["bars"], 86400)
        rows, _ = L.simulate(cfg, "ETH-USD", bars, [], None)
        future = copy.deepcopy(bars)
        for b in future[400:]:
            b[1:5] = [x*5 for x in b[1:5]]
        rows2, _ = L.simulate(cfg, "ETH-USD", future, [], None)
        self.assertEqual(rows[:400], rows2[:400], "rewriting the future must not change the past")

    def test_every_family_trades_and_pays_costs(self):
        for fam in ("donchian_breakout", "ma_trend", "momentum", "trend_pullback", "squeeze_breakout", "fair_value_gap"):
            cfg = next(c for c in L.library() if c["family"] == fam and c["tf"] == "1d")
            daily, trades = L.run_config(cfg, self.data, self.leader)
            self.assertTrue(daily, fam)
            if trades:
                self.assertTrue(all(isinstance(t["ret"], float) for t in trades))

    def test_long_only_never_shorts(self):
        cfg = next(c for c in L.library() if c["side"] == "long" and c["family"] == "momentum")
        bars = L.resample(self.data["BTC-USD"]["bars"], 86400)
        rows, _ = L.simulate(cfg, "BTC-USD", bars, [], None)
        self.assertTrue(all(p >= 0 for _, _, p in rows))

    def test_no_leverage(self):
        for cfg in L.library()[:20]:
            bars = L.resample(self.data["ETH-USD"]["bars"], L.TF[cfg["tf"]])
            rows, _ = L.simulate(cfg, "ETH-USD", bars, [], None)
            self.assertTrue(all(abs(p) <= 1.0 for _, _, p in rows))

    def test_fair_value_gap_detects_and_trades_the_retrace(self):
        bars = []
        p = 100.0
        for k in range(80):  # uptrend so price is above its 50-bar average
            p *= 1.004
            bars.append([START+k*86400, p, p*1.003, p*0.997, p*1.001, 1000.0])
        # candle 1, a strong candle 2, candle 3 whose low clears candle 1's high: a bullish gap
        h1 = bars[-1][2]
        bars.append([START+80*86400, p*1.001, p*1.06, p*1.0, p*1.055, 1000.0])
        bars.append([START+81*86400, p*1.055, p*1.08, h1*1.03, p*1.07, 1000.0])
        # retrace into the gap and hold above its bottom
        bars.append([START+82*86400, p*1.07, p*1.07, h1*1.005, h1*1.02, 1000.0])
        for k in range(83, 100):
            p2 = h1*1.02*(1.01**(k-82))
            bars.append([START+k*86400, p2/1.01, p2*1.003, p2/1.01*0.997, p2, 1000.0])
        cfg = next(c for c in L.library() if c["family"] == "fair_value_gap" and c["tf"] == "1d" and c["side"] == "long"
                   and not c["btc_filter"])
        rows, trades = L.simulate(cfg, "BTC-USD", bars, [], None)
        positions = [p for _, _, p in rows]
        self.assertEqual(positions[81], 0, "no entry on the candle that forms the gap")
        self.assertGreater(positions[82], 0, "enters on the retrace into the gap")
        self.assertTrue(trades and trades[0]["ret"] > 0)

    def test_short_receives_positive_funding(self):
        bars = [[START+k*86400, 100, 100.5, 99.5, 100, 1] for k in range(5)]
        cfg = {"family": "momentum", "tf": "1d", "lookback": 1, "side": "both", "btc_filter": False}
        fund = [[START+k*86400+3600, 0.01] for k in range(5)]
        per_bar = L.funding_per_bar(bars, fund, 86400)
        self.assertEqual(per_bar[1], 0.01)


class TrainingTest(unittest.TestCase):
    def test_trend_market_teaches_trend_lessons_and_noise_teaches_nothing(self):
        lessons = L.train(market(seed=7, years=3), shuffles=1, log=lambda *_: None)
        self.assertTrue(lessons["chosen"], "a market with real regime cycles has learnable trend setups")
        families = {c["family"] for c in lessons["chosen"]}
        self.assertLessEqual(len(lessons["chosen"]), L.SELECTION["max_selected"])
        self.assertEqual(len(families), len(lessons["chosen"]), "at most one per family")
        noise = L.train(market(seed=9, years=3, drift=0.0), shuffles=1, log=lambda *_: None)
        self.assertEqual(noise["chosen"], [], "a lab that learns from noise is fooling itself")

    def test_selection_rules(self):
        good = {"blocks": [5, 3, -2, 8, 4], "profit_factor": 1.5, "trades": 40, "sharpe": 1.2}
        self.assertTrue(L.survives(good, 1.0)[0])
        self.assertFalse(L.survives({**good, "sharpe": 0.9}, 1.0)[0], "must beat the luck bar")
        self.assertFalse(L.survives({**good, "blocks": [5, -3, -2, -8, 4]}, 0)[0], "most blocks positive")
        self.assertFalse(L.survives({**good, "blocks": [5, 3, -20, 8, 4]}, 0)[0], "no disaster block")
        self.assertFalse(L.survives({**good, "trades": 5}, 0)[0])


class TestingTest(unittest.TestCase):
    def lessons(self):
        cfg = next(c for c in L.library() if c["family"] == "donchian_breakout" and c["tf"] == "1d")
        lessons = {"lab": "henry-lab-v1", "chosen": [cfg], "selection_rules": L.SELECTION, "luck_bar_sharpe": 0.5,
                   "trained_on": {"from": 0, "to": 1, "coins": ["BTC-USD"]}}
        lessons["fingerprint"] = L.fingerprint(lessons)
        return lessons

    def test_tampered_lessons_are_refused(self):
        lessons = self.lessons()
        lessons["chosen"][0]["entry"] = 30
        with self.assertRaises(ValueError):
            L.test(market(seed=3), lessons)

    def test_ledger_flags_a_second_test_of_the_same_lessons(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = str(Path(d)/"ledger.json")
            data = market(seed=3)
            first = L.test(data, self.lessons(), ledger)
            self.assertEqual(first["previous_tests_of_these_lessons"], 0)
            self.assertIn(first["verdict"], ("PASS", "FAIL"))
            second = L.test(data, self.lessons(), ledger)
            self.assertEqual(second["previous_tests_of_these_lessons"], 1)
            self.assertEqual(len(json.loads(Path(ledger).read_text())), 2)

    def test_promote_freezes_one_named_setup_and_records_why(self):
        lessons = self.lessons()
        cfg = lessons["chosen"][0]
        lessons.update(leaderboard=[{"id": cfg["id"], "why_not": ["Sharpe 0.7 does not beat the luck bar 1.0"],
                                     "sharpe": 0.7}], setups_tested=60, family_summary={})
        out = L.promote(lessons, cfg["id"], "best training candidate")
        self.assertEqual(out["chosen"], [cfg])
        self.assertEqual(out["promoted"]["rank_on_training_leaderboard"], 1)
        self.assertEqual(out["fingerprint"], L.fingerprint(out))
        r = L.test(market(seed=3), out)
        self.assertTrue(r["promoted"]["missed"])
        with self.assertRaises(ValueError):
            L.promote(lessons, "family=nope", "x")

    def test_nothing_learned_means_nothing_tested(self):
        lessons = {**self.lessons(), "chosen": []}
        lessons["fingerprint"] = L.fingerprint(lessons)
        self.assertEqual(L.test(market(seed=3), lessons)["verdict"], "NOTHING TO TEST")

    def test_gate_compares_against_btc_hold(self):
        r = L.test(market(seed=3), self.lessons())
        self.assertIn("drawdown_below_btc_hold", r["checks"])
        self.assertIn("by_year", r["btc_hold"])


if __name__ == "__main__":
    unittest.main()
