"""Thesis test for liquidation_print (surface rate over K cycles, like oi/funding_carry). Plants real
liquidation cascades: a spike in liquidation notional on one side WITH a price overshoot in that
direction, then a reversion the strategy fades. Long-liq (forced sells) -> down overshoot -> fade UP;
short-liq -> up overshoot -> fade DOWN. Noise: only small random liquidations, no cascades.
EVOLVER_USE_LLM=0 -> never hits the API."""
import os
import random
import sys
from collections import defaultdict

os.environ["EVOLVER_USE_LLM"] = "0"
sys.path.insert(0, os.path.dirname(__file__))
import research_tick as rt  # noqa: E402

BASE, HOUR = 1_600_000_000_000, 3_600_000
FAM = next(f for f in rt.CRYPTO_FAMILIES if f["name"] == "liquidation_print")


def gen(seed, planted):
    """{coin:{ts:(close, long_liq, short_liq)}}, 3000 hourly bars."""
    rng = random.Random(seed)
    data = {}
    for ci in range(19):
        cl, lla, sla, sched = [100.0 * (1 + 0.5 * ci)], [0.0], [0.0], defaultdict(float)
        for t in range(1, 3000):
            step = rng.gauss(0, 0.012) + sched.get(t, 0.0)
            ll, sl = abs(rng.gauss(0, 1e5)), abs(rng.gauss(0, 1e5))   # baseline small liquidations
            if planted and rng.random() < 0.02:                       # 2% of hours: a real cascade
                H, rev = 8, rng.uniform(0.02, 0.035)
                if rng.random() < 0.5:                                # long-liq: down overshoot, revert up
                    ll += rng.uniform(2e6, 6e6)
                    step -= rng.uniform(0.03, 0.06)
                    for k in range(1, H + 1):
                        sched[t + k] += rev / H
                else:                                                 # short-liq: up overshoot, revert down
                    sl += rng.uniform(2e6, 6e6)
                    step += rng.uniform(0.03, 0.06)
                    for k in range(1, H + 1):
                        sched[t + k] -= rev / H
            cl.append(cl[-1] * (1 + step))
            lla.append(ll)
            sla.append(sl)
        data[f"C{ci}"] = {BASE + i * HOUR: (cl[i], lla[i], sla[i]) for i in range(3000)}
    return data


def surface_rate(planted, k):
    return sum(bool(rt.cycle(FAM, gen(7 + i, planted))[1]) for i in range(k))


if __name__ == "__main__":
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(f"liquidation_print gate — surface rate over {K} cycles (EVOLVER_USE_LLM=0):")
    p = surface_rate(True, K)
    n = surface_rate(False, K)
    print(f"  planted liquidation cascades: {p}/{K} surfaced")
    print(f"  noise (small random liq)    : {n}/{K} surfaced")
    ok = p >= max(1, K // 2) and n == 0
    print(f"\n{'PASS' if ok else 'FAIL'}: gate {'fades real cascades in the majority AND never on noise' if ok else 'MISBEHAVED'}")
