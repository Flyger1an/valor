import json
import math
import random
import tempfile
import unittest
from pathlib import Path

from evolver.trading import henry_carry as C

START = 1_640_995_200  # 2022-01-01


def market(seed=5, days=900, mean_8h=0.0001, persistence=0.97, noise_8h=0.00008, coins=("BTC-USD", "ETH-USD", "ADA-USD"),
           with_perp=True):
    """Spot hourly bars, 8h funding as a persistent process around `mean_8h`, daily perp prices
    with a small mean-reverting basis."""
    random.seed(seed)
    out = {}
    for j, sym in enumerate(coins):
        p, f, basis = 100.0*(j+1), mean_8h, 0.0
        bars, fund, perp = [], [], []
        for h in range(days*24):
            t = START+h*3600
            o = p
            p *= math.exp(random.gauss(0, 0.007))
            bars.append([t, o, max(o, p)*1.001, min(o, p)*0.999, p, 1.0])
            if t % 28800 == 0:
                f = mean_8h+persistence*(f-mean_8h)+random.gauss(0, noise_8h)
                fund.append([t, f])
            if t % 86400 == 86400-3600:
                basis = 0.8*basis+random.gauss(0, 0.0005)
                perp.append([t-t % 86400, p, p, p, p*(1+basis)])
        out[sym] = {"bars": bars, "funding": fund}
        if with_perp:
            out[sym]["perp_daily"] = perp
    return C.merge([{"symbols": out}])


class CarryMechanicsTest(unittest.TestCase):
    def test_daily_view_sums_funding_and_aligns_perp(self):
        m = market(days=10)
        v = C.daily_view(m["BTC-USD"])
        day = sorted(v)[3]
        self.assertIsNotNone(v[day]["perp"])
        prints = [r for t, r in m["BTC-USD"]["funding"].items() if C.day_of(t) == day]
        self.assertAlmostEqual(v[day]["funding"], sum(prints))

    def test_always_on_collects_funding_minus_costs(self):
        m = market(days=120, mean_8h=0.0002, noise_8h=0.0)
        view = C.daily_view(m["BTC-USD"])
        rows, trades = C.simulate({"rule": "always_on", "universe": "all"}, "BTC-USD", view)
        total = sum(r for _, r, _ in rows)
        expected = 0.0006*(len(rows)-1)/C.CAPITAL_PER_NOTIONAL
        self.assertGreater(total, 0)
        self.assertLess(abs(total-expected), 0.05, "funding minus a few costs, scaled to capital")

    def test_negative_funding_costs_the_short(self):
        m = market(days=60, mean_8h=-0.0002, noise_8h=0.0)
        rows, _ = C.simulate({"rule": "always_on", "universe": "all"}, "BTC-USD", C.daily_view(m["BTC-USD"]))
        self.assertLess(sum(r for _, r, _ in rows), 0)

    def test_timed_rule_stays_out_when_funding_is_low(self):
        m = market(days=120, mean_8h=0.00001, noise_8h=0.0)
        cfg = {"rule": "timed", "lookback_days": 7, "entry_apr": 10.0, "exit_apr": 5.0, "universe": "all"}
        rows, trades = C.simulate(cfg, "BTC-USD", C.daily_view(m["BTC-USD"]))
        self.assertFalse(any(on for _, _, on in rows))
        self.assertEqual(trades, [])

    def test_basis_moves_hit_the_pnl(self):
        view = {START+k*86400: {"spot": 100.0, "perp": 100.0, "funding": 0.0} for k in range(5)}
        view[START+3*86400]["perp"] = 102.0  # the short loses when the perp rallies vs spot
        rows, _ = C.simulate({"rule": "always_on", "universe": "all"}, "BTC-USD", view)
        self.assertLess(rows[3][1], -0.01)

    def test_alpaca_spot_fees_cost_more(self):
        self.assertGreater(C.leg_cost("BTC-USD", C.ALPACA_SPOT_FEE_BPS), C.leg_cost("BTC-USD", C.COSTS["spot_fee_bps"]))

    def test_majors_universe_ignores_alts(self):
        m = market(days=60)
        views = {s: C.daily_view(v) for s, v in m.items()}
        _, trades, _ = C.run({"rule": "always_on", "universe": "majors"}, views)
        self.assertTrue(all(t["symbol"] in C.MAJORS for t in trades))


class CarryTrainingTest(unittest.TestCase):
    def test_real_premium_is_found_and_noise_is_rejected(self):
        real = C.train(market(seed=5, mean_8h=0.0001), shuffles=1, log=lambda *_: None)
        self.assertGreater(real["premium"]["mean_daily_funding_t_stat"], 3)
        self.assertTrue(any(real["premium_real"].values()))
        self.assertIsNotNone(real["chosen"])
        noise = C.train(market(seed=6, mean_8h=0.0, persistence=0.0), shuffles=1, log=lambda *_: None)
        self.assertFalse(any(noise["premium_real"].values()), "no premium where there is none")
        self.assertIsNone(noise["chosen"])

    def test_missing_perp_prices_are_flagged(self):
        L = C.train(market(days=300, with_perp=False), shuffles=1, log=lambda *_: None)
        self.assertTrue(L["coins_without_perp_prices"])

    def test_one_shot_test_ledger_and_tamper_check(self):
        L = C.train(market(seed=5), shuffles=1, log=lambda *_: None)
        data = market(seed=11, days=500)
        with tempfile.TemporaryDirectory() as d:
            ledger = str(Path(d)/"ledger.json")
            r1 = C.test(data, L, ledger)
            self.assertIn(r1["verdict"], ("PASS", "FAIL"))
            self.assertEqual(C.test(data, L, ledger)["previous_tests_of_these_lessons"], 1)
        L["chosen"] = {**L["chosen"], "universe": "majors" if L["chosen"]["universe"] == "all" else "all"}
        with self.assertRaises(ValueError):
            C.test(data, L)


if __name__ == "__main__":
    unittest.main()
