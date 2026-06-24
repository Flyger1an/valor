"""Hunt for a LESS-FRAGILE lead: time-series momentum with walk-forward + a stability gate.

    python3 scripts/evolve_trend.py

Reuses the 45-coin daily cache. Searches on TRAIN, locks the genome, tests it on the NEVER-SEEN
holdout, then checks whether its NEIGHBORS also hold out-of-sample — a plateau (robust) vs a
spike (fragile, like the liquidation lead). A second lead only counts if it's both OOS-positive
AND stable across nearby parameters.
"""
from __future__ import annotations

import pathlib
import pickle
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.evolve.engine import evolve  # noqa: E402
from evolver.optimize.trend_following import run_trend  # noqa: E402

FEE_BPS = 5.0
SPACE = {"lookback": (20.0, 200.0, int), "holding": (5.0, 40.0, int), "skip": (0.0, 5.0, int),
         "thr": (0.0, 0.10, float), "vol_window": (10.0, 60.0, int)}
FAMILY = ("time-series momentum / trend-following (per-asset): long if trailing return up, short "
          "if down (deadband thr), inverse-vol sized. lookback=trend window, holding=rebalance, "
          "skip, thr=deadband, vol_window. Maximize OOS/deflated/recent; trends => low turnover.")


def _sh(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def _neighbors(params):
    out = []
    for k, (lo, hi, typ) in SPACE.items():
        for f in (0.8, 1.2):
            q = dict(params)
            v = params[k] * f if params[k] else (lo + hi) / 2 * f
            v = max(lo, min(hi, v))
            q[k] = int(round(v)) if typ is int else round(v, 4)
            if q != params:
                out.append(q)
    return out


def main():
    cache = ROOT / ".xs_cache_40mo.pkl"
    if not cache.exists():
        print("run scripts/evolve_cross_sectional.py first to build .xs_cache_40mo.pkl")
        return
    data = pickle.loads(cache.read_bytes())
    days = sorted({d for s in data.values() for d in s})
    split = days[int(len(days) * 0.72)]
    print(f"{len(data)} coins | train first {(split-days[0])//86400000}d | "
          f"holdout last {(days[-1]-split)//86400000}d (never seen)\n")

    def bt_train(params, lo, hi):
        return run_trend(data, {**params, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=lo, hi=split)

    print("searching trend-following on TRAIN only...")
    r = evolve(bt_train, SPACE, FAMILY, generations=5, pop=10, seed=7, use_llm=False, log=lambda *_: None)
    print(f"  {r.n_evaluated} genomes | train PBO {r.pbo}")
    print("\n  top TRAIN genomes:")
    for c in r.elites[:5]:
        p = c.params
        print(f"    lb {int(p['lookback']):>3} hold {int(p['holding']):>2} skip {int(p['skip'])} "
              f"thr {p['thr']:.3f} volW {int(p['vol_window']):>2} | trainSR {c.full_sharpe:+.2f} "
              f"DSR {c.dsr:.2f} folds {c.fold_sharpes}")

    rng = random.Random(11)

    def boot_p(x):
        if len(x) < 2:
            return 1.0
        b = [_sh([rng.choice(x) for _ in x]) for _ in range(3000)]
        return sum(1 for s in b if s <= 0) / len(b)

    best = r.elites[0]
    print(f"\nLOCK fitness-selected genome -> test on NEVER-SEEN holdout + neighborhood stability:")
    ho = [r2 for _, r2 in run_trend(data, {**best.params, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=split)]
    ho2 = [r2 for _, r2 in run_trend(data, {**best.params, "fee_bps": 2 * FEE_BPS}, DEFAULT_LIMITS, lo=split)]
    osr, on, op, o2 = _sh(ho), len(ho), boot_p(ho), _sh(ho2)
    print(f"  locked {dict((k,(round(v,3) if isinstance(v,float) else int(v))) for k,v in best.params.items())}")
    print(f"  OOS Sharpe {osr:+.3f} (n {on}, p {op:.3f}) | 2x-cost {o2:+.3f}")

    nb = _neighbors(best.params)
    nb_sr = [_sh([r2 for _, r2 in run_trend(data, {**q, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=split)]) for q in nb]
    pos = sum(1 for s in nb_sr if s > 0)
    print(f"  NEIGHBORHOOD (n={len(nb)} nearby params, OOS): {pos}/{len(nb)} positive | "
          f"mean OOS Sharpe {sum(nb_sr)/len(nb_sr):+.3f} | range [{min(nb_sr):+.2f}, {max(nb_sr):+.2f}]")

    oos_ok = osr > 0 and op < 0.05 and on >= 20 and o2 > 0
    stable = pos >= 0.75 * len(nb) and sum(nb_sr) / len(nb_sr) > 0
    print()
    if oos_ok and stable:
        print("VERDICT: trend-following HOLDS out-of-sample AND is STABLE across the neighborhood (a")
        print("  plateau, not a spike) -> a genuine LESS-FRAGILE second lead. Earns shadow validation.")
    elif oos_ok and not stable:
        print("VERDICT: holds OOS but the neighborhood is unstable -> fragile like the liquidation lead;")
        print("  not the robust second lead we wanted.")
    else:
        print(f"VERDICT: does NOT hold out-of-sample (OOS Sharpe {osr:+.2f}, p {op:.3f}). Trend-following")
        print("  doesn't survive walk-forward in this universe/period. Honest no; keep hunting.")


if __name__ == "__main__":
    main()
