"""The hard test — high-dimensional cross-sectional factor search over ~45 coins.

    python3 scripts/evolve_cross_sectional.py          # algorithmic (thorough, fast)
    python3 scripts/evolve_cross_sectional.py llm       # LLM-as-mutation (gpt-5.5)

7-dim genome (3 signed factor weights + lookback/holding/quantile/skip) -> a dollar-neutral
long/short portfolio. The engine searches; the honest gate (deflated Sharpe + PBO + walk-forward
+ recency + cost-stress) decides. This is the factor zoo — if anything survives, it earned it.
"""
from __future__ import annotations

import os
import pathlib
import pickle
import random
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_envf = ROOT / ".env"
if _envf.exists():
    for _l in _envf.read_text().splitlines():
        if "=" in _l and not _l.strip().startswith("#"):
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.data import binance_dumps as bd  # noqa: E402
from evolver.evolve.engine import evolve  # noqa: E402
from evolver.optimize.cross_sectional import run_cross_sectional  # noqa: E402

UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT", "LINK", "LTC", "BCH",
            "ETC", "XLM", "ATOM", "AVAX", "NEAR", "FIL", "ALGO", "VET", "ICP", "HBAR", "EGLD",
            "THETA", "AAVE", "UNI", "MKR", "SNX", "CRV", "COMP", "SUSHI", "GRT", "ENJ", "CHZ",
            "MANA", "SAND", "AXS", "GALA", "RUNE", "ZIL", "BAT", "DASH", "ZEC", "IOTA", "ONE", "QTUM"]
MONTHS = 40
SPACE = {"w_mom": (-2.0, 2.0, float), "w_rev": (-2.0, 2.0, float), "w_vol": (-2.0, 2.0, float),
         "lookback": (5.0, 90.0, int), "holding": (2.0, 30.0, int),
         "quantile": (0.1, 0.4, float), "skip": (0.0, 5.0, int)}
FAMILY = ("cross-sectional crypto long/short: each rebalance, score coins by w_mom*momentum + "
          "w_rev*short-term-reversal + w_vol*volatility (cross-sectional z-scores), long top "
          "quantile / short bottom quantile dollar-neutral, hold `holding` days, skip `skip` recent "
          "days. Weights are signed. Maximize out-of-sample/deflated/recent, beware turnover cost.")


