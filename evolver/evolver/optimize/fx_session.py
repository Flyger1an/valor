"""FX session/fix seasonality — the FX twin of funding_session.

FX flow clusters by session: the Tokyo/London/NY opens, the London-NY overlap, and the 16:00 London
fix show repeatable intraday patterns. We trade one UTC hour-of-day, with direction conditioned on the
prior-window move (continue or fade the pre-session range). The search picks the hour, the hold, the
pre-window length, and continue-vs-fade.

universe: {pair: {ts_ms: (o,h,l,c)}} hourly. Entry at the NEXT bar's open; honest round-trip cost —
FX majors are cheap (~a pip), so default fee/slip are small and there is no perp funding term.
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS
from evolver.optimize.liquidation_reversion import round_trip_cost

# UTC hours where FX flow concentrates: Tokyo open, pre-London, London open/AM, NY ramp, London-NY
# overlap, and the 16:00 London fix. Searching these 8 (not all 24) keeps the discrete search tractable
# AND encodes the real prior that FX seasonality is session-driven — domain knowledge, not data-snooping.
SESSION_HOURS = [0, 6, 7, 8, 12, 13, 15, 16]

DEFAULT_PARAMS = {"session_idx": 3, "hold_hours": 6, "lookback": 6, "trade_dir": 1.0,
                  "fee_bps": 0.5, "slip_bps": 1.0, "funding_bps_8h": 0.0}


def run_fx_session(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    hour0 = SESSION_HOURS[int(p["session_idx"]) % len(SESSION_HOURS)]   # search a session, not 1-of-24
    hold, lb = int(p["hold_hours"]), int(p["lookback"])
    sgn = 1.0 if p["trade_dir"] >= 0 else -1.0          # continue (+) or fade (-) the pre-session move
    cost = round_trip_cost(p["fee_bps"], hold, p["slip_bps"], p["funding_bps_8h"])
    out = []
    for pair in universe:
        ts = sorted(universe[pair])
        bars = [universe[pair][t] for t in ts]
        n = len(ts)
        for i in range(lb, n - hold - 1):
            if lo is not None and ts[i] < lo:
                continue
            if hi is not None and ts[i] >= hi:
                break
            if (ts[i] // 3_600_000) % 24 != hour0:      # only the chosen UTC hour fires
                continue
            c0, cprev = bars[i][3], bars[i - lb][3]
            if c0 <= 0 or cprev <= 0:
                continue
            prior = c0 / cprev - 1                       # the pre-session move
            if prior == 0:
                continue
            entry = bars[i + 1][0]
            if entry <= 0:
                continue
            side = sgn * (1.0 if prior > 0 else -1.0)
            net = side * (bars[i + 1 + hold][3] / entry - 1) - cost
            out.append((ts[i + 1], net))
    out.sort()
    return out
