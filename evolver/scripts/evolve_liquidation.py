"""MEV-adjacent pivot — liquidation-cascade reversion search.

    python3 scripts/evolve_liquidation.py [llm]

Structurally different from factor mining: a single-asset EVENT strategy that fades forced-
liquidation overshoots (violent intrabar wicks that snap back). 20 liquid perps, hourly, 18mo.
HIGH costs by design (8bps/side = catching a falling knife). Same hardened gate.
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
from evolver.optimize.liquidation_reversion import run_liquidation_reversion  # noqa: E402

UNIVERSE = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "DOT", "LTC",
            "ADA", "NEAR", "ARB", "OP", "INJ", "SUI", "APT", "SEI", "ATOM", "FIL"]
MONTHS = 24                 # salvage: more history -> more events overall
FEE_BPS = 8.0
# FOCUSED a-priori region (moderate wick = more frequent events; the broad 240-trial search
# over-penalized the edge via deflation — a focused ~40-config test faces a fair bar).
SPACE = {"wick_atr": (2.5, 4.5, float), "hold_hours": (3.0, 18.0, int), "body_max": (0.35, 0.7, float),
         "cooldown_h": (2.0, 18.0, int), "atr_window": (36.0, 96.0, int)}
FAMILY = ("liquidation-cascade reversion (MEV-adjacent, event-driven): fade violent intrabar "
          "wicks (forced-liquidation overshoots that snap back). wick_atr=wick size in ATR to "
          "trigger, hold_hours=hold, body_max=max body/range (ensure it's a wick not a trend), "
          "cooldown_h=min gap per coin, atr_window=ATR lookback. Maximize OOS/deflated/recent; "
          "costs are high (falling knife).")


def _sh(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def load():
    cache = ROOT / f".liq_cache_{MONTHS}mo.pkl"
    if cache.exists():
        u = pickle.loads(cache.read_bytes())
        print(f"loaded {len(u)} coins from cache ({cache.name})")
        return u

    def one(c):
        try:
            s = bd.intraday_ohlc(f"{c}USDT", "futures/um", "1h", MONTHS)
            return (c, s) if s and len(s) >= 5000 else (c, None)
        except Exception:
            return c, None
    print(f"fetching {len(UNIVERSE)} coins x 1h OHLC ({MONTHS}mo)...")
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
    allts = sorted({t for s in data.values() for t in s})
    print(f"universe: {len(data)}/{len(UNIVERSE)} coins, {len(allts)} hourly bars "
          f"({(allts[-1]-allts[0])//86400000} days), fee {FEE_BPS}bps/side\n")
    if len(data) < 8:
        print("too few coins")
        return

    def backtest(params, lo, hi):
        return run_liquidation_reversion(data, {**params, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=lo, hi=hi)

    gens, pop = (6, 8) if use_llm else (4, 8)   # FOCUSED: ~40 trials -> fair deflation bar
    print(f"mode: {'LLM-as-mutation (gpt-5.5)' if use_llm else 'algorithmic (focused salvage)'} "
          f"| gens {gens} x pop {pop}\n")
    r = evolve(backtest, SPACE, FAMILY, generations=gens, pop=pop, seed=7, use_llm=use_llm)

    print(f"\nsearch: {r.n_evaluated} genomes | {r.archive.coverage()} QD cells | "
          f"LLM mutations {r.used_llm} | population PBO {r.pbo if r.pbo is not None else 'n/a'}")
    print(f"deflation: n_trials {r.n_trials}, var(trial Sharpe) {r.var_trials_sr}\n")
    print("top genomes:")
    print(f"  {'wickATR':>7} {'hold':>4} {'bodyMx':>6} {'cool':>4} {'atrW':>4} {'trd':>5} "
          f"{'oosSR':>6} {'recent':>7} {'cons':>5} {'DSR':>5}")
    for c in r.elites[:8]:
        p = c.params
        print(f"  {p['wick_atr']:>7.2f} {int(p['hold_hours']):>4} {p['body_max']:>6.2f} "
              f"{int(p['cooldown_h']):>4} {int(p['atr_window']):>4} {c.n_trades:>5} "
              f"{c.oos_sharpe:>+6.2f} {c.recent_sharpe:>+7.2f} {c.consistency:>5.2f} {c.dsr:>5.2f}")

    print("\nSTAGE 2 — confirmation (recent sig + 2x-cost + held-out + deflation + sample + fold-stability):")
    to_check = r.promoted or r.elites[:2]
    rng = random.Random(7)

    def boot_p(x):
        if len(x) < 2:
            return 1.0
        b = [_sh([rng.choice(x) for _ in x]) for _ in range(2000)]
        return sum(1 for s in b if s <= 0) / len(b)

    survivors = []
    split = allts[int(len(allts) * 0.7)]
    recent_lo = allts[-180 * 24]
    for c in to_check:
        rec = [r2 for _, r2 in run_liquidation_reversion(data, {**c.params, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=recent_lo)]
        c2 = [r2 for _, r2 in run_liquidation_reversion(data, {**c.params, "fee_bps": 2 * FEE_BPS}, DEFAULT_LIMITS)]
        ho = [r2 for _, r2 in run_liquidation_reversion(data, {**c.params, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=split)]
        rp, hp, cs = boot_p(rec), boot_p(ho), _sh(c2)
        fs = c.fold_sharpes or [0.0]
        checks = {"recent_sig": rp < 0.05, "holdout_sig": hp < 0.05, "survives_2x_cost": cs > 0,
                  "deflated(DSR>.95)": c.dsr > 0.95, "recent_n>=20": len(rec) >= 20,
                  "sharpe_sane(<2)": abs(_sh(rec)) < 2.0 and abs(_sh(ho)) < 2.0,
                  "fold_stable": max(fs) < 2.0 and min(fs) > -0.3}
        ok = all(checks.values())
        fails = [k for k, v in checks.items() if not v]
        print(f"  {dict((k,(round(v,2) if isinstance(v,float) else int(v))) for k,v in c.params.items())}")
        print(f"    recent SR {_sh(rec):+.2f} (n {len(rec)}, p {rp:.3f}) | held-out {_sh(ho):+.2f} (p {hp:.3f}) "
              f"| 2x-cost {cs:+.2f} | DSR {c.dsr} | folds {fs}")
        print(f"    -> {'CONFIRMED ✅' if ok else 'REJECTED — fails: ' + ', '.join(fails)}")
        if ok:
            survivors.append(c)

    print(f"\nVERDICT: {len(survivors)} confirmed of {r.n_evaluated} genomes (liquidation reversion).")
    if not survivors:
        print("  The liquidation-overshoot signal is real but doesn't clear the gate net of falling-knife")
        print("  costs + deflation + recency. Same honest bar, different (event-driven) strategy type.")


if __name__ == "__main__":
    main()
