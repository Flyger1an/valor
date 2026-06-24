"""Hybrid deep-history funding-carry validation.

Uses the source router (prefer='depth' -> Binance dumps) to pull ~18 months of
funding + perp + spot, then OOS-tests taker vs maker execution on a real sample
spanning past high-funding regimes. This is what the 3-trade OKX window couldn't do.

    python3 scripts/hybrid_funding_oos.py
"""
from __future__ import annotations

import datetime as dt
import itertools
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.data import sources  # noqa: E402
from evolver.optimize.funding_backtest import run_funding_backtest  # noqa: E402
from evolver.optimize.promotion import deflated_sharpe, sharpe  # noqa: E402

BASES = ["BTC", "ETH", "SOL"]
LOOKBACK = 730  # ~24 months — reach back into the 2024 high-funding bull


def _ym(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m")


def build():
    data = {}
    for b in BASES:
        fund, fs = sources.funding_daily(b, LOOKBACK, prefer="depth")
        perp, ps = sources.closes_daily(b, "perp", LOOKBACK, prefer="depth")
        spot, ss = sources.closes_daily(b, "spot", LOOKBACK, prefer="depth")
        print(f"  {b}: funding<{fs}> {len(fund)}d | perp<{ps}> {len(perp)}d | spot<{ss}> {len(spot)}d")
        data[b] = (spot, perp, fund)
    return data


def main():
    print("HYBRID deep-history funding-carry validation (router prefer='depth')\n")
    data = build()
    days = sorted({d for b in BASES for d in data[b][2]})
    if len(days) < 60:
        print("\ninsufficient history assembled")
        return
    split = days[int(len(days) * 0.7)]
    print(f"\n  window {_ym(days[0])} -> {_ym(days[-1])}  ({len(days)} funding-days)  split @ {_ym(split)}")

    # honest regime picture: how often did annualized funding actually run hot?
    allf = sorted(abs(data[b][2][d] * 365) for b in BASES for d in data[b][2])
    n = len(allf)
    pct = lambda thr: sum(1 for x in allf if x > thr) / n * 100
    print(f"  |ann funding| distribution: median {allf[n//2]*100:.1f}% | >5%: {pct(0.05):.0f}% of days "
          f"| >10%: {pct(0.10):.0f}% | >20%: {pct(0.20):.0f}% | max {allf[-1]*100:.0f}%\n")

    grid = [dict(zip(("entry_ann_funding", "exit_ann_funding", "max_hold_days"), v))
            for v in itertools.product([0.05, 0.08, 0.12, 0.20], [0.0, 0.03], [7, 14, 30])]

    def best(exe, label):
        b = None
        for g in grid:
            tr = run_funding_backtest(data, {**g, **exe}, DEFAULT_LIMITS, hi=split)
            if len(tr["returns"]) < 10:
                continue
            s = deflated_sharpe(tr["returns"], len(grid))
            if b is None or s > b[0]:
                b = (s, {**g, **exe}, tr)
        if not b:
            print(f"  {label}: too few trades")
            return
        _s, p, tr = b
        va = run_funding_backtest(data, p, DEFAULT_LIMITS, lo=split)
        oos = sharpe(va["returns"])
        vk = va["kpis"]
        print(f"  {label:12} entry>{int(p['entry_ann_funding']*100)}% hold{p['max_hold_days']}d | "
              f"TRAIN sh {sharpe(tr['returns']):+.3f} (n={tr['kpis']['trades']}) || "
              f"OOS sh {oos:+.3f} ret {(vk.get('total_return') or 0.0):+.3f} PF {vk.get('profit_factor')} "
              f"(n={vk.get('trades', 0)}) funding ${va['funding_collected_usd']:,.0f}  "
              f"-> {'PROMOTE ✅' if oos > 0.10 else 'reject'}")

    print("=== execution-cost A/B (chronological split — fails: regime non-stationary) ===")
    best({"fee_bps": 5.0, "legs": 4}, "taker 5/4")
    best({"fee_bps": 2.0, "legs": 2}, "maker 2/2")

    print("\n=== cross-asset validation (fit on 2 coins, test on the held-out 3rd) ===")
    maker = {"fee_bps": 2.0, "legs": 2}

    def fit_eval(fit_bases, test_base):
        fit = {k: data[k] for k in fit_bases}
        b = None
        for g in grid:
            tr = run_funding_backtest(fit, {**g, **maker}, DEFAULT_LIMITS)
            if len(tr["returns"]) < 10:
                continue
            s = deflated_sharpe(tr["returns"], len(grid))
            if b is None or s > b[0]:
                b = (s, {**g, **maker}, tr)
        if not b:
            print(f"  fit {fit_bases}: too few trades")
            return
        _s, p, tr = b
        va = run_funding_backtest({test_base: data[test_base]}, p, DEFAULT_LIMITS)
        vk = va["kpis"]
        print(f"  fit {'+'.join(fit_bases):8} -> test {test_base:3} | entry>{int(p['entry_ann_funding']*100)}% hold{p['max_hold_days']}d "
              f"| FIT sh {sharpe(tr['returns']):+.3f} || {test_base} sh {sharpe(va['returns']):+.3f} "
              f"ret {(vk.get('total_return') or 0.0):+.3f} (n={vk.get('trades', 0)})")

    fit_eval(["BTC", "ETH"], "SOL")
    fit_eval(["BTC", "SOL"], "ETH")
    fit_eval(["ETH", "SOL"], "BTC")
    print("\nrouter lineage:", sources.LINEAGE[:3], "...")


if __name__ == "__main__":
    main()
