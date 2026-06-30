"""Thesis test for options_pin (#5): the gate must SURFACE a planted max-pain pull (spot drifts toward
max-pain as a near expiry approaches) in the majority of cycles, and REJECT noise (max-pain unrelated to
where spot goes) every time. Validates the family + gate are SOUND — the REAL verdict needs weeks of
forward-accumulated Deribit snapshots (no historical OI-by-strike). EVOLVER_USE_LLM=0 -> no API."""
import math
import os
import random
import sys

os.environ["EVOLVER_USE_LLM"] = "0"
sys.path.insert(0, os.path.dirname(__file__))
import research_tick as rt  # noqa: E402
from evolver.optimize.options_flow import run_options_pin as ROP  # noqa: E402

BASE, DAY = 1_600_000_000_000, 86_400_000
SPACE = {"gap_min": (0.01, 0.05, float), "dte_max": (5.0, 21.0, float),
         "hold_days": (2.0, 10.0, int), "trade_dir": (-1.0, 1.0, float)}
FAM = {"name": "options_pin", "refresh": None, "bt": ROP, "space": SPACE, "fee": 5.0, "slip": 8.0,
       "stab": ("gap_min", "dte_max", "hold_days"), "min_cov": 120, "min_n": 20, "min_coins": 2}


def gen(seed, planted):
    """{coin:{day:(spot, {max_pain,dte,...})}}, 500 daily bars, 4 coins. An expiry every ~20 days; if
    planted, spot is pulled toward that expiry's max-pain when dte<=7; if noise, pure random walk."""
    rng = random.Random(seed)
    data = {}
    for ci in range(4):
        S = 100.0 * (1 + ci)
        mp = S * (1 + rng.uniform(-0.05, 0.05))
        dte, rows = 20, {}
        for d in range(500):
            if dte <= 0:                                   # roll to a fresh expiry + new pin
                dte = 20
                mp = S * (1 + rng.uniform(-0.06, 0.06))
            drift = 0.15 * (mp - S) / S if (planted and dte <= 7) else 0.0   # pin pull near expiry
            S *= math.exp(rng.gauss(drift, 0.02))
            rows[BASE + d * DAY] = (S, {"max_pain": mp, "oi_wall": mp, "total_oi": 1e4,
                                        "pcr": 1.0, "dte": float(dte)})
            dte -= 1
        data[f"C{ci}"] = rows
    return data


def surface_rate(planted, k):
    return sum(bool(rt.cycle(FAM, gen(11 + i, planted))[1]) for i in range(k))


if __name__ == "__main__":
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(f"options_pin gate — surface rate over {K} cycles (EVOLVER_USE_LLM=0):")
    p = surface_rate(True, K)
    n = surface_rate(False, K)
    print(f"  planted max-pain pull : {p}/{K} surfaced")
    print(f"  noise (random walk)   : {n}/{K} surfaced")
    ok = p >= max(1, K // 2) and n == 0
    print(f"\n{'PASS' if ok else 'FAIL'}: {'family+gate SOUND (surfaces a real pin, never noise)' if ok else 'MISBEHAVED'}")
