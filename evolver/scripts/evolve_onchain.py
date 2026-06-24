"""On-chain cross-sectional factor search — price factors + DefiLlama TVL factors.

    python3 scripts/evolve_onchain.py [llm]

36 DeFi/L1 tokens with Binance perp price + DefiLlama TVL. 9-dim genome (5 signed factor
weights + lookback/holding/quantile/skip). Same hardened gate. Honest caveat: USD TVL is
partly price-in-disguise; the price-vs-TVL DIVERGENCE factor is the cleaner on-chain signal.
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
from evolver.data import binance_dumps as bd, defillama as dl  # noqa: E402
from evolver.evolve.engine import evolve  # noqa: E402
from evolver.optimize.cross_sectional_onchain import run_cross_sectional_onchain  # noqa: E402

UNIVERSE = ["ETH", "SOL", "AVAX", "BNB", "MATIC", "ARB", "OP", "NEAR", "APT", "SUI", "SEI",
            "INJ", "ATOM", "FTM", "KAVA", "CELO", "ALGO", "ROSE", "STX", "TRX", "FLOW", "EGLD",
            "AAVE", "UNI", "CRV", "MKR", "COMP", "SNX", "LDO", "SUSHI", "PENDLE", "GMX", "DYDX",
            "CAKE", "BAL", "1INCH"]
MONTHS = 40
FEE_BPS = 5.0
SPACE = {"w_mom": (-2.0, 2.0, float), "w_rev": (-2.0, 2.0, float), "w_vol": (-2.0, 2.0, float),
         "w_tvlmom": (-2.0, 2.0, float), "w_tvldiv": (-2.0, 2.0, float),
         "lookback": (5.0, 90.0, int), "holding": (2.0, 30.0, int),
         "quantile": (0.1, 0.4, float), "skip": (0.0, 5.0, int)}
FAMILY = ("cross-sectional crypto long/short with ON-CHAIN factors: price (momentum, reversal, "
          "vol) + on-chain (TVL momentum = capital inflow, price-vs-TVL divergence = is price "
          "ahead of locked value). Signed weights. Maximize OOS/deflated/recent; the divergence "
          "factor is the cleaner on-chain edge since USD TVL is partly price-driven.")


def _sh(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def load():
    cache = ROOT / f".onchain_cache_{MONTHS}mo.pkl"
    if cache.exists():
        u = pickle.loads(cache.read_bytes())
        print(f"loaded {len(u)} coins from cache ({cache.name})")
        return u

    def one(c):
        try:
            px = bd.daily_closes(f"{c}USDT", "futures/um", MONTHS)
            tvl = dl.tvl_for(c)
            if px and tvl and len(px) >= 360 and len(tvl) >= 360:
                return c, (px, tvl)
        except Exception:
            pass
        return c, None
    print(f"fetching price (Binance) + TVL (DefiLlama) for {len(UNIVERSE)} coins...")
    u = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for c, d in ex.map(one, UNIVERSE):
            if d:
                u[c] = d
    cache.write_bytes(pickle.dumps(u))
    return u


def main():
    use_llm = len(sys.argv) > 1 and sys.argv[1] == "llm"
    data = load()
    days = sorted({d for px, _ in data.values() for d in px})
    print(f"universe: {len(data)}/{len(UNIVERSE)} coins with price+TVL ({sorted(data)})")
    print(f"          {len(days)} days ({(days[-1]-days[0])//86400000//30} months), fee {FEE_BPS}bps\n")
    if len(data) < 15:
        print("too few coins")
        return

    def backtest(params, lo, hi):
        return run_cross_sectional_onchain(data, {**params, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=lo, hi=hi)

    gens, pop = (6, 8) if use_llm else (14, 16)
    print(f"mode: {'LLM-as-mutation (gpt-5.5)' if use_llm else 'algorithmic'} | gens {gens} x pop {pop}\n")
    r = evolve(backtest, SPACE, FAMILY, generations=gens, pop=pop, seed=7, use_llm=use_llm)

    print(f"\nsearch: {r.n_evaluated} genomes | {r.archive.coverage()} QD cells | "
          f"LLM mutations {r.used_llm} | population PBO {r.pbo if r.pbo is not None else 'n/a'}")
    print(f"deflation: n_trials {r.n_trials}, var(trial Sharpe) {r.var_trials_sr}\n")
    print("top genomes (does on-chain weight survive?):")
    print(f"  {'wMom':>5} {'wRev':>5} {'wVol':>5} {'wTvlM':>6} {'wTvlD':>6} {'lkbk':>4} {'hold':>4} "
          f"{'qtl':>4} {'trd':>4} {'oosSR':>6} {'recent':>7} {'cons':>5} {'DSR':>5}")
    for c in r.elites[:8]:
        p = c.params
        print(f"  {p['w_mom']:>+5.2f} {p['w_rev']:>+5.2f} {p['w_vol']:>+5.2f} {p['w_tvlmom']:>+6.2f} "
              f"{p['w_tvldiv']:>+6.2f} {int(p['lookback']):>4} {int(p['holding']):>4} {p['quantile']:>4.2f} "
              f"{c.n_trades:>4} {c.oos_sharpe:>+6.2f} {c.recent_sharpe:>+7.2f} {c.consistency:>5.2f} {c.dsr:>5.2f}")

    print("\nSTAGE 2 — confirmation (recent sig + 2x-cost + held-out + deflation + sample + fold-stability):")
    to_check = r.promoted or r.elites[:2]
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
        rec = [r2 for _, r2 in run_cross_sectional_onchain(data, {**c.params, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=recent_lo)]
        c2 = [r2 for _, r2 in run_cross_sectional_onchain(data, {**c.params, "fee_bps": 2 * FEE_BPS}, DEFAULT_LIMITS)]
        ho = [r2 for _, r2 in run_cross_sectional_onchain(data, {**c.params, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=split)]
        rp, hp, cs = boot_p(rec), boot_p(ho), _sh(c2)
        fs = c.fold_sharpes or [0.0]
        checks = {"recent_sig": rp < 0.05, "holdout_sig": hp < 0.05, "survives_2x_cost": cs > 0,
                  "deflated(DSR>.95)": c.dsr > 0.95, "recent_n>=20": len(rec) >= 20,
                  "sharpe_sane(<2)": abs(_sh(rec)) < 2.0 and abs(_sh(ho)) < 2.0,
                  "fold_stable": max(fs) < 2.0 and min(fs) > -0.3}
        ok = all(checks.values())
        fails = [k for k, v in checks.items() if not v]
        oc = abs(c.params["w_tvlmom"]) + abs(c.params["w_tvldiv"])
        pc = abs(c.params["w_mom"]) + abs(c.params["w_rev"]) + abs(c.params["w_vol"])
        print(f"  on-chain weight {oc:.2f} vs price weight {pc:.2f} | "
              f"{dict((k,(round(v,2) if isinstance(v,float) else int(v))) for k,v in c.params.items())}")
        print(f"    recent SR {_sh(rec):+.2f} (n {len(rec)}, p {rp:.3f}) | held-out {_sh(ho):+.2f} (p {hp:.3f}) "
              f"| 2x-cost {cs:+.2f} | DSR {c.dsr} | folds {fs}")
        print(f"    -> {'CONFIRMED ✅' if ok else 'REJECTED — fails: ' + ', '.join(fails)}")
        if ok:
            survivors.append(c)

    print(f"\nVERDICT: {len(survivors)} confirmed of {r.n_evaluated} genomes (on-chain factor search).")
    if not survivors:
        print("  Adding on-chain TVL factors to the cross-section did not produce an edge that clears")
        print("  the gate. Note whether the search even WANTS on-chain weight (above) — if it zeroes")
        print("  them out, TVL carries no independent signal over price here.")


if __name__ == "__main__":
    main()
