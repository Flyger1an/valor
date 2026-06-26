"""Open-interest-conditioned reversion — a POSITIONING/flow edge, not a price pattern.

Thesis (structural, not statistical): a price move accompanied by a SURGE in open interest is driven by
FRESH leverage piling in (FOMO longs, crowded shorts) — over-leveraged moves are fragile and tend to
revert. A move on FALLING open interest is deleveraging/position-closing — cleaner, less prone to snap
back. So: only fade a move when open interest surged with it. OI is the 'how much new leverage is in
this move' signal — data the price-only families never see, which is the whole point.

universe: {coin: {day_ms: (close, open_interest)}}  (daily; OI from okx_oi_history, accumulated).
Returns [(entry_ts, net_return), ...] pooled across coins. Next-bar fill (signal on close[i], enter at
close[i+1]) — no using the signal bar as its own entry. Honest round-trip cost shared with the shadow.
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS
from evolver.optimize.liquidation_reversion import round_trip_cost

DEFAULT_PARAMS = {"lookback": 7, "holding": 5, "oi_thresh": 0.10, "ret_thresh": 0.05,
                  "trade_dir": 1, "fee_bps": 5.0, "slip_bps": 5.0, "funding_bps_8h": 1.5}


def _cl_oi(v):
    """Accept (close, oi) tuples; tolerate a bare close (oi unknown -> skip via None)."""
    if isinstance(v, (tuple, list)):
        return (v[0], v[1]) if len(v) >= 2 else (v[0], None)
    return (v, None)


def run_oi_reversion(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    lb, hold = int(p["lookback"]), int(p["holding"])
    oi_thr, ret_thr = p["oi_thresh"], p["ret_thresh"]
    sgn = 1.0 if p["trade_dir"] >= 0 else -1.0
    cost = round_trip_cost(p["fee_bps"], hold * 24, p["slip_bps"], p["funding_bps_8h"])  # daily hold -> hours
    out = []
    for coin in universe:
        ts = sorted(universe[coin])
        n = len(ts)
        if n < lb + hold + 4:
            continue
        cl = [_cl_oi(universe[coin][t])[0] for t in ts]
        oi = [_cl_oi(universe[coin][t])[1] for t in ts]
        last = -10 ** 9
        for i in range(lb, n - hold - 1):
            if i < last + hold:                       # no overlapping same-coin trades
                continue
            if lo is not None and ts[i] < lo:
                continue
            if hi is not None and ts[i] >= hi:
                break
            c0, cL, oi0, oiL = cl[i], cl[i - lb], oi[i], oi[i - lb]
            if not (c0 and cL and oi0 and oiL and cL > 0 and oiL > 0):
                continue
            ret = c0 / cL - 1
            doi = oi0 / oiL - 1
            if abs(ret) < ret_thr or doi < oi_thr:    # need a move AND an OI surge (fresh leverage)
                continue
            entry, exit_ = cl[i + 1], cl[i + 1 + hold]
            if not (entry and exit_ and entry > 0):
                continue
            side = sgn * (-1.0 if ret > 0 else 1.0)   # FADE the leverage-driven move
            out.append((ts[i + 1], side * (exit_ / entry - 1) - cost))
            last = i
    out.sort()
    return out
