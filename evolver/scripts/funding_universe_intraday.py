"""Risk-modeled funding-carry at universe scale — the real verdict.

Same fixed a-priori rule, but on HOURLY data with liquidation + slippage modeled.
If the carry survives with a *realistic* drawdown -> first promotable edge. If the
tail eats it -> we killed a classic carry blowup in paper, for free.

    python3 scripts/funding_universe_intraday.py
"""
from __future__ import annotations

import pathlib
import random
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.data import binance_dumps as bd  # noqa: E402
from evolver.optimize.funding_intraday import run_intraday_funding  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOGE", "LINK",
         "DOT", "LTC", "ATOM", "ARB", "OP"]
MONTHS = 26  # reach back into the 2024 high-funding bull (calm 2025-26 alone = no trades)
PARAMS = {"entry_ann_funding": 0.10, "exit_ann_funding": 0.03, "max_hold_hours": 168,
          "maker_bps": 2.0, "slip_bps": 4.0, "slip_funding_k": 12.0}


def _sharpe(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def load_coin(c):
    try:
        perp = bd.intraday_ohlc(f"{c}USDT", "futures/um", "1h", MONTHS)
        spot_o = bd.intraday_ohlc(f"{c}USDT", "spot", "1h", MONTHS)
        fund = bd.funding_history(f"{c}USDT", MONTHS)
        spot = {t: v[3] for t, v in spot_o.items()}
        if perp and spot and fund:
            return c, (spot, perp, fund)
    except Exception:
        pass
    return c, None


def main():
    print(f"fetching {len(COINS)} coins x 1h (perp OHLC + spot + funding), {MONTHS}mo...")
    data = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for c, d in ex.map(load_coin, COINS):
            if d:
                data[c] = d
    print(f"loaded {len(data)}/{len(COINS)}: {sorted(data)}")
    if len(data) < 6:
        print("too few coins")
        return

    print(f"\n=== BASIS-MANAGED sweep — intraday stop caps the adverse excursion ({len(data)} coins) ===")
    print(f"{'stop':>5} {'trades':>7} {'sharpe':>8} {'win':>6} {'return':>8} {'maxDD':>8} "
          f"{'wMAE':>7} {'stops':>6} {'liq':>4} {'coins+':>7} {'p':>6}")
    rng = random.Random(7)
    best = None
    for stop in [None, 0.01, 0.02, 0.03, 0.05]:
        p = {**PARAMS, "basis_stop": stop}
        res = run_intraday_funding(data, p, DEFAULT_LIMITS)
        rets, k = res["returns"], res["kpis"]
        n = len(rets)
        sh = _sharpe(rets)
        boot = [_sharpe([rng.choice(rets) for _ in rets]) for _ in range(1500)] if n >= 2 else []
        pval = (sum(1 for s in boot if s <= 0) / len(boot)) if boot else 1.0
        pos = sum(1 for c in data
                  if (run_intraday_funding({c: data[c]}, p, DEFAULT_LIMITS)["kpis"].get("total_return") or 0) > 0)
        ret, wr, dd = k.get("total_return") or 0, k.get("win_rate") or 0, k.get("max_dd") or 0
        print(f"{str(stop):>5} {n:>7} {sh:>+8.3f} {wr:>6.2f} {ret:>+8.3f} {dd:>+8.3f} "
              f"{res['worst_intratrade_mae']:>7.2f} {res['stops']:>6} {res['liquidations']:>4} "
              f"{pos:>4}/{len(data)} {pval:>6.3f}")
        if sh > 0.10 and pval < 0.05 and ret > 0 and pos >= 0.6 * len(data):
            if best is None or sh > best[0]:
                best = (sh, stop)

    print()
    if best:
        print(f"VERDICT: basis-managed carry SURVIVES at stop={best[1]} "
              f"(Sharpe {best[0]:+.3f}, +return, broad, significant) -> promotable candidate ✅")
    else:
        print("VERDICT: no stop level yields a real edge — the stop trades big losses for many small")
        print("         ones (whipsaw); the carry stays dead. Honest kill stands.")


if __name__ == "__main__":
    main()
