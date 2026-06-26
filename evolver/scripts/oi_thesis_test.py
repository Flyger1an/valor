"""Thesis test for oi_reversion: the gate must PASS a planted OI-surge-reversion edge and REJECT
noise (price & OI independent). Run with EVOLVER_USE_LLM=0 so it never hits the API."""
import os
import random
import sys
from collections import defaultdict

os.environ["EVOLVER_USE_LLM"] = "0"
sys.path.insert(0, os.path.dirname(__file__))
import research_tick as rt  # noqa: E402

BASE = 1_600_000_000_000
DAY = 86_400_000
FAM = next(f for f in rt.CRYPTO_FAMILIES if f["name"] == "oi_reversion")


def gen(seed, planted):
    """{coin:{ts:(close,oi)}}. planted=True injects reversion AFTER an OI-surge move."""
    rng = random.Random(seed)
    data = {}
    for ci in range(19):
        closes, ois, sched = [100.0 * (1 + 0.5 * ci)], [1e6], defaultdict(float)
        for t in range(1, 400):
            step = rng.gauss(0, 0.02) + (sched.get(t, 0.0) if planted else 0.0)
            closes.append(closes[-1] * (1 + step))
            oi_step = rng.gauss(0, 0.03) + (0.22 if rng.random() < 0.08 else 0.0)  # occasional OI surge
            ois.append(max(ois[-1] * (1 + oi_step), 1.0))
            if planted and t >= 5:
                ret = closes[t] / closes[t - 5] - 1
                doi = ois[t] / ois[t - 5] - 1
                if doi > 0.12 and abs(ret) > 0.05:        # leverage-surge move -> plant a reversion
                    d = -0.013 * (1 if ret > 0 else -1)
                    for k in range(1, 5):
                        sched[t + k] += d
        data[f"C{ci}"] = {BASE + i * DAY: (closes[i], ois[i]) for i in range(400)}
    return data


def run(label, planted):
    summ, cand = rt.cycle(FAM, gen(7, planted))
    verdict = "SURFACED" if cand else "rejected"
    print(f"  {label:28s} -> {verdict:9s} | {summ.split('] ', 1)[-1][:88]}")
    return bool(cand)


if __name__ == "__main__":
    print("oi_reversion gate (EVOLVER_USE_LLM=0):")
    planted = run("planted OI-surge reversion", True)
    noise = run("noise (price/OI independent)", False)
    print(f"\n{'PASS' if planted and not noise else 'FAIL'}: "
          f"gate {'passes the real edge AND rejects noise' if planted and not noise else 'MISBEHAVED'}")
