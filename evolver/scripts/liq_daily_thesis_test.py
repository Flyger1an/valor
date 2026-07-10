"""Thesis test for liq_print_daily (the Coinalyze-backed daily forced-flow family): the gate must
SURFACE a planted daily liquidation-reversion (big by-side liq days followed by a fade-able
rebound) in the majority of cycles, and REJECT spike-but-no-edge noise every time.
EVOLVER_USE_LLM=0 -> no API. Surface-rate over K cycles, same methodology as every family."""
import math
import os
import random
import sys

os.environ["EVOLVER_USE_LLM"] = "0"
sys.path.insert(0, os.path.dirname(__file__))
import research_tick as rt  # noqa: E402

BASE, D = 1_600_000_000_000, 86_400_000
FAM = {"name": "liq_print_daily", "refresh": None, "bt": rt.run_liq_print_daily,
       "space": rt.SPACE_LIQ_DAILY, "fee": 8.0, "slip": 24.0,
       "stab": ("liq_mult", "hold_hours", "lookback"), "min_cov": 400, "min_n": 12}


def gen(seed, planted):
    """{coin:{day:(close, ll, sl)}}, 1200 daily bars, 6 coins. Baseline daily liq ~1M USD; ~1-in-25
    days a 25x one-sided cascade. planted -> the 2 days AFTER a cascade drift AGAINST the forced
    flow (+~2.6%, comfortably above the ~0.8% round-trip cost); noise -> cascades with no edge."""
    rng = random.Random(seed)
    data = {}
    for ci in range(6):
        S, rows, drift_left, drift_dir = 100.0 * (1 + ci), {}, 0, 0.0
        for d in range(1200):
            r = rng.gauss(0, 0.02)
            if planted and drift_left > 0:
                r += drift_dir * 0.013
                drift_left -= 1
            S *= math.exp(r)
            ll = sl = rng.uniform(0.2e6, 1.8e6)
            if rng.random() < 0.04:
                if rng.random() < 0.5:
                    ll = rng.uniform(20e6, 40e6)             # longs liquidated -> forced sells
                    drift_dir = +1.0                          # -> planted rebound UP
                else:
                    sl = rng.uniform(20e6, 40e6)
                    drift_dir = -1.0
                drift_left = 2 if planted else 0
            rows[BASE + d * D] = (S, ll, sl)
        data[f"C{ci}"] = rows
    return data


def surface_rate(planted, k):
    return sum(bool(rt.cycle(FAM, gen(31 + i, planted))[1]) for i in range(k))


if __name__ == "__main__":
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"liq_print_daily gate — surface rate over {K} cycles (EVOLVER_USE_LLM=0):")
    p = surface_rate(True, K)
    n = surface_rate(False, K)
    print(f"  planted daily liq-reversion : {p}/{K} surfaced")
    print(f"  noise (cascades, no edge)   : {n}/{K} surfaced")
    ok = p >= max(1, K // 2) and n == 0
    print(f"\n{'PASS' if ok else 'FAIL'}: {'family+gate SOUND' if ok else 'MISBEHAVED'}")
