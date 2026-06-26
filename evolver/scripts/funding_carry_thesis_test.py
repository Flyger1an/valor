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

BASE, DAY = 1_600_000_000_000, 86_400_000
FAM = next(f for f in rt.CRYPTO_FAMILIES if f["name"] == "funding_carry")


def gen(seed, planted):
    """{coin:{ts:(close, daily_funding)}}, 500 daily bars. planted -> persistent per-coin funding
    (collectible carry); noise -> funding random each day. Price is the same random walk either way."""
    rng = random.Random(seed)
    data = {}
    for ci in range(19):
        base_f = rng.uniform(-0.004, 0.004)          # structural funding level (±0.4%/day, crowded regime)
        cl = [100.0 * (1 + 0.3 * ci)]
        for _ in range(1, 500):
            cl.append(cl[-1] * (1 + rng.gauss(0, 0.011)))
        series = {}
        for t in range(500):
            f = (base_f + rng.gauss(0, 0.0004)) if planted else rng.gauss(0, 0.0022)
            series[BASE + t * DAY] = (cl[t], f)
        data[f"C{ci}"] = series
    return data


def surface_rate(planted, k):
    return sum(bool(rt.cycle(FAM, gen(11 + i, planted))[1]) for i in range(k))


if __name__ == "__main__":
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(f"funding_carry gate — surface rate over {K} cycles (price is pure noise; edge is funding only):")
    p = surface_rate(True, K)
    n = surface_rate(False, K)
    print(f"  planted persistent-funding carry: {p}/{K} surfaced")
    print(f"  noise (funding random daily)    : {n}/{K} surfaced")
    ok = p >= max(1, K // 2) and n == 0
    print(f"\n{'PASS' if ok else 'FAIL'}: gate {'collects the real carry in the majority AND never on noise' if ok else 'MISBEHAVED'}")
