"""Parameter ensemble of liquidation reversion — bagging over hyperparameters to KILL the
fragility, keeping the (3x OOS-confirmed) edge.

Instead of one config (wick 3.78/hold 9) and praying the neighbors don't bite, run a BASKET
spanning the validated region (wick 3.5-4.5, hold 6-12, atr 40/70), equal capital to each
sleeve, average the equity curves. Every sleeve IS the real edge; averaging cancels the
config-specific noise. The test: does the basket hold OOS AND collapse the outcome spread
that single-config selection suffers?

    python3 scripts/liquidation_ensemble.py
"""
from __future__ import annotations

import pathlib
import pickle
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.optimize.liquidation_reversion import run_liquidation_reversion as RLR  # noqa: E402

FEE = 8.0
WEEK = 7 * 86_400_000
# a-priori grid across the validated region (a RANGE, not the single winner)
BASKET = [{"wick_atr": w, "hold_hours": h, "body_max": 0.55, "cooldown_h": 5, "atr_window": a}
          for w in (3.5, 4.0, 4.5) for h in (6, 9, 12) for a in (40, 70)]


def _sh(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def _std(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    return (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5


def _weekly(trades, weeks):
    b = {}
    for t, r in trades:
        b[t // WEEK] = b.get(t // WEEK, 0.0) + r
    return [b.get(w, 0.0) for w in weeks]


def main():
    liq = pickle.loads((ROOT / ".liq_cache_24mo.pkl").read_bytes())
    allts = sorted({t for s in liq.values() for t in s})
    c0, c1 = allts[0], allts[-1]
    split = c0 + int((c1 - c0) * 0.72)
    sw = split // WEEK
    print(f"{len(liq)} coins | train {(split-c0)//86400000}d | holdout {(c1-split)//86400000}d (unseen) | "
          f"basket of {len(BASKET)} configs\n")

    def run_all(fee):
        trs = [RLR(liq, {**cfg, "fee_bps": fee}, DEFAULT_LIMITS, lo=c0) for cfg in BASKET]
        weeks = sorted({t // WEEK for tr in trs for t, _ in tr})
        return weeks, [_weekly(tr, weeks) for tr in trs]

    weeks, wk = run_all(FEE)
    tr_idx = [i for i, w in enumerate(weeks) if w < sw]
    ho_idx = [i for i, w in enumerate(weeks) if w >= sw]
    ens = [sum(wk[c][i] for c in range(len(BASKET))) / len(BASKET) for i in range(len(weeks))]

    def seg(idx, s):
        return [s[i] for i in idx]

    cfg_oos = sorted(_sh(seg(ho_idx, wk[c])) for c in range(len(BASKET)))
    cfg_tr = [_sh(seg(tr_idx, wk[c])) for c in range(len(BASKET))]
    print("SINGLE-CONFIG fragility (what you'd face picking ONE blindly):")
    print(f"  train  Sharpe/wk: min {min(cfg_tr):+.2f}  median {sorted(cfg_tr)[len(cfg_tr)//2]:+.2f}  max {max(cfg_tr):+.2f}")
    print(f"  HOLDOUT Sharpe/wk: min {cfg_oos[0]:+.2f}  median {cfg_oos[len(cfg_oos)//2]:+.2f}  max {cfg_oos[-1]:+.2f}"
          f"  | spread {cfg_oos[-1]-cfg_oos[0]:.2f}, std {_std(cfg_oos):.2f}")
    neg = sum(1 for s in cfg_oos if s <= 0)
    print(f"  of {len(BASKET)} configs, {neg} are <=0 OOS  <- pick wrong and you get a dud")

    rng = random.Random(11)

    def bp(x):
        b = [_sh([rng.choice(x) for _ in x]) for _ in range(4000)]
        return sum(1 for s in b if s <= 0) / len(b) if x else 1.0

    ho_ens = seg(ho_idx, ens)
    w2, wk2 = run_all(2 * FEE)
    ho2_idx = [i for i, w in enumerate(w2) if w >= sw]
    ens2 = [sum(wk2[c][i] for c in range(len(BASKET))) / len(BASKET) for i in range(len(w2))]
    print(f"\nPARAMETER ENSEMBLE (what you robustly get, ONE number):")
    print(f"  train Sharpe/wk {_sh(seg(tr_idx, ens)):+.3f} | HOLDOUT Sharpe/wk {_sh(ho_ens):+.3f} "
          f"(p {bp(ho_ens):.3f}, n {len(ho_ens)}wk) | 2x-cost {_sh(seg(ho2_idx, ens2)):+.3f}")

    ens_oos = _sh(ho_ens)
    med = cfg_oos[len(cfg_oos) // 2]
    robust = ens_oos > 0 and bp(ho_ens) < 0.05 and _sh(seg(ho2_idx, ens2)) > 0 and neg <= 0.25 * len(BASKET)
    print()
    print(f"the ensemble OOS Sharpe ({ens_oos:+.2f}) vs the single-config median ({med:+.2f}) and worst "
          f"({cfg_oos[0]:+.2f}):")
    if robust:
        print("  -> the basket keeps the edge AND removes the parameter lottery (you no longer have to")
        print("     pick the lucky config). Significant OOS, survives 2x cost, few losing sleeves.")
        print("  ROBUST LEAD ✅ — liquidation-reversion BASKET earns shadow validation (paper, no capital).")
    else:
        print("  -> the basket is positive/stable but doesn't fully clear the hardened bar (sig/cost/")
        print("     too many losing sleeves). Less fragile than a point, but flag honestly before shadow.")


if __name__ == "__main__":
    main()
