"""Thesis test for funding_carry. Like oi_thesis_test, the gate's search seed is time-based and
production requires CONFIRM=2 — so this tests the SURFACE RATE over K cycles. The planted edge is a
persistent per-coin funding the strategy collects (price is pure noise in BOTH, so the only edge is the
funding P&L); noise is funding random each day (nothing to rank). EVOLVER_USE_LLM=0 -> no API."""
import os
import random
import sys

os.environ["EVOLVER_USE_LLM"] = "0"
sys.path.insert(0, os.path.dirname(__file__))
import research_tick as rt  # noqa: E402

BASE, EIGHTH = 1_600_000_000_000, 28_800_000   # 8h grid (funding's natural cadence)
FAM = next(f for f in rt.CRYPTO_FAMILIES if f["name"] == "funding_carry")


def gen(seed, planted):
    """{coin:{ts:(close, 8h_funding)}}, 400 8h-bars (~130 days = the depth OKX's 95d accrues to in a
    few months). planted -> persistent per-coin funding in a STRONG-carry regime (the regime this
    low-Sharpe factor is designed to catch); noise -> funding random each bar. Same price walk."""
    rng = random.Random(seed)
    data = {}
    for ci in range(19):
        base_f = rng.uniform(-0.0025, 0.0025)        # strong structural 8h funding (crowded regime)
        cl = [100.0 * (1 + 0.3 * ci)]
        for _ in range(1, 400):
            cl.append(cl[-1] * (1 + rng.gauss(0, 0.006)))   # per-8h price noise
        series = {}
        for t in range(400):
            f = (base_f + rng.gauss(0, 0.0003)) if planted else rng.gauss(0, 0.0015)
            series[BASE + t * EIGHTH] = (cl[t], f)
        data[f"C{ci}"] = series
    return data


def surface_rate(planted, k):
    return sum(bool(rt.cycle(FAM, gen(11 + i, planted))[1]) for i in range(k))


if __name__ == "__main__":
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(f"funding_carry gate — surface rate over {K} cycles (strong-carry regime; edge is funding only):")
    p = surface_rate(True, K)
    n = surface_rate(False, K)
    print(f"  planted strong-carry regime : {p}/{K} surfaced")
    print(f"  noise (funding random)      : {n}/{K} surfaced")
    # funding_carry is a LOW-SHARPE dollar-neutral factor: even with DSR 1.00 / n~18, the gate's PBO
    # test keeps it on a tight leash (its flat optimum + heavy price noise read as borderline ~0.5), so
    # it surfaces strong carry over REPEATED cycles, not every one. The HARD requirement is the safety
    # side: noise must NEVER surface. (CONFIRM=2 + many hourly cycles promote a real strong-carry edge.)
    ok = n == 0 and p >= 2
    print(f"\n{'PASS' if ok else 'FAIL'}: {'surfaces strong carry over repeated cycles; noise never (safe)' if ok else 'MISBEHAVED'}")
