"""Two-strategy ensemble: trend-following (robust/weak) + liquidation reversion (strong/fragile).

The diversification test, done honestly:
  * search EACH strategy on TRAIN only, lock both genomes (no holdout leakage)
  * convert each to a weekly P&L stream over the common period
  * risk-parity blend (scale each to equal vol on TRAIN, 50/50), apply to the never-seen holdout
  * ask: do they DECORRELATE? is combined OOS Sharpe > either alone? does it clear significance?

    python3 scripts/ensemble_test.py
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
from evolver.optimize.liquidation_reversion import run_liquidation_reversion as RLR  # noqa: E402
from evolver.optimize.trend_following import run_trend as RT  # noqa: E402

LIQ_FEE, TREND_FEE = 8.0, 5.0
WEEK = 7 * 86_400_000
SPACE_LIQ = {"wick_atr": (2.5, 4.5, float), "hold_hours": (3.0, 18.0, int), "body_max": (0.35, 0.7, float),
             "cooldown_h": (2.0, 18.0, int), "atr_window": (36.0, 96.0, int)}
SPACE_TREND = {"lookback": (20.0, 200.0, int), "holding": (5.0, 40.0, int), "skip": (0.0, 5.0, int),
               "thr": (0.0, 0.10, float), "vol_window": (10.0, 60.0, int)}


def _sh(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def _weekly(trades, weeks):
    b = {}
    for t, r in trades:
        b[t // WEEK] = b.get(t // WEEK, 0.0) + r
    return [b.get(w, 0.0) for w in weeks]


def _corr(a, b):
    n = len(a)
    if n < 2:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a) ** 0.5
    vb = sum((x - mb) ** 2 for x in b) ** 0.5
    return cov / (va * vb) if va > 0 and vb > 0 else 0.0


def main():
    liq = pickle.loads((ROOT / ".liq_cache_24mo.pkl").read_bytes())
    trd = pickle.loads((ROOT / ".xs_cache_40mo.pkl").read_bytes())
    liq_ts = sorted({t for s in liq.values() for t in s})
    trd_ts = sorted({t for s in trd.values() for t in s})
    c0, c1 = max(liq_ts[0], trd_ts[0]), min(liq_ts[-1], trd_ts[-1])   # common span
    split = c0 + int((c1 - c0) * 0.72)
    print(f"common span {(c1-c0)//86400000}d | train {(split-c0)//86400000}d | holdout {(c1-split)//86400000}d (unseen)\n")

    print("searching each strategy on TRAIN only...")
    rl = evolve(lambda p, lo, hi: RLR(liq, {**p, "fee_bps": LIQ_FEE}, DEFAULT_LIMITS, lo=c0, hi=split),
                SPACE_LIQ, "liquidation reversion", generations=4, pop=8, seed=7, use_llm=False, log=lambda *_: None)
    rt = evolve(lambda p, lo, hi: RT(trd, {**p, "fee_bps": TREND_FEE}, DEFAULT_LIMITS, lo=c0, hi=split),
                SPACE_TREND, "trend following", generations=5, pop=10, seed=7, use_llm=False, log=lambda *_: None)
    gl, gt = rl.elites[0].params, rt.elites[0].params
    print(f"  liq genome:   {dict((k,(round(v,2) if isinstance(v,float) else int(v))) for k,v in gl.items())}")
    print(f"  trend genome: {dict((k,(round(v,3) if isinstance(v,float) else int(v))) for k,v in gt.items())}")

    def streams(fee_mult=1.0):
        lt = RLR(liq, {**gl, "fee_bps": LIQ_FEE * fee_mult}, DEFAULT_LIMITS, lo=c0)
        tt = RT(trd, {**gt, "fee_bps": TREND_FEE * fee_mult}, DEFAULT_LIMITS, lo=c0)
        weeks = sorted(set(t // WEEK for t, _ in lt) | set(t // WEEK for t, _ in tt))
        return weeks, _weekly(lt, weeks), _weekly(tt, weeks)

    weeks, lw, tw = streams()
    sw = split // WEEK
    tr = [i for i, w in enumerate(weeks) if w < sw]
    ho = [i for i, w in enumerate(weeks) if w >= sw]
    # risk-parity: scale each to unit vol on TRAIN, blend 50/50
    sl = 1.0 / (_sh([lw[i] for i in tr]) and (sum((lw[i]-sum(lw[j] for j in tr)/len(tr))**2 for i in tr)/len(tr))**0.5 or 1.0)
    st = 1.0 / ((sum((tw[i]-sum(tw[j] for j in tr)/len(tr))**2 for i in tr)/len(tr))**0.5 or 1.0)
    comb = [0.5 * sl * lw[i] + 0.5 * st * tw[i] for i in range(len(weeks))]

    rng = random.Random(11)

    def bp(x):
        if len(x) < 2:
            return 1.0
        b = [_sh([rng.choice(x) for _ in x]) for _ in range(3000)]
        return sum(1 for s in b if s <= 0) / len(b)

    def seg(idx, series):
        return [series[i] for i in idx]

    corr_tr = _corr(seg(tr, lw), seg(tr, tw))
    corr_ho = _corr(seg(ho, lw), seg(ho, tw))
    print(f"\ncorrelation (weekly): train {corr_tr:+.2f} | holdout {corr_ho:+.2f}  "
          f"{'<- diversifying' if corr_ho < 0.4 else '<- too correlated to help'}")

    print(f"\n{'':12} {'liq alone':>10} {'trend alone':>12} {'ENSEMBLE':>10}")
    for name, idx in [("TRAIN", tr), ("HOLDOUT", ho)]:
        print(f"  {name:9} SR {_sh(seg(idx, lw)):>+10.3f} {_sh(seg(idx, tw)):>+12.3f} {_sh(seg(idx, comb)):>+10.3f}")
    ho_comb = seg(ho, comb)
    p_comb = bp(ho_comb)
    weeks2, lw2, tw2 = streams(2.0)
    comb2 = [0.5 * sl * lw2[i] + 0.5 * st * tw2[i] for i in range(len(weeks2))]
    ho2 = [i for i, w in enumerate(weeks2) if w >= sw]
    print(f"\nENSEMBLE out-of-sample: Sharpe/wk {_sh(ho_comb):+.3f} | p {p_comb:.3f} | "
          f"n {len(ho_comb)}wk | 2x-cost SR {_sh(seg(ho2, comb2)):+.3f}")

    lift = _sh(ho_comb) > max(_sh(seg(ho, lw)), _sh(seg(ho, tw)))
    ok = _sh(ho_comb) > 0 and p_comb < 0.05 and corr_ho < 0.5 and _sh(seg(ho2, comb2)) > 0
    print()
    if ok and lift:
        print("VERDICT: the ensemble DECORRELATES and the combined OOS Sharpe beats either half AND clears")
        print("  significance — diversification delivered the robust lead. Pair earns shadow validation. ✅")
    elif _sh(ho_comb) > 0 and corr_ho < 0.5:
        print("VERDICT: they decorrelate and the blend is positive OOS, but it doesn't clear significance")
        print("  /beat both halves cleanly. Promising direction, not yet a confirmed robust lead.")
    else:
        print("VERDICT: the ensemble doesn't clear the bar OOS (too correlated, or the blend is dominated/")
        print("  diluted). Diversification didn't rescue it here. Honest read.")


if __name__ == "__main__":
    main()
