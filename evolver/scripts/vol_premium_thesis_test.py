"""Thesis test for vol_premium (Phase 3): the gate must SURFACE a planted STRONG variance premium
(implied vol persistently rich vs realized) in the majority of cycles, and REJECT noise (implied =
realized) every time. This validates the family + gate are SOUND — which is separate from whether the
REAL Deribit vol premium clears (it does NOT, net of frictions + through the gate's holdout/multiplicity/
2x-cost/PBO — see docs/roadmap-vol-premium.md). EVOLVER_USE_LLM=0 -> no API. Surface-rate over K cycles,
the same methodology as the spot families (the gate's evolve seed is time-based; CONFIRM needs repeats)."""
import math
import os
import random
import sys

os.environ["EVOLVER_USE_LLM"] = "0"
sys.path.insert(0, os.path.dirname(__file__))
import research_tick as rt  # noqa: E402
from evolver.optimize.vol_premium import run_vol_premium as RVP  # noqa: E402

BASE, DAY = 1_600_000_000_000, 86_400_000
# capped space — no n-starvation: tenor<=14, iv_rank<=0.5 keep enough holdout trades to clear min_n
SPACE = {"tenor_days": (5.0, 14.0, int), "iv_rank_min": (0.0, 0.5, float), "lookback": (30.0, 90.0, int)}
FAM = {"name": "vol_premium", "refresh": None, "bt": RVP, "space": SPACE, "fee": 5.0, "slip": 10.0,
       "stab": ("tenor_days", "iv_rank_min", "lookback"), "min_cov": 200, "min_n": 20}


def gen(seed, planted):
    """{coin:{ts:(close, dvol)}}, 400 daily bars, 4 coins. planted -> realized vol LOW (30%) while
    implied DVOL is RICH (~80) => a fat collectible premium; noise -> implied == realized (~50)."""
    rng = random.Random(seed)
    data = {}
    for ci in range(4):
        S, rows = 100.0 * (1 + ci), {}
        for d in range(400):
            rv = 0.30 if planted else 0.50
            S *= math.exp(rng.gauss(0, rv / math.sqrt(365)))
            dvol = (80 + rng.gauss(0, 3)) if planted else (50 + rng.gauss(0, 5))
            rows[BASE + d * DAY] = (S, dvol)
        data[f"C{ci}"] = rows
    return data


def surface_rate(planted, k):
    return sum(bool(rt.cycle(FAM, gen(7 + i, planted))[1]) for i in range(k))


if __name__ == "__main__":
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(f"vol_premium gate — surface rate over {K} cycles (EVOLVER_USE_LLM=0):")
    p = surface_rate(True, K)
    n = surface_rate(False, K)
    print(f"  planted strong premium  : {p}/{K} surfaced")
    print(f"  noise (implied=realized): {n}/{K} surfaced")
    ok = p >= max(1, K // 2) and n == 0
    print(f"\n{'PASS' if ok else 'FAIL'}: {'family+gate SOUND (surfaces a real premium, never noise)' if ok else 'MISBEHAVED'}")
