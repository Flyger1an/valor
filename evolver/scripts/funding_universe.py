"""Funding-carry at universe scale (18 coins) — the honest significance test.

Fetch deep history for 18 coins in parallel (router prefer='depth' -> Binance dumps),
run a FIXED a-priori carry rule (no per-coin tuning), POOL every trade into one
sample, and test: bootstrap p-value that the edge is real, + how many coins are
individually positive (broad vs driven by 1-2 names).

    python3 scripts/funding_universe.py
"""
from __future__ import annotations

import pathlib
import random
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.data import sources  # noqa: E402
from evolver.optimize.funding_backtest import run_funding_backtest  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOGE", "LINK",
         "DOT", "LTC", "NEAR", "ATOM", "FIL", "ARB", "OP", "APT", "INJ"]
LOOKBACK = 730
# Fixed, a-priori (NOT tuned per coin): collect when ann-funding hot, short hold, maker.
PARAMS = {"entry_ann_funding": 0.10, "exit_ann_funding": 0.03, "max_hold_days": 7,
          "fee_bps": 2.0, "legs": 2}


def _sharpe(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def load_coin(c):
    try:
        fund, _ = sources.funding_daily(c, LOOKBACK, prefer="depth")
        perp, _ = sources.closes_daily(c, "perp", LOOKBACK, prefer="depth")
        spot, _ = sources.closes_daily(c, "spot", LOOKBACK, prefer="depth")
        if fund and perp and spot:
            return c, (spot, perp, fund)
    except Exception:
        pass
    return c, None


def main():
    print(f"fetching {len(COINS)} coins x (spot+perp+funding) in parallel...")
    data = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for c, d in ex.map(load_coin, COINS):
            if d:
                data[c] = d
    print(f"loaded {len(data)}/{len(COINS)}: {sorted(data)}\n")
    if len(data) < 6:
        print("too few coins loaded")
        return

    res = run_funding_backtest(data, PARAMS, DEFAULT_LIMITS)  # one shared book, all coins
    rets, k = res["returns"], res["kpis"]
    n = len(rets)

    rng = random.Random(7)
    boot = [_sharpe([rng.choice(rets) for _ in rets]) for _ in range(3000)] if n >= 2 else []
    pval = (sum(1 for s in boot if s <= 0) / len(boot)) if boot else 1.0

    pos = sum(1 for c in data
              if (run_funding_backtest({c: data[c]}, PARAMS, DEFAULT_LIMITS)["kpis"].get("total_return") or 0) > 0)

    print(f"POOLED across {len(data)} coins (FIXED a-priori params, no per-coin tuning):")
    print(f"  trades            {n}")
    print(f"  sharpe/trade      {_sharpe(rets):+.3f}")
    print(f"  bootstrap p(edge<=0) {pval:.3f}   {'<-- SIGNIFICANT' if pval < 0.05 else '(not significant)'}")
    print(f"  win_rate          {k.get('win_rate')}")
    print(f"  profit_factor     {k.get('profit_factor')}")
    print(f"  max_dd            {k.get('max_dd')}")
    print(f"  total_return      {k.get('total_return')}  (final equity ${res['equity']:,.0f})")
    print(f"  funding collected ${res['funding_collected_usd']:,.0f}")
    print(f"  per-coin breadth  {pos}/{len(data)} coins net positive")
    # smell test: a real carry does NOT have ~0 drawdown or PF>5. That's unmodeled risk.
    risk_suspect = abs(k.get("max_dd") or 0) < 0.01 or (k.get("profit_factor") or 99) > 5
    stats_ok = pval < 0.05 and _sharpe(rets) > 0.10 and pos >= 0.6 * len(data)
    print()
    if risk_suspect:
        print("⚠️  RISK UNDER-MODELED: max_dd≈0 and PF≫1 are impossible for a real carry.")
        print("    Daily closes make perp≈spot, so basis-blowout / liquidation / intraday funding-flip /")
        print("    slippage losses are INVISIBLE here. The funding *collection* is real and broad; the")
        print("    Sharpe/DD are fantasy until intraday + liquidation + slippage are modeled.")
    print(f"VERDICT: {'stats significant BUT risk unmodeled -> NOT promotable' if (stats_ok and risk_suspect) else ('REAL, BROAD, RISK-MODELED EDGE' if stats_ok else 'inconclusive')}")


if __name__ == "__main__":
    main()
