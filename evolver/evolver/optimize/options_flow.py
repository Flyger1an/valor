"""Options forced-flow family (#5) — the max-pain pin. As a dominant options expiry approaches, spot
tends to gravitate toward the strike that expires the most open interest worthless ("max-pain"): dealers
hedging their books and holders defending positions create mechanical flow toward it. The family enters
when (a) a big expiry is near (dte <= dte_max) and (b) spot is displaced from that expiry's max-pain by
>= gap_min, trades TOWARD the pin, and holds `hold_days`. Judged by the SAME gate as every family.

Deribit serves NO historical OI-by-strike, so the data is FORWARD-ACCUMULATED (one snapshot/day):
universe = {coin: {day_ms: (spot, {"max_pain","oi_wall","total_oi","pcr","dte"})}}.  Max-pain needs no
dealer-positioning assumption (sign-agnostic); the search picks trade_dir, so the gate decides whether
the pin pulls toward (dir +1) or repels from (dir -1) — or neither (rejected).
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS

DEFAULT_PARAMS = {"gap_min": 0.02, "dte_max": 14.0, "hold_days": 5, "trade_dir": 1.0,
                  "fee_bps": 5.0, "slip_bps": 8.0}


def max_pain(strikes: dict):
    """argmin over settlement S of total holder payout = Σ call_oi·max(S−K,0) + put_oi·max(K−S,0)."""
    ks = sorted(strikes)
    if not ks:
        return None
    return min(ks, key=lambda S: sum(strikes[K][0] * max(S - K, 0.0) + strikes[K][1] * max(K - S, 0.0)
                                     for K in ks))


def oi_wall(strikes: dict):
    """strike carrying the most total OI — the heaviest hedging level (sign-agnostic)."""
    return max(strikes, key=lambda K: strikes[K][0] + strikes[K][1]) if strikes else None


def run_options_pin(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    gap_min, dte_max, hold = p["gap_min"], p["dte_max"], int(p["hold_days"])
    cost = (p["fee_bps"] + p["slip_bps"]) / 1e4 * 2.0      # round-trip frictions
    out = []
    for coin in universe:
        ts = sorted(universe[coin])
        rows = [universe[coin][t] for t in ts]
        spots = [r[0] if isinstance(r, (tuple, list)) else None for r in rows]
        for i in range(len(ts) - hold):
            if lo is not None and ts[i] < lo:
                continue
            if hi is not None and ts[i] >= hi:
                break
            r = rows[i]
            if not isinstance(r, (tuple, list)) or len(r) < 2 or not isinstance(r[1], dict):
                continue
            S, fwd = spots[i], spots[i + hold]
            mp, dte = r[1].get("max_pain"), r[1].get("dte", 99.0)
            if not S or S <= 0 or not fwd or fwd <= 0 or not mp or dte > dte_max:
                continue
            gap = (mp - S) / S                                 # signed displacement to the pin
            if abs(gap) < gap_min:
                continue
            sig = (1.0 if gap > 0 else -1.0) * p["trade_dir"]  # trade toward (dir +1) the pin
            out.append((ts[i], sig * (fwd / S - 1.0) - cost))
    out.sort()
    return out