def _sh(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def load():
    cache = ROOT / f".xs_cache_{MONTHS}mo.pkl"
    if cache.exists():
        u = pickle.loads(cache.read_bytes())
        print(f"loaded {len(u)} coins from cache ({cache.name})")
        return u

    def one(c):
        try:
            s = bd.daily_closes(f"{c}USDT", "futures/um", MONTHS)
            return (c, s) if s and len(s) >= 300 else (c, None)
        except Exception:
            return c, None
    print(f"fetching {len(UNIVERSE)} coins x {MONTHS}mo daily closes (Binance CDN)...")
    u = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for c, s in ex.map(one, UNIVERSE):
            if s:
                u[c] = s
    cache.write_bytes(pickle.dumps(u))
    return u


def main():
    use_llm = len(sys.argv) > 1 and sys.argv[1] == "llm"
    data = load()
    days = sorted({d for s in data.values() for d in s})
    print(f"universe: {len(data)} coins, {len(days)} trading days "
          f"({(days[-1]-days[0])//86400000//30} months)\n")
    if len(data) < 15:
        print("too few coins")
        return

    def backtest(params, lo, hi):
        return run_cross_sectional(data, {**params, "fee_bps": 4.0}, DEFAULT_LIMITS, lo=lo, hi=hi)

    gens, pop = (6, 8) if use_llm else (14, 16)   # backtest is ~0.05s -> afford a big honest search
    print(f"mode: {'LLM-as-mutation (gpt-5.5)' if use_llm else 'algorithmic'} | "
          f"generations {gens} x pop {pop}\n")
    r = evolve(backtest, SPACE, FAMILY, generations=gens, pop=pop, seed=7, use_llm=use_llm)

    print(f"\nsearch: {r.n_evaluated} genomes | {r.archive.coverage()} QD cells | "
          f"LLM mutations {r.used_llm} | population PBO {r.pbo if r.pbo is not None else 'n/a'}")
    print(f"deflation: n_trials {r.n_trials}, var(trial Sharpe) {r.var_trials_sr}\n")
    print("top genomes (OOS-consistency fitness):")
    print(f"  {'wMom':>5} {'wRev':>5} {'wVol':>5} {'lkbk':>4} {'hold':>4} {'qtl':>4} {'skip':>4} "
          f"{'trd':>4} {'fullSR':>7} {'oosSR':>6} {'recent':>7} {'cons':>5} {'DSR':>5}")
    for c in r.elites[:8]:
        p = c.params
        print(f"  {p['w_mom']:>+5.2f} {p['w_rev']:>+5.2f} {p['w_vol']:>+5.2f} {int(p['lookback']):>4} "
              f"{int(p['holding']):>4} {p['quantile']:>4.2f} {int(p['skip']):>4} {c.n_trades:>4} "
              f"{c.full_sharpe:>+7.2f} {c.oos_sharpe:>+6.2f} {c.recent_sharpe:>+7.2f} "
              f"{c.consistency:>5.2f} {c.dsr:>5.2f}")

    # cross-sectional confirmation: recent significance + cost-stress + held-out time
    print("\nSTAGE 2 — confirmation (recent significance + 2x-cost-stress + held-out time):")
    to_check = r.promoted or r.elites[:1]
    rng = random.Random(7)

    def boot_p(x):
        if len(x) < 2:
            return 1.0
        b = [_sh([rng.choice(x) for _ in x]) for _ in range(2000)]
        return sum(1 for s in b if s <= 0) / len(b)

    survivors = []
    split = days[int(len(days) * 0.7)]
    recent_lo = days[-180]
    for c in to_check:
        rec = run_cross_sectional(data, {**c.params, "fee_bps": 4.0}, DEFAULT_LIMITS, lo=recent_lo)
        rec_r = [r for _, r in rec]
        c2 = run_cross_sectional(data, {**c.params, "fee_bps": 8.0}, DEFAULT_LIMITS)
        c2_r = [r for _, r in c2]
        ho = run_cross_sectional(data, {**c.params, "fee_bps": 4.0}, DEFAULT_LIMITS, lo=split)
        ho_r = [r for _, r in ho]
        rp, hp, cs = boot_p(rec_r), boot_p(ho_r), _sh(c2_r)
        fs = c.fold_sharpes or [0.0]
        checks = {"recent_sig": rp < 0.05, "holdout_sig": hp < 0.05, "survives_2x_cost": cs > 0,
                  "deflated(DSR>.95)": c.dsr > 0.95, "recent_n>=20": len(rec_r) >= 20,
                  "sharpe_sane(<2)": abs(_sh(rec_r)) < 2.0 and abs(_sh(ho_r)) < 2.0,
                  "fold_stable": max(fs) < 2.0 and min(fs) > -0.3}
        ok = all(checks.values())
        fails = [k for k, v in checks.items() if not v]
        print(f"  {dict((k, (round(v,3) if isinstance(v,float) else int(v))) for k,v in c.params.items())}")
        print(f"    recent SR {_sh(rec_r):+.2f} (n {len(rec_r)}, p {rp:.3f}) | held-out SR {_sh(ho_r):+.2f} "
              f"(p {hp:.3f}) | 2x-cost {cs:+.2f} | DSR {c.dsr} | folds {fs}")
        print(f"    -> {'CONFIRMED ✅' if ok else 'REJECTED — fails: ' + ', '.join(fails)}")
        if ok:
            survivors.append(c)

    print(f"\nVERDICT: {len(survivors)} confirmed candidate(s) out of {r.n_evaluated} genomes searched.")
    if not survivors:
        print("  The engine searched a 7-dim factor space over ~45 coins and the honest gate refused")
        print("  every genome. Either no robust cross-sectional edge here, or it doesn't clear costs +")
        print("  deflation + recency. No fantasy manufactured — the gate held under max overfit pressure.")


if __name__ == "__main__":
    main()
