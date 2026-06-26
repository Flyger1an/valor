"""Thesis test for oi_reversion. The gate's search seed is time-based (diversity across cycles), and
production requires CONFIRM=2 separate passes — so a single cycle is a noisy draw. This tests the
SURFACE RATE over K cycles (varying the data draw): a real OI-surge-reversion edge should surface in
the majority, noise in NONE. EVOLVER_USE_LLM=0 -> never hits the API."""
import os
import random
import sys
from collections import defaultdict

os.environ["EVOLVER_USE_LLM"] = "0"
sys.path.insert(0, os.path.dirname(__file__))
import research_tick as rt  # noqa: E402

BASE, DAY = 1_600_000_000_000, 86_400_000
FAM = next(f for f in rt.CRYPTO_FAMILIES if f["name"] == "oi_reversion")


def gen(seed, planted):
    """{coin:{ts:(close,oi)}}, 700 daily bars. planted -> a reversion is injected AFTER an OI-surge
    move (fresh leverage = fragile); noise -> price & OI independent random walks."""
    rng = random.Random(seed)
    data = {}
    for ci in range(19):
        closes, ois, sched = [100.0 * (1 + 0.5 * ci)], [1e6], defaultdict(float)
        for t in range(1, 700):
            step = rng.gauss(0, 0.02) + (sched.get(t, 0.0) if planted else 0.0)
            closes.append(closes[-1] * (1 + step))
            ois.append(max(ois[-1] * (1 + rng.gauss(0, 0.03) + (0.22 if rng.random() < 0.10 else 0.0)), 1.0))
            if planted and t >= 5:
                ret = closes[t] / closes[t - 5] - 1
                doi = ois[t] / ois[t - 5] - 1
                if doi > 0.12 and abs(ret) > 0.05:        # leverage-surge move -> plant a reversion
                    d = -0.013 * (1 if ret > 0 else -1)
                    for k in range(1, 5):
                        sched[t + k] += d
        data[f"C{ci}"] = {BASE + i * DAY: (closes[i], ois[i]) for i in range(700)}
    return data


def surface_rate(planted, k):
    return sum(bool(rt.cycle(FAM, gen(7 + i, planted))[1]) for i in range(k))


if __name__ == "__main__":
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(f"oi_reversion gate — surface rate over {K} cycles (EVOLVER_USE_LLM=0):")
    p = surface_rate(True, K)
    n = surface_rate(False, K)
    print(f"  planted OI-surge reversion : {p}/{K} surfaced")
    print(f"  noise (price/OI independent): {n}/{K} surfaced")
    ok = p >= max(1, K // 2) and n == 0
    print(f"\n{'PASS' if ok else 'FAIL'}: gate {'surfaces the real edge in the majority AND never on noise' if ok else 'MISBEHAVED'}")
