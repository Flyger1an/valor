"""OUT-OF-SAMPLE edge search.

Split the OKX history 70/30 by date. Grid-search the strategy params on TRAIN
(maximize deflated Sharpe — penalizes overfitting). Take the single best config and
report its VALIDATION (out-of-sample) performance. Answers the only question that
matters now: is there any OOS edge, or is the signal family flat?

    python3 scripts/oos_search.py
"""
from __future__ import annotations

import itertools
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.data.okx import okx_daily_closes  # noqa: E402
from evolver.optimize.forward_backtest import run_forward_backtest  # noqa: E402
from evolver.optimize.promotion import penalized_sharpe, sharpe  # noqa: E402

PAIRS = [
    ("ETH", "BTC", "cointegration_spread"),
    ("ETH", "SOL", "stat_arb_pair"),
    ("SOL", "BTC", "stat_arb_pair"),
    ("BTC", "SOL", "stat_arb_pair"),
]
GRID = {
    "window": [20, 30, 40],
    "z_entry": [1.0, 1.5, 2.0],
    "z_exit": [0.0, 0.5],
    "max_hold_days": [7, 14, 21],
}


def main() -> None:
    bases = sorted({b for p in PAIRS for b in p[:2]})
    closes = {b: okx_daily_closes(b, limit=300) for b in bases}
    all_ts = sorted({t for b in bases for t in closes[b]})
    split = all_ts[int(len(all_ts) * 0.7)]  # 70/30 train/valid by date

    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    print(f"OOS search: {len(combos)} param sets | {len(all_ts)} days | train<split<valid")

    best = None
    for p in combos:
        tr = run_forward_backtest(closes, PAIRS, p, DEFAULT_LIMITS, hi=split)
        if len(tr["returns"]) < 15:  # need enough train trades to mean anything
            continue
        score = penalized_sharpe(tr["returns"], len(combos))
        if best is None or score > best[0]:
            best = (score, p, tr)

    if not best:
        print("no param set produced enough train trades")
        return

    _score, p, tr = best
    va = run_forward_backtest(closes, PAIRS, p, DEFAULT_LIMITS, lo=split)
    tr_sh, va_sh = sharpe(tr["returns"]), sharpe(va["returns"])

    print(f"\nBEST params (by TRAIN deflated-Sharpe): {p}")
    print(f"  TRAIN      : trades {tr['kpis']['trades']:>3} | sharpe/trade {tr_sh:+.3f} | "
          f"return {tr['kpis'].get('total_return'):+.3f} | equity ${tr['equity']:,.0f}")
    print(f"  VALID (OOS): trades {va['kpis']['trades']:>3} | sharpe/trade {va_sh:+.3f} | "
          f"return {va['kpis'].get('total_return'):+.3f} | PF {va['kpis'].get('profit_factor')} | "
          f"maxDD {va['kpis'].get('max_dd')} | equity ${va['equity']:,.0f}")
    print(f"\nVERDICT: out-of-sample Sharpe/trade = {va_sh:+.3f} -> ", end="")
    if len(va["returns"]) < 8:
        print("INSUFFICIENT OOS sample to judge")
    elif va_sh > 0.10:
        print("a lead worth pursuing (validate further before trusting)")
    else:
        print("NO meaningful OOS edge — the signal family is flat; needs fundamental")
        print("         research (intraday, cointegration tests, funding), not tuning.")


if __name__ == "__main__":
    main()
