"""Thesis test for intraday_reversion: the gate must SURFACE a planted intraday reversion (large
lb-hour moves systematically revert) in the majority of cycles and REJECT a pure random walk every
time. Validates the family + gate are SOUND (separate from whether REAL hourly crypto shows the edge
net of the harsh intraday fee wall). EVOLVER_USE_LLM=0 -> no API. Surface-rate over K cycles."""
import math
import os
import random
import sys

os.environ["EVOLVER_USE_LLM"] = "0"
sys.path.insert(0, os.path.dirname(__file__))
import research_tick as rt  # noqa: E402
from evolver.optimize.intraday_reversion import run_intraday_reversion as RIR  # noqa: E402

BASE, H = 1_600_000_000_000, 3_600_000
# same capped space as the production family (entry_z<=1.6 so no genome starves the holdout below min_n)
SPACE = {"lookback": (3.0, 8.0, int), "entry_z": (1.0, 1.6, float),
         "hold_hours": (2.0, 6.0, int), "vol_window": (72.0, 240.0, int)}
FAM = {"name": "intraday_reversion", "refresh": None, "bt": RIR, "space": SPACE, "fee": 5.0,
       "slip": 6.0, "stab": ("lookback", "entry_z", "hold_hours"), "min_cov": 24 * 30, "min_n": 30}


def gen(seed, planted):
    """{coin:{ts:(o,h,l,c)}}, 2600 hourly bars, 6 coins. planted -> each hour pulls back a fraction of
    the trailing 6h move (a fade-able reversion edge, net of ~0.9% hourly noise); noise -> random walk."""
    rng = random.Random(seed)
    data = {}
    for ci in range(8):                                 # 8 coins x 5000 bars -> holdout clears min_n=30
        S = 100.0 * (1 + ci)
        prices = [S]
        rows = {}
        for h in range(5000):
            r = rng.gauss(0, 0.009)
            if planted and h >= 6 and prices[-7] > 0:
                move = prices[-1] / prices[-7] - 1
                r += -0.22 * move                       # revert ~22% of the 6h move each hour
            S *= math.exp(r)
            prices.append(S)
            rows[BASE + h * H] = (S, S, S, S)           # (o,h,l,c) — only close is used
        data[f"C{ci}"] = rows
    return data


def surface_rate(planted, k):
    return sum(bool(rt.cycle(FAM, gen(21 + i, planted))[1]) for i in range(k))


if __name__ == "__main__":
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(f"intraday_reversion gate — surface rate over {K} cycles (EVOLVER_USE_LLM=0):")
    p = surface_rate(True, K)
    n = surface_rate(False, K)
    print(f"  planted reversion : {p}/{K} surfaced")
    print(f"  noise (rand walk) : {n}/{K} surfaced")
    ok = p >= max(1, K // 2) and n == 0
    print(f"\n{'PASS' if ok else 'FAIL'}: {'family+gate SOUND (surfaces a real intraday edge, never noise)' if ok else 'MISBEHAVED'}")
