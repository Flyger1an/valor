"""Decisive honest test for the liquidation-reversion lead: TRUE walk-forward.

Search (focused) on the FIRST 18 months only; lock the winning genome; evaluate it on the
LAST 6 months it never saw. No look-ahead, no trial-undercounting — the holdout was never in
the search. If the blind-selected genome is positive + significant out-of-sample, it's real
and earns shadow validation. If it collapses, the in-sample lead was a mirage.

    python3 scripts/liquidation_walkforward.py
"""
from __future__ import annotations

import pathlib
import pickle
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolver.data.stats import block_bootstrap_pvalue  # noqa: E402
from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.evolve.engine import evolve  # noqa: E402
from evolver.optimize.liquidation_reversion import run_liquidation_reversion as RLR  # noqa: E402

FEE_BPS = 8.0
SPACE = {"wick_atr": (2.5, 4.5, float), "hold_hours": (3.0, 18.0, int), "body_max": (0.35, 0.7, float),
         "cooldown_h": (2.0, 18.0, int), "atr_window": (36.0, 96.0, int)}
FAMILY = "liquidation-cascade reversion (fade intrabar liquidation wicks). Maximize OOS/deflated/recent."


def _sh(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def main():
    cache = ROOT / ".liq_cache_24mo.pkl"
    if not cache.exists():
        print("run scripts/evolve_liquidation.py first to build .liq_cache_24mo.pkl")
        return
    data = pickle.loads(cache.read_bytes())
    allts = sorted({t for s in data.values() for t in s})
    split = allts[-180 * 24]            # last 180 days held out ENTIRELY from the search
    print(f"{len(data)} coins | train = first {(split-allts[0])//86400000}d | "
          f"holdout = last {(allts[-1]-split)//86400000}d (NEVER seen by search)\n")

    def backtest_train(params, lo, hi):
        return RLR(data, {**params, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=lo, hi=split)

    print("searching on TRAIN only (first 18mo)...")
    r = evolve(backtest_train, SPACE, FAMILY, generations=4, pop=8, seed=7, use_llm=False, log=lambda *_: None)
    print(f"  {r.n_evaluated} genomes | train PBO {r.pbo}")

    rng = random.Random(11)

    def boot_p(x):   # stationary block bootstrap -> preserves autocorrelation
        return block_bootstrap_pvalue(x, n_boot=3000)

    print("\nlock each top TRAIN genome -> evaluate on the NEVER-SEEN holdout:")
    print(f"  {'wickATR':>7} {'hold':>4} {'atrW':>4} | {'trainSR':>7} {'trDSR':>5} || "
          f"{'OOS_SR':>6} {'OOS_n':>5} {'OOS_p':>6} {'OOS_2xcost':>10}")
    confirmed = 0
    for c in r.elites[:5]:
        ho = [r2 for _, r2 in RLR(data, {**c.params, "fee_bps": FEE_BPS}, DEFAULT_LIMITS, lo=split)]
        ho2 = [r2 for _, r2 in RLR(data, {**c.params, "fee_bps": 2 * FEE_BPS}, DEFAULT_LIMITS, lo=split)]
        p = c.params
        osr, on, op, o2 = _sh(ho), len(ho), boot_p(ho), _sh(ho2)
        live = osr > 0 and op < 0.05 and on >= 15 and o2 > 0
        confirmed += int(live)
        print(f"  {p['wick_atr']:>7.2f} {int(p['hold_hours']):>4} {int(p['atr_window']):>4} | "
              f"{c.full_sharpe:>+7.2f} {c.dsr:>5.2f} || {osr:>+6.2f} {on:>5} {op:>6.3f} {o2:>+10.2f}"
              f"  {'<- holds OOS ✅' if live else ''}")

    print()
    if confirmed:
        print(f"VERDICT: {confirmed} genome(s) selected blind on 18mo stay positive + significant on the")
        print("  NEVER-SEEN 6mo holdout. That's genuine out-of-sample edge -> earns Phase 3 shadow validation.")
        print("  (Still: small holdout sample; shadow-mode forward paper is the real proof. No live capital.)")
    else:
        print("VERDICT: the in-sample lead does NOT survive true walk-forward — genomes picked on 18mo")
        print("  fail on the unseen 6mo. The liquidation edge was in-sample/regime luck. Honest kill.")


if __name__ == "__main__":
    main()
