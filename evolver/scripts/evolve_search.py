"""Evolutionary strategy search — LLM-driven mutation + MAP-Elites + honest gate.

    python3 scripts/evolve_search.py          # algorithmic mutation (fast, offline, exact)
    python3 scripts/evolve_search.py llm       # LLM-as-mutation-operator (needs OPENAI_API_KEY)

Demo family: cross-venue funding dislocation (the edge we proved decayed tonight). The point
is NOT to find alpha — it's to show the engine's fitness REFUSES to promote a decayed edge,
autonomously rediscovering the manual verdict. An honest search mostly returns "nothing robust".
"""
from __future__ import annotations

import os
import pathlib
import pickle
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# load secrets from evolver/.env (gitignored) without printing them
_envf = ROOT / ".env"
if _envf.exists():
    for _line in _envf.read_text().splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.evolve.engine import evolve  # noqa: E402
from evolver.optimize.cross_venue import run_cross_venue  # noqa: E402

SPACE = {"entry_ann_diff": (0.03, 0.40, float),
         "exit_ann_diff": (0.005, 0.10, float),
         "max_hold_days": (3.0, 30.0, int)}
FAMILY = ("cross-venue funding dislocation: long the low-funding venue's perp, short the high-"
          "funding venue's perp, collect the funding differential. entry_ann_diff=annualized "
          "differential to enter, exit_ann_diff=differential to exit, max_hold_days=max hold.")


def main():
    use_llm = len(sys.argv) > 1 and sys.argv[1] == "llm"
    cache = ROOT / ".xv_cache_18mo.pkl"
    if not cache.exists():
        print("run scripts/cross_venue_oos.py first to build .xv_cache_18mo.pkl")
        return
    data = pickle.loads(cache.read_bytes())

    def backtest(params, lo, hi):
        p = {"entry_ann_diff": params["entry_ann_diff"], "exit_ann_diff": params["exit_ann_diff"],
             "max_hold_days": int(params["max_hold_days"]), "fee_bps": 2.0}
        res = run_cross_venue(data, p, DEFAULT_LIMITS, lo=lo, hi=hi)
        return [(f.ts, f.pnl_pct) for f in res["fills"]]

    key = "present" if os.getenv("OPENAI_API_KEY") else "MISSING"
    mode = f"LLM-as-mutation (STRONG_MODEL={os.getenv('STRONG_MODEL','?')}, key {key})" if use_llm else "algorithmic"
    gens, pop = (3, 4) if use_llm else (6, 8)
    print(f"mode: {mode} | {len(data)} coins | generations {gens} x pop {pop}\n")

    r = evolve(backtest, SPACE, FAMILY, generations=gens, pop=pop, seed=7, use_llm=use_llm)

    print(f"\nsearch: {r.n_evaluated} genomes evaluated | {r.archive.coverage()} QD cells | "
          f"LLM mutations used: {r.used_llm}")
    print(f"deflation: n_trials {r.n_trials}, var(trial Sharpe) {r.var_trials_sr} | "
          f"population PBO {r.pbo if r.pbo is not None else 'n/a'}")
    print(f"\ntop genomes (by OOS-consistency fitness):")
    print(f"  {'entry':>6} {'exit':>6} {'hold':>5} {'trades':>7} {'fullSR':>7} {'oosSR':>6} "
          f"{'recent':>7} {'consist':>8} {'DSR':>6}")
    for c in r.elites[:6]:
        p = c.params
        print(f"  {p['entry_ann_diff']:>6.3f} {p['exit_ann_diff']:>6.3f} {int(p['max_hold_days']):>5} "
              f"{c.n_trades:>7} {c.full_sharpe:>+7.2f} {c.oos_sharpe:>+6.2f} {c.recent_sharpe:>+7.2f} "
              f"{c.consistency:>8.2f} {c.dsr:>6.2f}")

    print(f"\nPROMOTION GATE (DSR>0.95 AND recent>0 AND consistency>=0.6 AND trades>=30 AND PBO<0.5):")
    if r.promoted:
        print(f"  {len(r.promoted)} genome(s) PASS — candidate(s) for OOS shadow validation:")
        for c in r.promoted:
            print(f"    {c.params} | DSR {c.dsr} recent {c.recent_sharpe}")
    else:
        best = r.elites[0]
        why = []
        if best.dsr <= 0.95:
            why.append(f"DSR {best.dsr}≤0.95 (not significant after multiple-testing haircut)")
        if best.recent_sharpe <= 0:
            why.append(f"recent Sharpe {best.recent_sharpe}≤0 (edge decayed)")
        if r.pbo is not None and r.pbo >= 0.5:
            why.append(f"PBO {r.pbo}≥0.5 (search overfit)")
        print(f"  0 genomes pass — the engine refuses to promote. Best genome blocked by: "
              f"{'; '.join(why) or 'consistency/sample gates'}.")
        print("  => exactly right: this edge is decayed. The search did not manufacture a fantasy.")

    # STAGE 2 — confirm proposals on recent significance + intraday execution (automates
    # tonight's manual check; a daily DSR pass is NOT enough to be a candidate)
    import random as _rnd
    from evolver.evolve.confirm import confirm
    from evolver.optimize.cross_venue_intraday import run_cross_venue_intraday
    xvi = ROOT / ".xvi_cache_6mo.pkl"
    to_confirm = r.promoted or r.elites[:1]
    if xvi.exists() and to_confirm:
        idata = pickle.loads(xvi.read_bytes())
        recent_lo = sorted({d for v in data.values() for d in v[0]})[-180]
        rng = _rnd.Random(7)

        def _sh(x):
            if len(x) < 2:
                return 0.0
            m = sum(x) / len(x)
            sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
            return m / sd if sd > 0 else 0.0

        def _p(x):
            if len(x) < 2:
                return 1.0
            b = [_sh([rng.choice(x) for _ in x]) for _ in range(2000)]
            return sum(1 for s in b if s <= 0) / len(b)

        def validate(params):
            dp = {"entry_ann_diff": params["entry_ann_diff"], "exit_ann_diff": params["exit_ann_diff"],
                  "max_hold_days": int(params["max_hold_days"]), "fee_bps": 2.0}
            dr = run_cross_venue(data, dp, DEFAULT_LIMITS, lo=recent_lo)["returns"]
            ip = {"entry_ann_diff": params["entry_ann_diff"], "exit_ann_diff": 0.02,
                  "max_hold_hours": int(params["max_hold_days"]) * 24, "fee_bps": 2.0, "slip_bps": 3.0}
            ir = run_cross_venue_intraday(idata, ip, DEFAULT_LIMITS)["returns"]
            brd = sum(1 for c in idata if (run_cross_venue_intraday(
                {c: idata[c]}, ip, DEFAULT_LIMITS)["kpis"].get("total_return") or 0) > 0) / len(idata)
            return {"recent_sharpe": _sh(dr), "recent_p": _p(dr),
                    "intraday_sharpe": _sh(ir), "intraday_p": _p(ir), "breadth": brd}

        print("\nSTAGE 2 — confirmation (recent significance + intraday execution):")
        surv = confirm(to_confirm, validate)
        print(f"\n  CONFIRMED: {len(surv)} candidate(s) — "
              + ("real shadow-mode candidate(s) ✅" if surv
                 else "none; held back. A daily edge that fails recent/intraday significance is a"
                      " WATCHLIST item, not a trade — the two-stage gate working as designed."))


if __name__ == "__main__":
    main()
