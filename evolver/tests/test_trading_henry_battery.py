import math
import random
import unittest

from evolver.trading import henry_battery as B
from evolver.trading import henry_lab as L

DAY = 86400
START = 1_514_764_800  # 2018-01-01


def daily(n, start=START, drift=0.0, vol=0.03, p0=100.0, seed=1, crash_at=None):
    random.seed(seed)
    out, p = [], p0
    for k in range(n):
        o = p
        r = drift+random.gauss(0, vol)
        if crash_at is not None and k >= crash_at:
            r = -0.35  # a collapse: -35% a day
        p = max(p*math.exp(r), 1e-9)
        out.append([start+k*DAY, o, max(o, p)*1.01, min(o, p)*0.99, p, 1000.0])
    return out


def cycles(n, seed=3, p0=100.0):
    """Long trends both ways, so a trend filter has something to do."""
    random.seed(seed)
    out, p = [], p0
    for k in range(n):
        drift = 0.006 if (k//150) % 2 == 0 else -0.006
        o = p
        p *= math.exp(drift+random.gauss(0, 0.03))
        out.append([START+k*DAY, o, max(o, p)*1.01, min(o, p)*0.99, p, 1000.0])
    return out


class SplitTest(unittest.TestCase):
    def test_relisted_ticker_is_split_but_a_crash_is_not(self):
        old = daily(300, crash_at=250)  # boom then collapse to ~0
        new = daily(200, start=old[-1][0]+60*DAY, p0=2.0, seed=2)  # same ticker, new coin, months later
        pieces = B.split_listings("LUNA", old+new)
        self.assertEqual(set(pieces), {"LUNA#1", "LUNA#2"})
        self.assertEqual(len(pieces["LUNA#1"]), 300, "the collapse days stay in the first listing")
        self.assertLess(pieces["LUNA#1"][-1][4], pieces["LUNA#1"][200][4]*0.01)

    def test_impossible_upward_jump_splits(self):
        a = daily(200)
        b = [[t+200*DAY, o*1000, h*1000, l*1000, c*1000, v] for t, o, h, l, c, v in daily(200, seed=5)]
        self.assertEqual(len(B.split_listings("X", a+b)), 2)

    def test_clean_series_is_one_listing(self):
        self.assertEqual(list(B.split_listings("BTC", daily(400))), ["BTC"])


class EvaluationTest(unittest.TestCase):
    def test_rule_cuts_a_collapse(self):
        bars = daily(400, drift=0.004, crash_at=320)
        r = B.evaluate_set({"LUNA": bars}, None, 365)["per_symbol"]["LUNA"]
        self.assertLess(r["hold"]["return_pct"], -90)
        self.assertGreater(r["rule"]["return_pct"], r["hold"]["return_pct"]+50)
        self.assertLess(r["rule"]["max_drawdown_pct"], r["hold"]["max_drawdown_pct"])

    def test_hold_and_rule_cover_the_same_days(self):
        bars = cycles(500)
        rets, _, _, warm = B.rule_on(bars, "BTC-USD", None)
        self.assertEqual(len(rets), len(B.hold_returns(bars, warm)))

    def test_traditional_costs_are_applied_and_restored(self):
        before = (L.FEE_BPS, L.SLIP_BPS, dict(L.SPREAD_BPS), L.DEFAULT_SPREAD_BPS)
        bars = cycles(600)
        cheap = B.evaluate_set({"SPX": bars}, None, 252, costs=B.TRAD_COSTS)["portfolio"]["rule"]["return_pct"]
        dear = B.evaluate_set({"SPX": bars}, None, 252)["portfolio"]["rule"]["return_pct"]
        self.assertGreater(cheap, dear)
        self.assertEqual(before, (L.FEE_BPS, L.SLIP_BPS, dict(L.SPREAD_BPS), L.DEFAULT_SPREAD_BPS))

    def test_stooq_parser(self):
        text = "Date,Open,High,Low,Close,Volume\n1989-12-29,1,1,1,1,0\n2020-01-02,10,11,9,10.5,100\nbad,row\n2020-01-03,10.5,10.6,10,10.2,\n"
        rows = B.parse_stooq(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][4], 10.5)


