"""Funding-carry OOS test: grid-search entry/exit/hold on TRAIN (deflated Sharpe),
report the winner's VALIDATION (out-of-sample). Honest verdict on whether funding
carry survives fees + basis risk OOS.

    python3 scripts/funding_oos.py
"""
from __future__ import annotations

import itertools
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.optimize.funding_backtest import load, run_funding_backtest  # noqa: E402
from evolver.optimize.promotion import penalized_sharpe, sharpe  # noqa: E402

BASES = ["BTC", "ETH", "SOL"]
GRID = {
    "entry_ann_funding": [0.08, 0.15, 0.30, 0.50],
    "exit_ann_funding": [0.0, 0.05],
    "max_hold_days": [7, 14, 30],
}


def main() -> None:
    print("loading real OKX spot + perp + funding history (BTC/ETH/SOL)...")
    data = load(BASES)
    all_days = sorted({d for b in data for d in data[b][2]})
    split = all_days[int(len(all_days) * 0.7)]
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    print(f"{len(all_days)} funding-days | train < split < valid | {len(combos)} param sets\n")

    best = None
    for p in combos:
        tr = run_funding_backtest(data, p, DEFAULT_LIMITS, hi=split)
        n = len(tr["returns"])
        if n < 8:
            continue
        score = penalized_sharpe(tr["returns"], len(combos))
        if best is None or score > best[0]:
            best = (score, p, tr)

    if not best:
        print("no param set produced enough train trades (regime had little extreme funding)")
        return

    _s, p, tr = best
    va = run_funding_backtest(data, p, DEFAULT_LIMITS, lo=split)
    tr_sh, va_sh = sharpe(tr["returns"]), sharpe(va["returns"])
    print(f"BEST params (TRAIN deflated-Sharpe): {p}")
    print(f"  TRAIN      : trades {tr['kpis']['trades']:>3} | sharpe/trade {tr_sh:+.3f} | "
          f"return {tr['kpis'].get('total_return'):+.3f} | funding ${tr['funding_collected_usd']:,.0f} | equity ${tr['equity']:,.0f}")
    print(f"  VALID (OOS): trades {va['kpis']['trades']:>3} | sharpe/trade {va_sh:+.3f} | "
          f"return {va['kpis'].get('total_return'):+.3f} | PF {va['kpis'].get('profit_factor')} | "
          f"maxDD {va['kpis'].get('max_dd')} | equity ${va['equity']:,.0f}")
    print(f"\nVERDICT: funding-carry OOS Sharpe/trade = {va_sh:+.3f} -> ", end="")
    if len(va["returns"]) < 6:
        print("INSUFFICIENT OOS sample (regime lacked extreme funding) — inconclusive")
    elif va_sh > 0.10:
        print("a REAL lead — validate further (more venues/history) before trusting")
    else:
        print("still no durable OOS edge in this window after fees+basis")


if __name__ == "__main__":
    main()
