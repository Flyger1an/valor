import json
import math
import random
import unittest

from evolver.trading import henry_lab as L
from evolver.trading import henry_tradfi as T

DAY = 86400
START = 946684800  # 2000-01-01


def walk(n, drift, vol, seed, start=START, p0=100.0):
    rng = random.Random(seed)
    bars, p = [], p0
    for k in range(n):
        o = p
        p = p*math.exp(drift+rng.gauss(0, vol))
        hi, lo = max(o, p)*1.003, min(o, p)*0.997
        bars.append([start+k*DAY, o, hi, lo, p, 1e6])
    return bars


def regime(n, seed, start=START):
    """Up, crash, up again: a trend rule should keep less of the crash than holding."""
    a = walk(n//3, 0.0012, 0.008, seed, start)
    b = walk(n//3, -0.003, 0.015, seed+1, a[-1][0]+DAY, a[-1][4])
    c = walk(n-2*(n//3), 0.0012, 0.008, seed+2, b[-1][0]+DAY, b[-1][4])
    return a+b+c


class ParseTests(unittest.TestCase):
    def test_yahoo_scales_to_adjusted_close_and_skips_gaps(self):
        raw = json.dumps({"chart": {"result": [{
            "timestamp": [START+13*3600, START+DAY+13*3600, START+2*DAY+13*3600],
            "indicators": {"quote": [{"open": [10, None, 12], "high": [11, 12, 13], "low": [9, 10, 11],
                                      "close": [10, 11, 12], "volume": [5, 6, None]}],
                           "adjclose": [{"adjclose": [5, 5.5, 6]}]}}]}})
        bars = T.parse_yahoo(raw)
        self.assertEqual([b[0] for b in bars], [START, START+2*DAY])  # floored to UTC day, None row dropped
        self.assertAlmostEqual(bars[0][4], 5.0)
        self.assertAlmostEqual(bars[0][1], 5.0)
        self.assertEqual(bars[1][5], 0.0)
        raw_bars = T.parse_yahoo(raw, adjust=False)
        self.assertAlmostEqual(raw_bars[0][4], 10.0)

    def test_ecb_builds_usd_crosses(self):
        csv_text = "Date,USD,JPY,GBP,AUD,CAD,CHF,NZD,\n2020-01-02,1.2,130,0.8,1.6,1.5,1.1,1.7,\n2020-01-03,N/A,,,,,,,\n"
        fx = T.parse_ecb(csv_text)
        self.assertAlmostEqual(fx["EURUSD"][0][4], 1.2)
        self.assertAlmostEqual(fx["USDJPY"][0][4], 130/1.2)
        self.assertAlmostEqual(fx["GBPUSD"][0][4], 1.2/0.8)
        self.assertAlmostEqual(fx["GBPJPY"][0][4], 130/0.8)
        self.assertAlmostEqual(fx["AUDUSD"][0][4], 1.2/1.6)
        self.assertAlmostEqual(fx["USDCHF"][0][4], 1.1/1.2)
        self.assertEqual(len(fx["EURUSD"]), 1)  # N/A day skipped


class EvalTests(unittest.TestCase):
    def test_window_compounds_inside_dates_only(self):
        t0 = T.dt.datetime(2020, 1, 1, tzinfo=T.dt.timezone.utc).timestamp()
        series = [(t0+k*DAY, 0.1) for k in range(5)]
        self.assertEqual(T.window(series, "2020-01-01", "2020-01-03"), 21.0)
        self.assertIsNone(T.window(series, "2019-01-01", "2019-06-01"))  # before the data: missing, not 0%
        self.assertIsNone(T.window(series, "2019-12-01", "2020-01-03"))  # starts before the data: partial

    def test_costs_and_funding_are_restored_and_shorts_earn_no_funding(self):
        saved = (L.FEE_BPS, L.DEFAULT_FUNDING_8H)
        bars = walk(400, -0.002, 0.005, 3)
        r = T.run_symbol(T.RULE_FX, "EURUSD", bars, {"fee_bps": 0, "slip_bps": 0, "spread_bps": 0})
        self.assertEqual((L.FEE_BPS, L.DEFAULT_FUNDING_8H), saved)
        # a short with zero costs earns exactly -pos * price change, nothing extra
        pos, rets = r["pos"], r["rule"]
        w = r["warm"]
        for i in range(w+2, len(bars)):
            if pos[i-1] < 0 and pos[i] == pos[i-1]:
                exp = pos[i-1]*(bars[i][4]/bars[i-1][4]-1)
                self.assertAlmostEqual(rets[i-w-1], exp, places=12)
                break
        else:
            self.fail("no held short found")

    def test_fx_rule_shorts_downtrends(self):
        r = T.run_symbol(T.RULE_FX, "X", walk(600, -0.002, 0.004, 5), T.FX_COSTS)
        self.assertTrue(any(p < 0 for p in r["pos"]))
        self.assertGreater(T.metrics(r["rule"], 260)["return_pct"], 0)

    def test_stock_rule_never_shorts_and_spy_filter_blocks_entries(self):
        bars = walk(600, 0.002, 0.006, 7)
        bear = walk(600, -0.002, 0.006, 8)
        free = T.run_symbol(dict(T.RULE_STOCKS, btc_filter=False), "QQQ", bars, T.STOCK_COSTS)
        gated = T.run_symbol(T.RULE_STOCKS, "QQQ", bars, T.STOCK_COSTS, L.Leader(bear))
        self.assertTrue(all(p >= 0 for p in free["pos"]))
        self.assertTrue(any(p > 0 for p in free["pos"]))
        self.assertTrue(all(p == 0 for p in gated["pos"]))

    def test_full_run_on_synthetic_markets(self):
        data = {"indexes": {"S&P 500": regime(1500, 1), "Nikkei 225": regime(1500, 2)},
                "etfs": {"SPY": regime(1200, 3), "QQQ": regime(1200, 4), "TLT": walk(1200, 0.0002, 0.006, 5)},
                "fx": {"EURUSD": regime(900, 6), "USDJPY": walk(900, -0.001, 0.005, 7)}, "fx_source": "test"}
        stocks, fx = T.run_stocks(data), T.run_fx(data)
        self.assertIn(stocks["verdict"]["verdict"], ("PASS", "FAIL"))
        self.assertIn("drawdown_below_hold_everywhere", stocks["verdict"]["checks"])
        self.assertEqual(set(stocks["crashes"]), set(T.CRASHES))
        self.assertIn("nikkei_1990_bust", stocks)
        self.assertIsNone(stocks["crashes"]["1987 crash"]["rule_pct"])
        self.assertIsNotNone(stocks["crashes"]["Dot-com bust"]["rule_pct"])
        self.assertIn("of 1 covered crashes", stocks["verdict"]["checks"]["crashes_contained"]["detail"])
        sp = stocks["datasets"]["S&P 500 index"]
        self.assertLess(sp["rule"]["max_drawdown_pct"], sp["hold"]["max_drawdown_pct"])
        self.assertEqual(set(fx["verdict"]["checks"]), {"portfolio_sharpe", "most_pairs_profitable", "drawdown",
                                                        "survives_a_day_of_lag", "survives_double_costs",
                                                        "neighbors_agree", "bootstrap"})
        T.show(stocks, fx, {"x": "unavailable"})

    def test_no_fx_data_is_reported_not_crashed(self):
        self.assertEqual(T.run_fx({"fx": {}})["verdict"]["verdict"], "NO DATA")


if __name__ == "__main__":
    unittest.main()
