"""Liquidation-cascade reversion — a MEV-adjacent, event-driven strategy (NOT a factor).

Thesis: forced liquidations are non-informational selling that overshoots, then reverts. The
MEV searcher captures the liquidation bonus in-block (a latency race we can't win); we capture
the *reversion* on the perp (a minute-to-hour game where research wins). The liquidation
footprint is a violent intrabar WICK that snaps back: the low (or high) spikes far beyond the
bar's body in ATR units, then closes near the open. We fade it — long the dump, short the squeeze.

universe: {coin: {ts_ms: (o,h,l,c)}} hourly. Single-asset event strategy, pooled across coins.
Costs are deliberately HIGH (catching a falling knife is taker + slippage into volatility).
ATR via precomputed true-range prefix sum -> O(1) per bar.
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS

DEFAULT_PARAMS = {"wick_atr": 3.0, "hold_hours": 6, "body_max": 0.5, "cooldown_h": 12,
                  "atr_window": 48, "fee_bps": 8.0}


def run_liquidation_reversion(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    wick, hold, bodymax = p["wick_atr"], int(p["hold_hours"]), p["body_max"]
    cd, aw, fee = int(p["cooldown_h"]), int(p["atr_window"]), p["fee_bps"] / 1e4
    out = []
    for coin in universe:
        ts = sorted(universe[coin])
        bars = [universe[coin][t] for t in ts]
        n = len(ts)
        if n < aw + hold + 2:
            continue
        tr = [0.0] * n                                    # true range
        for j in range(1, n):
            h, l, pc = bars[j][1], bars[j][2], bars[j - 1][3]
            tr[j] = max(h - l, abs(h - pc), abs(l - pc))
        pref = [0.0] * (n + 1)                             # prefix sum -> O(1) rolling ATR
        for j in range(n):
            pref[j + 1] = pref[j] + tr[j]
        last, i = -10 ** 9, aw + 1
        while i < n - hold:
            if lo is not None and ts[i] < lo:
                i += 1
                continue
            if hi is not None and ts[i] >= hi:
                break
            if i - last < cd:
                i += 1
                continue
            o, h, l, c = bars[i]
            atr = (pref[i] - pref[i - aw]) / aw
            if atr <= 0 or c <= 0:
                i += 1
                continue
            body, rng = abs(c - o), h - l
            sig = 0
            if body <= bodymax * rng and rng > 0:
                if (min(o, c) - l) / atr >= wick:
                    sig = 1            # liquidation dump -> fade long
                elif (h - max(o, c)) / atr >= wick:
                    sig = -1           # liquidation squeeze -> fade short
            if sig:
                net = sig * (bars[i + hold][3] / c - 1) - 2 * fee   # round-trip taker + slippage
                out.append((ts[i], net))
                last, i = i, i + hold
            else:
                i += 1
    out.sort()
    return out
