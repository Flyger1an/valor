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

# Execution-cost assumptions, SHARED with the live shadow book (scripts/shadow_runner.py imports
# round_trip_cost) so the gate and the paper book can never disagree about the same strategy.
LIQ_SLIP_BPS = 12.0            # spread/2 + market impact per side — a falling-knife taker into a thin book
LIQ_FUNDING_BPS_PER_8H = 1.5  # conservative funding drag over the hold (always a cost)

DEFAULT_PARAMS = {"wick_atr": 3.0, "hold_hours": 6, "body_max": 0.5, "cooldown_h": 12,
                  "atr_window": 48, "fee_bps": 8.0,
                  "slip_bps": LIQ_SLIP_BPS, "funding_bps_8h": LIQ_FUNDING_BPS_PER_8H}


def round_trip_cost(fee_bps, hold_hours, slip_bps=LIQ_SLIP_BPS, funding_bps_8h=LIQ_FUNDING_BPS_PER_8H):
    """Round-trip friction as a return-fraction: taker fee + spread/impact on BOTH legs + a funding
    drag over the hold. The ONE cost model for this strategy — the gate backtest and the live shadow
    both call it, so a gate PASS means 'tradeable at the same cost the shadow will hold it to'."""
    return 2 * (fee_bps + slip_bps) / 1e4 + funding_bps_8h * (hold_hours / 8.0) / 1e4


def run_liquidation_reversion(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    wick, hold, bodymax = p["wick_atr"], int(p["hold_hours"]), p["body_max"]
    cd, aw = int(p["cooldown_h"]), int(p["atr_window"])
    cost = round_trip_cost(p["fee_bps"], hold, p["slip_bps"], p["funding_bps_8h"])  # honest round-trip
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
        while i < n - hold - 1:                                # need bar i+1 (entry) .. i+1+hold (exit)
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
                entry = bars[i + 1][0]                           # fill at NEXT bar's OPEN — the signal
                if entry <= 0:                                    # is only known at bar i's close, and c
                    i += 1                                        # (that close) is the price that DEFINES
                    continue                                      # the wick, so filling at it is the lie
                net = sig * (bars[i + 1 + hold][3] / entry - 1) - cost
                out.append((ts[i + 1], net))
                last, i = i, i + hold + 1
            else:
                i += 1
    out.sort()
    return out
