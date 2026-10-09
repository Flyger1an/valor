import math
import random
import unittest

from evolver.trading import henry_rotation as R

DAY = 86400
START = 1104537600  # 2005-01-01


def series(n, drift, seed, vol=0.004):
    rng = random.Random(seed)
    p, out = 100.0, []
    for k in range(n):
        p *= math.exp(drift+rng.gauss(0, vol))
        out.append([START+k*DAY, p, p, p, p, 0.0])
    return out


def market(n=900, drifts=None, vol=0.004):
    drifts = drifts or {s: 0.0001*(k-8) for k, s in enumerate(R.UNIVERSE)}
    return {"etfs": {s: series(n, drifts[s], k, vol) for k, s in enumerate(R.UNIVERSE)}}


class RotationTests(unittest.TestCase):
    def test_month_ends(self):
        days = [START+k*DAY for k in range(62)]  # Jan 1 .. Mar 3
        ends = R.month_ends(days)
        self.assertEqual(ends[:2], [30, 58])

    def test_picks_top_three_by_twelve_month_return(self):
        days, closes = R.align(market(vol=0.0)["etfs"])
        ends = R.month_ends(days)
        w = R.targets(closes, ends, 13, R.RULE)
        self.assertEqual(set(w), {"GLD", "SLV", "DBC"})
        self.assertAlmostEqual(sum(w.values()), 1.0)

    def test_absolute_filter_goes_to_cash_when_everything_falls(self):
        data = market(drifts={s: -0.001 for s in R.UNIVERSE})
        days, closes = R.align(data["etfs"])
        w = R.targets(closes, R.month_ends(days), 13, R.RULE)
        self.assertEqual(w, {})
        start, rets, _ = R.simulate(days, closes)
        self.assertTrue(all(r == 0 for r in rets))

    def test_needs_ten_eligible_etfs(self):
        etfs = market()["etfs"]
        for s in R.UNIVERSE[1:8]:
            etfs[s] = etfs[s][-100:]   # 7 late listings leave 9 eligible
        days, closes = R.align(etfs)
        self.assertIsNone(R.targets(closes, R.month_ends(days), 13, R.RULE))

    def test_weights_take_effect_a_day_after_the_month_end_and_costs_hit_turnover(self):
        days, closes = R.align(market()["etfs"])
        ends = R.month_ends(days)
        start, rets, _ = R.simulate(days, closes, cost_bps=10.0)
        self.assertEqual(start, ends[12]+1)
        _, free, _ = R.simulate(days, closes, cost_bps=0.0)
        self.assertLess(sum(rets), sum(free))
        # first day after the trade earns the picks' returns with no cost
        w = R.targets(closes, ends, 12, R.RULE)
        i = start+1
        exp = sum(v*(closes[s][i]/closes[s][i-1]-1) for s, v in w.items())
        self.assertAlmostEqual(free[0], exp, places=12)

    def test_full_evaluation_runs(self):
        out = R.evaluate(market(1400))
        self.assertIn(out["verdict"]["verdict"], ("PASS", "FAIL"))
        self.assertEqual(len(out["stress"]["neighbors"]), 9)
        self.assertGreater(out["rotation"]["cagr_pct"], out["spy"]["cagr_pct"])  # top drifters beat SPY's drift
        R.show(out)


if __name__ == "__main__":
    unittest.main()
