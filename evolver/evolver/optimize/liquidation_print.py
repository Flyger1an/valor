"""Liquidation-PRINT reversion — the real-forced-flow version of liquidation_reversion.

liquidation_reversion fades a big WICK (a candle that MIGHT be a liquidation cascade). This fades an
ACTUAL liquidation cascade, measured from the exchange's liquidation feed: when forced liquidations
spike far above their recent baseline, the forced flow overshoots and reverts. It even knows the SIDE —
long liquidations are forced SELLS (price pushed down -> fade UP); short liquidations are forced BUYS
(pushed up -> fade DOWN) — which a wick can't tell you. This is the same thesis on strictly better data.

universe: {coin: {hour_ms: (close, long_liq_notional, short_liq_notional)}}.
Returns [(entry_ts, net_return), ...] pooled. Next-bar fill; honest round_trip_cost shared with shadow.

BAR-AGNOSTIC: the loop steps by bar INDEX, so lookback/hold/cooldown are in BARS. The only
wall-clock assumption is the funding drag — pass bar_hours (default 1.0; 24.0 for a daily
universe like the Coinalyze-backfilled liq_print_daily family) so the hold's funding cost is
charged for its true duration.
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS
from evolver.optimize.liquidation_reversion import LIQ_SLIP_BPS, round_trip_cost

DEFAULT_PARAMS = {"liq_mult": 5.0, "lookback": 72, "hold_hours": 8, "cooldown_h": 6,
                  "trade_dir": 1, "fee_bps": 8.0, "slip_bps": LIQ_SLIP_BPS, "funding_bps_8h": 1.5}


def _row(v):
    """(close, long_liq, short_liq) from a 3-tuple; tolerate a bare close (no liq -> never triggers)."""
    if isinstance(v, (tuple, list)):
        return v[0], (v[1] if len(v) > 1 else 0.0) or 0.0, (v[2] if len(v) > 2 else 0.0) or 0.0
    return v, 0.0, 0.0


def run_liquidation_print(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    mult, lb, hold, cd = p["liq_mult"], int(p["lookback"]), int(p["hold_hours"]), int(p["cooldown_h"])
    sgn = 1.0 if p["trade_dir"] >= 0 else -1.0
    cost = round_trip_cost(p["fee_bps"], hold * p.get("bar_hours", 1.0),
                           p["slip_bps"], p["funding_bps_8h"])
    out = []
    for coin in universe:
        ts = sorted(universe[coin])
        n = len(ts)
        if n < lb + hold + 4:
            continue
        rows = [_row(universe[coin][t]) for t in ts]
        cl = [r[0] for r in rows]
        tot = [r[1] + r[2] for r in rows]
        win = sum(tot[0:lb])                          # rolling window sum of liquidation notional
        last = -10 ** 9
        for i in range(lb, n - hold - 1):
            base = win / lb
            win += tot[i] - tot[i - lb]               # advance the window to [i-lb+1 .. i]
            if i < last + cd:
                continue
            if lo is not None and ts[i] < lo:
                continue
            if hi is not None and ts[i] >= hi:
                break
            if base <= 0 or tot[i] < mult * base:     # need a liquidation SPIKE vs the recent baseline
                continue
            ll, sl = rows[i][1], rows[i][2]
            if ll == sl:
                continue
            side = sgn * (1.0 if ll > sl else -1.0)   # longs liquidated (forced sells) -> fade UP
            entry, exit_ = cl[i + 1], cl[i + 1 + hold]
            if not (entry and exit_ and entry > 0):
                continue
            out.append((ts[i + 1], side * (exit_ / entry - 1) - cost))
            last = i
    out.sort()
    return out
