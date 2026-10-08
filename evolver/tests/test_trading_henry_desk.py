import copy
import io
import json
import math
import random
import unittest

from evolver.trading import henry_desk as hd
from evolver.trading import henry_desk_data as data_mod
from evolver.trading import henry_desk_replay as rp
from evolver.trading.henry_desk_reviewer import LLMReviewer

START = 1_780_000_000 - 1_780_000_000 % 3600


def market(seed=11, days=120, drift=0.0012, phases=("bull", "range", "bear", "range"), symbols=None):
    """Synthetic hourly market: BTC-led regimes, alts with higher beta, regime-dependent funding."""
    random.seed(seed)
    n = days*24
    seg = n//len(phases)
    syms = symbols or {"BTC-USD": (60000, 1.0), "ETH-USD": (3000, 1.2), "WIF-USD": (2.0, 1.8)}
    shocks = [(phases[min(k//seg, len(phases)-1)], random.gauss(0, 0.006)) for k in range(n)]
    out = {}
    for sym, (p0, beta) in syms.items():
        p, anchor, bars, fund = p0, p0, [], []
        for k, (name, r) in enumerate(shocks):
            t = START+k*3600
            d = {"bull": drift, "bear": -drift}.get(name, 0.0)
            ret = beta*(d+r)+random.gauss(0, 0.004*beta)
            if name == "range":
                ret += -0.02*math.log(p/anchor)
            else:
                anchor = p
            o = p
            p = p*math.exp(ret)
            hi, lo = max(o, p)*(1+abs(random.gauss(0, 0.003))), min(o, p)*(1-abs(random.gauss(0, 0.003)))
            bars.append([t, o, hi, lo, p, random.lognormvariate(10, 0.4)*(1+abs(ret)/0.006)])
            if t % 28800 == 0:
                fund.append([t, {"bull": 0.0003, "bear": -0.0002}.get(name, 0.00005)+random.gauss(0, 0.0001)])
        out[sym] = {"bars": bars, "funding": fund}
    return rp.series_from({"symbols": out})


class FeaturesTest(unittest.TestCase):
    def test_higher_timeframe_bar_counts_only_when_complete(self):
        times = [START+k*3600 for k in range(10)]
        ones = [1.0]*10
        closes, done = hd.aggregate(times, ones, ones, ones, list(range(10)), ones, hd.H4)
        # hours 0-3 form the first 4h bar; it is complete at the close of hour 3, not before
        self.assertEqual(done[:5], [-1, -1, -1, 0, 0])
        self.assertEqual(closes[0], 3)

    def test_analysis_never_sees_the_future(self):
        s = market(days=60)
        base = s["ETH-USD"]
        t = base.t[900]
        before = hd.Desk(s).analyze("ETH-USD", t)[1]
        future = copy.deepcopy(s)
        f = future["ETH-USD"]
        for k in range(901, len(f.c)):  # rewrite everything after hour 900
            f.c[k] *= 3
            f.h[k] *= 3
            f.l[k] *= 3
            f.o[k] *= 3
        rebuilt = {k: hd.Series(k, v.t, v.o, v.h, v.l, v.c, v.v, v.funding_t, v.funding_r) for k, v in future.items()}
        after = hd.Desk(rebuilt).analyze("ETH-USD", t)[1]
        self.assertEqual(json.dumps(before, sort_keys=True, default=str), json.dumps(after, sort_keys=True, default=str))


class AnalystTest(unittest.TestCase):
    def setUp(self):
        self.s = market(days=120)
        self.desk = hd.Desk(self.s)
        self.seg = len(self.s["BTC-USD"].t)//4

    def label_at(self, k):
        btc = self.s["BTC-USD"]
        return hd.analyst_regime(btc, k)["label"]

    def test_regimes_are_recognized(self):
        seg = self.seg
        share = lambda lo, hi, lab: sum(self.label_at(k) == lab for k in range(lo, hi))/(hi-lo)  # noqa: E731
        self.assertGreater(share(seg//2, seg, "bull"), 0.6)
        self.assertGreater(share(2*seg+seg//2, 3*seg, "bear"), 0.35)
        self.assertLess(share(2*seg+seg//2, 3*seg, "bull"), 0.02, "a bear leg is never read as bull")
        self.assertLess(share(seg//2, seg, "bear"), 0.02, "a bull leg is never read as bear")
        self.assertGreater(share(3*seg+seg//2, 4*seg, "range"), 0.4)

    def test_every_thesis_carries_its_evidence_and_checks(self):
        found = None
        for t in self.s["BTC-USD"].t[200::7]:
            ths = self.desk.theses(t)
            if ths:
                found = ths[0]
                break
        self.assertIsNotNone(found)
        for key in ("regime", "structure", "volatility", "positioning", "cross_asset", "execution"):
            self.assertIn(key, found["evidence"])
        for key in ("reward_to_risk", "pays_for_costs", "conviction", "stop_on_correct_side", "stop_wide_enough"):
            self.assertIn(key, found["checks"])
        self.assertEqual(found["approved_by_desk"], all(c["pass"] for c in found["checks"].values()))

    def test_bear_playbook_only_shorts_and_labels_them_simulated(self):
        seg = self.seg
        shorts = longs = 0
        for t in self.s["BTC-USD"].t[2*seg+seg//2:3*seg]:
            for th in self.desk.theses(t):
                if th["regime"] == "bear":
                    longs += th["direction"] == "long"
                    shorts += th["direction"] == "short"
                    if th["direction"] == "short":
                        self.assertEqual(th["execution"], "simulated_perp")
        self.assertGreater(shorts, 0)
        self.assertEqual(longs, 0)

    def test_alt_longs_blocked_when_btc_is_bear(self):
        th = {"symbol": "ETH-USD"}
        s = self.s["ETH-USD"]
        i = 300
        notes = self.desk.analyze("ETH-USD", s.t[i])[1]
        setup = ("breakout_long", 1, s.c[i]*0.97, s.c[i]*1.2, True, "test")
        th = hd.write_thesis(s, i, notes, setup, "bear")
        self.assertFalse(th["checks"]["market_alignment"]["pass"])
        self.assertFalse(th["approved_by_desk"])


class BookTest(unittest.TestCase):
    def thesis(self, direction="short", entry=100.0, stop=104.0, target=90.0, runner=False, sym="BTC-USD"):
        return {"symbol": sym, "direction": direction, "execution": "spot" if direction == "long" else "simulated_perp",
                "entry_ref": entry, "stop": stop, "target": target, "runner": runner, "conviction": 70.0,
                "conviction_parts": {}, "reward_to_risk": 2.5, "expected_move_pct": 10, "setup": "test", "regime": "bear",
                "reason": "test"}

    def test_short_profits_when_price_falls_and_receives_positive_funding(self):
        b = hd.Book()
        pos = b.open(self.thesis(), 100.0, START, {"BTC-USD": 100.0})
        self.assertIsNotNone(pos)
        b.apply_funding("BTC-USD", 0.001, 100.0)
        self.assertGreater(pos["funding"], 0)
        b.manage(pos, (95, 96, 89, 90), START+3600, "bear", 1.0)
        t = b.trades[-1]
        self.assertEqual(t["reason"], "target")
        self.assertGreater(t["pnl_usd"], 0)
        self.assertGreater(t["funding_usd"], 0)
        self.assertAlmostEqual(b.equity({}), b.cash)

    def test_stop_assumed_first_when_bar_hits_both(self):
        b = hd.Book()
        pos = b.open(self.thesis(direction="long", stop=96.0, target=110.0), 100.0, START, {"BTC-USD": 100.0})
        b.manage(pos, (100, 111, 95, 105), START+3600, "bull", 1.0)
        self.assertEqual(b.trades[-1]["reason"], "stop")
        self.assertLess(b.trades[-1]["pnl_usd"], 0)

    def test_gap_through_stop_before_fill_rejects_the_trade(self):
        b = hd.Book()
        self.assertIsNone(b.open(self.thesis(direction="long", stop=96.0, target=110.0), 95.0, START, {"BTC-USD": 95.0}))
        self.assertEqual(b.rejected, 1)

    def test_risk_sizing_and_no_leverage(self):
        b = hd.Book()
        pos = b.open(self.thesis(direction="long", stop=99.0, target=110.0), 100.0, START, {"BTC-USD": 100.0})
        self.assertLessEqual(pos["qty"]*pos["entry"], 500)  # capped by cash, not 2.5%/1% = 1250 notional
        b2 = hd.Book()
        pos2 = b2.open(self.thesis(direction="long", stop=80.0, target=150.0), 100.0, START, {"BTC-USD": 100.0})
        risk_usd = pos2["qty"]*(pos2["entry"]-80.0)
        self.assertLess(risk_usd, 500*hd.DESK_RULES["risk_per_trade"]*1.3)

    def test_runner_trails_past_its_target(self):
        b = hd.Book()
        pos = b.open(self.thesis(direction="long", stop=98.0, target=104.0, runner=True), 100.0, START, {"BTC-USD": 100.0})
        b.manage(pos, (100, 105, 100, 105), START+3600, "bull", 0.5)
        self.assertIn(pos, b.positions, "runner keeps going past the target")
        self.assertGreater(pos["stop"], 100.0, "and its stop has moved into profit")

    def test_performance_throttle_stands_down_after_a_bad_streak(self):
        b = hd.Book()
        for k in range(12):
            b.trades.append({"r_multiple": -1.0, "closed": START+k*3600})
        self.assertTrue(b.standing_down(START+13*3600))
        self.assertEqual(b.pauses, 1)
        self.assertTrue(b.standing_down(START+20*3600), "pause lasts")
        self.assertFalse(b.standing_down(START+13*3600+hd.DESK_RULES["performance_throttle"]["pause_hours"]*3600+1)
                         and b.pauses > 1)


class ReplayTest(unittest.TestCase):
    def test_trending_market_passes_and_noise_fails(self):
        trend = rp.replay(market(seed=11, days=150))
        self.assertGreater(trend["return_pct"], 0)
        self.assertIn("bear", trend["by_market_regime"])
        noise = rp.replay(market(seed=3, days=150, drift=0.0))
        self.assertEqual(noise["gate"]["verdict"], "FAIL", "a desk that passes on noise is fooling itself")
        self.assertLess(noise["max_drawdown_pct"], 30, "the risk manager contains damage when there is no edge")

    def test_report_has_attribution_and_halves(self):
        r = rp.replay(market(seed=11, days=90))
        self.assertEqual(set(r["analyst_attribution"]), set(hd.DESK_RULES["weights"]))
        self.assertEqual(len(r["halves_return_pct"]), 2)
        self.assertIn(r["gate"]["verdict"], ("PASS", "FAIL"))

    def test_sweep_restores_rules(self):
        before = json.dumps(hd.DESK_RULES, sort_keys=True)
        rp.SWEEP_BACKUP = rp.SWEEP
        try:
            rp.SWEEP = {"conviction_min": [60.0], "rr_min": [2.0]}
            rows = rp.sweep(market(seed=11, days=60))
        finally:
            rp.SWEEP = rp.SWEEP_BACKUP
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.dumps(hd.DESK_RULES, sort_keys=True), before)


class DataTest(unittest.TestCase):
    def test_kline_rows_accept_millisecond_and_microsecond_stamps(self):
        out = {}
        data_mod._add(out, [["1767225600000", "1", "2", "0.5", "1.5", "10"],
                            ["1767229200000000", "1.5", "2", "1", "1.8", "12"], ["open_time", "x"]])
        self.assertEqual(sorted(out), [1767225600, 1767229200])

    def test_months_back_includes_current_month_last(self):
        import datetime as dt
        ms = data_mod.months_back(2, dt.datetime(2026, 1, 15, tzinfo=dt.timezone.utc))
        self.assertEqual(ms, ["2025-11", "2025-12", "2026-01"])


class ReviewerTest(unittest.TestCase):
    def thesis(self):
        return {"t": START, "symbol": "BTC-USD", "setup": "breakout_long", "direction": "long", "evidence": {}}

    def test_disabled_reviewer_abstains_and_approves(self):
        r = LLMReviewer()
        r.enabled = False
        self.assertEqual(r(self.thesis())[0], True)
        self.assertTrue(r.log[-1]["abstained"])

    def test_veto_is_honored_and_logged(self):
        class Resp:
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body
        answer = {"choices": [{"message": {"content": json.dumps({"approve": False, "reason": "funding crowded and regime fading",
                                                                   "concerns": ["crowded"]})}}]}
        r = LLMReviewer(opener=lambda req, timeout: Resp(json.dumps(answer).encode()))
        r.enabled = True
        ok, why = r(self.thesis())
        self.assertFalse(ok)
        self.assertIn("crowded", why)

    def test_broken_reviewer_never_blocks_the_desk(self):
        def boom(req, timeout):
            raise TimeoutError()
        r = LLMReviewer(opener=boom)
        r.enabled = True
        self.assertEqual(r(self.thesis())[0], True)
        self.assertIn("unavailable", r.log[-1]["reason"])


if __name__ == "__main__":
    unittest.main()
