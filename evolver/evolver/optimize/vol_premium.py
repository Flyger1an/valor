"""Vol-premium family (#4, Phase 3) — harvests the variance risk premium via delta-hedged short
straddles, judged by the SAME gate as every spot family. It sells an ATM straddle when implied vol
(DVOL) is RICH vs its own recent history (IV-rank entry — the premium is fattest when vol is dear),
delta-hedges it over `tenor` days using the real path, and books the net P&L from
`delta_hedged_straddle_pnl`. The crash tail rides inside every trade (the simulator captures it), so the
gate's DSR / PBO / CONFIRM + 2x-cost stress must clear the edge net of frictions AND the tail to surface.

universe: {coin: {day_ms: (close, dvol)}}  — close = underlying, dvol = DVOL implied-vol index (vol pts).
Returns [(entry_ts, net_return), ...] pooled across coins (BTC + ETH = honest sample depth).
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS
from evolver.optimize.vol_pnl import delta_hedged_straddle_pnl

DEFAULT_PARAMS = {"tenor_days": 7, "iv_rank_min": 0.0, "lookback": 60, "skip": 0,
                  "fee_bps": 5.0, "slip_bps": 10.0, "option_spread_frac": 0.02}


def _row(v):
    """(close, dvol) from a (close, dvol) tuple; tolerate a bare close (no dvol -> skip)."""
    if isinstance(v, (tuple, list)):
        return (v[0], v[1]) if len(v) >= 2 else (v[0], None)
    return v, None


def run_vol_premium(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    tenor, lb = int(p["tenor_days"]), int(p["lookback"])
    ivmin = p["iv_rank_min"]
    out = []
    for coin in universe:
        ts = sorted(universe[coin])
        n = len(ts)
        if n < lb + tenor + 2:
            continue
        rows = [_row(universe[coin][t]) for t in ts]
        cl = [r[0] for r in rows]
        iv = [r[1] for r in rows]
        i = lb
        while i + tenor < n:
            if lo is not None and ts[i] < lo:
                i += tenor
                continue
            if hi is not None and ts[i] >= hi:
                break
            window = [x for x in iv[i - lb:i] if x is not None]
            if iv[i] is None or iv[i] <= 0 or len(window) < lb // 2:
                i += tenor
                continue
            rank = sum(1 for x in window if x <= iv[i]) / len(window)      # IV-rank: sell when vol is rich
            if rank < ivmin:
                i += tenor
                continue
            path = cl[i:i + tenor + 1]
            if any(x is None or x <= 0 for x in path):
                i += tenor
                continue
            out.append((ts[i], delta_hedged_straddle_pnl(
                path, iv[i] / 100.0, tenor / 365.0,
                fee_bps=p["fee_bps"], slip_bps=p["slip_bps"], option_spread_frac=p["option_spread_frac"])))
            i += tenor
    out.sort()
    return out