class StressTest(unittest.TestCase):
    def stress_data(self):
        out = {}
        for j, sym in enumerate(("BTC-USD", "ETH-USD")):
            hourly = []
            for t, o, h, l, c, v in cycles(900, seed=10+j):
                for k in range(24):
                    hourly.append([t+k*3600, o if k == 0 else c, h, l, c, v/24])
            out[sym] = {"bars": hourly, "funding": []}
        return out

    def test_stress_suite_runs_and_lag_and_costs_hurt(self):
        st = B.stress(self.stress_data(), log=lambda *_: None)
        self.assertLessEqual(st["double_costs"]["return_pct"], st["base"]["return_pct"])
        self.assertEqual(set(st["neighbors"]), {"30", "40", "60", "70", "80", "100"})
        self.assertTrue(0 <= st["bootstrap"]["p_sharpe_positive"] <= 1)

    def test_lag_shifts_positions(self):
        bars = cycles(10)
        pos = [0, 0, 1, 1, 1, 0, 0, 0, 0, 0]
        base = B.replay_positions(bars, pos, "BTC-USD")
        late = B.replay_positions(bars, pos, "BTC-USD", lag=1)
        self.assertNotEqual(base, late)
        self.assertLess(base[1], 0, "on time: the entry cost lands on the signal day")
        self.assertEqual(late[1], 0.0, "a day late: nothing happens on the signal day")
        self.assertLess(late[2], 0, "the entry cost lands one day later")


class ScorecardTest(unittest.TestCase):
    def test_scorecard(self):
        good = {"datasets": {"a": {"portfolio": {"rule": {"max_drawdown_pct": 20, "return_pct": 50},
                                                 "hold": {"max_drawdown_pct": 70, "return_pct": 90}}}},
                "dead_coins": {"LUNA": {"rule": {"return_pct": -30}, "hold": {"return_pct": -99}}},
                "stress": {"base": {"sharpe": 1.0}, "lag_1d": {"sharpe": 0.8}, "double_costs": {"return_pct": 10},
                           "hold": {"max_drawdown_pct": 80},
                           "neighbors": {str(n): {"return_pct": 5, "max_drawdown_pct": 30} for n in (30, 40, 60, 70, 80, 100)},
                           "bootstrap": {"p_sharpe_positive": 0.95}}}
        self.assertEqual(B.score(good)["verdict"], "PASS")
        bad = {**good, "dead_coins": {"LUNA": {"rule": {"return_pct": -90}, "hold": {"return_pct": -99}}}}
        self.assertFalse(B.score(bad)["checks"]["dead_coins_contained"]["pass"])
        worse_dd = {**good, "datasets": {"a": {"portfolio": {"rule": {"max_drawdown_pct": 75, "return_pct": 50},
                                                             "hold": {"max_drawdown_pct": 70, "return_pct": 90}}}}}
        self.assertFalse(B.score(worse_dd)["checks"]["drawdown_below_hold_everywhere"]["pass"])

    def test_full_run_on_a_synthetic_battery(self):
        battery = {"bitstamp_btc": [[t-1_000*DAY, o, h, l, c, v] for t, o, h, l, c, v in cycles(900, seed=1)],
                   "binance_daily": {"BTC": cycles(1600, seed=2), "XRP": cycles(1600, seed=4),
                                     "LUNA": daily(700, drift=0.004, crash_at=650)},
                   "traditional": {"S&P 500": cycles(2000, seed=6)}, "missing": {}}
        out = B.run(battery, None, log=lambda *_: None)
        self.assertIn("S&P 500 (no BTC filter)", out["datasets"])
        self.assertIn("LUNA", out["dead_coins"])
        self.assertIn(out["verdict"]["verdict"], ("PASS", "FAIL"))


if __name__ == "__main__":
    unittest.main()
