"""Thesis test: does funding-conditioning SHARPEN liquidation reversion, or just THIN trades?

Synthetic OHLC+funding (5-tuple bars). ~8% of bars are liquidation wicks. We inject the reversion
edge under two scenarios and compare funding_min=0 (take every wick) vs funding_min>0 (only fade when
the FLUSHED side was the crowded one, i.e. funding extreme in the matching direction):

  A) CONCENTRATED  — edge exists ONLY on crowded-funding wicks (the structural thesis is TRUE)
  B) AGNOSTIC      — edge exists on ALL wicks regardless of funding (thesis FALSE)

If the filter helps in A but only thins in B, it's a structural BET, not a free lunch — and worth
wiring as a family the gate can test on REAL data (where we don't know which world we're in).

    python3 scripts/funding_thesis_test.py
"""
from __future__ import annotations

import math
import random
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evolver.optimize.liquidation_reversion import run_liquidation_reversion as RLR  # noqa: E402


def _sharpe(rets):
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    sd = (sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def gen(n_coins, n_bars, seed, edge, fmin, concentrated, hold_edge=8, vol=0.010):
    base, step = 1_600_000_000_000, 3_600_000
    rng = random.Random(seed)
    data = {}
    for ci in range(n_coins):
        fund, f = [0.0] * n_bars, 0.0
        for i in range(n_bars):
            f = 0.97 * f + rng.gauss(0, 0.0004)         # AR(1) funding ~ N(0, ~0.0016) marginal
            fund[i] = f
        rets = [rng.gauss(0, vol) for _ in range(n_bars)]
        wick = {}
        for i in range(120, n_bars - hold_edge - 2):
            if rng.random() < 0.08:
                d = 1 if rng.random() < 0.5 else -1     # +1 down-wick (fade long), -1 up-wick (short)
                wick[i] = d
                crowded = (d == 1 and fund[i] >= fmin) or (d == -1 and fund[i] <= -fmin)
                if concentrated and not crowded:
                    continue                            # no edge on non-crowded wicks
                for k in range(1, hold_edge + 1):
                    rets[i + k] += d * (edge / hold_edge)
        p, px = 100.0 * math.exp(rng.gauss(0, 0.5)), []
        for i in range(n_bars):
            nx = p * math.exp(rets[i]); px.append((p, nx)); p = nx
        s = {}
        for i in range(n_bars):
            o, c = px[i]; d = wick.get(i, 0); sm = abs(rng.gauss(0, 0.006))
            if d == 1:
                big = rng.uniform(0.04, 0.11); l, h = min(o, c) * (1 - big), max(o, c) * (1 + sm)
            elif d == -1:
                big = rng.uniform(0.04, 0.11); h, l = max(o, c) * (1 + big), min(o, c) * (1 - sm)
            else:
                w = abs(rng.gauss(0, vol))
                l, h = (min(o, c) * (1 - w), max(o, c) * (1 + sm)) if rng.random() < 0.5 \
                    else (min(o, c) * (1 - sm), max(o, c) * (1 + w))
            s[base + i * step] = (o, h, l, c, fund[i])      # 5-tuple: funding rides on the bar
        data[f"F{ci}"] = s
    return data


def run(edge, fmin, concentrated):
    params = {"wick_atr": 3.0, "hold_hours": 8, "body_max": 0.6, "cooldown_h": 4, "atr_window": 48}
    rows = []
    for label, fm in [("filter OFF", 0.0), ("filter ON ", fmin)]:
        ns, shs = [], []
        for t in range(8):
            data = gen(16, 1500, 2000 + t, edge, fmin, concentrated)
            tr = RLR(data, {**params, "funding_min": fm})
            ns.append(len(tr))
            if len(tr) > 1:
                shs.append(_sharpe([v for _, v in tr]))
        rows.append((label, sum(ns) / len(ns), sum(shs) / len(shs) if shs else 0.0))
    return rows


def main():
    edge, fmin = 0.020, 0.0008
    print(f"funding thesis: {edge*1e4:.0f}bps edge/event, filter threshold |funding|>={fmin*1e4:.0f}bps\n")
    for name, conc in [("A) CONCENTRATED (edge only on crowded-funding wicks — thesis TRUE)", True),
                       ("B) AGNOSTIC (edge on all wicks regardless of funding — thesis FALSE)", False)]:
        print(name)
        for label, n, sh in run(edge, fmin, conc):
            print(f"     {label}: {n:5.0f} trades/run | per-trade Sharpe {sh:+.3f}")
        print()
    print("Read: in A, 'filter ON' Sharpe should JUMP (isolates the edge). In B, it should only LOSE")
    print("trades at similar/worse Sharpe. => the funding filter is a structural bet, not a free lunch.")


if __name__ == "__main__":
    main()
