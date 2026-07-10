"""Intraday reversion family (the first INTRADAY / day-trading-lite family) — fade a coin's own sharp
N-hour move on hourly bars.

Honest hypothesis: over a few hours a move that is large relative to the coin's recent hourly volatility
tends to partially revert (short-term overreaction / liquidity provision). Because it holds HOURS not
days, it trades far more often than the daily families and therefore meets the harshest 2x-cost stress in
the gate — more trades = more frictions. That is deliberate: it lets the honest gate render a verdict on
whether an intraday retail edge survives fees, which is exactly where day-trading styles usually die.

universe: {coin: {ts_ms: (o,h,l,c)}} — LIVE hourly OHLC (refresh_hourly). Signal at hour i: z = the
lb-hour move normalized by trailing hourly vol (x sqrt(lb), zero-mean); if |z| >= entry_z, FADE it, hold
`hold_hours`, exit at close. Entries are non-overlapping per coin (step by hold) so the block bootstrap
sees ~independent trades. Returns [(entry_ts, net_return)] pooled across coins.
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS

DEFAULT_PARAMS = {"lookback": 6, "entry_z": 2.0, "hold_hours": 4, "vol_window": 168,
                  "fee_bps": 5.0, "slip_bps": 6.0}


def _std(x):
    n = len(x)
    if n < 2:
        return 0.0
    m = sum(x) / n
    return (sum((v - m) ** 2 for v in x) / (n - 1)) ** 0.5


def _close(v):
    return v[3] if isinstance(v, (tuple, list)) and len(v) >= 4 else v


def run_intraday_reversion(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    lb, hold, vw = int(p["lookback"]), int(p["hold_hours"]), int(p["vol_window"])
    ez = p["entry_z"]
    cost = (p["fee_bps"] + p["slip_bps"]) / 1e4 * 2.0      # round-trip frictions (2 sides)
    out = []
    for coin in universe:
        ts = sorted(universe[coin])
        cl = [_close(universe[coin][t]) for t in ts]
        n = len(cl)
        if n < vw + lb + hold + 2 or any(c is None for c in cl):
            continue
        ret = [cl[i] / cl[i - 1] - 1 for i in range(1, n) if cl[i - 1]]   # hourly returns
        i = vw + lb
        while i + hold < n:
            if lo is not None and ts[i] < lo:
                i += 1
                continue
            if hi is not None and ts[i] >= hi:
                break
            if cl[i] <= 0 or cl[i - lb] <= 0 or cl[i + hold] <= 0:
                i += 1
                continue
            move = cl[i] / cl[i - lb] - 1                  # the lb-hour move to fade
            sd = _std(ret[i - vw:i])                       # trailing hourly-return vol
            if sd <= 0:
                i += 1
                continue
            z = move / (sd * (lb ** 0.5))                  # normalize by lb-hour vol (zero-mean)
            if abs(z) < ez:
                i += 1
                continue
            fwd = cl[i + hold] / cl[i] - 1
            direction = -1.0 if z > 0 else 1.0             # FADE: short a spike up, buy a spike down
            out.append((ts[i], direction * fwd - cost))
            i += hold                                      # non-overlapping -> ~independent trades
    out.sort()
    return out
