"""Funding-settlement intraday seasonality — a calendar/microstructure strategy.

Thesis: OKX perp funding settles every 8h (00:00 / 08:00 / 16:00 UTC). Around the stamp, positions
are mechanically adjusted to collect or dodge the payment, leaving a small, repeatable directional
pressure whose SIGN depends on who is paying (the funding sign). We trade one fixed phase-hour of the
8h cycle, conditioned on the funding sign — a mechanical flow, not a behavioral guess.

universe: {coin: {ts_ms: (o,h,l,c, funding)}} hourly (same 5-tuple feed as the funding-liquidation
family). phase = hour_of_day % 8 (0 = a settlement hour, 1..7 = hours since). The search picks the
phase, the hold, whether to trade WITH or AGAINST the funding sign, and a funding-extremeness floor.
Entry at the NEXT bar's open (the phase is only known at the bar's close); honest round-trip cost.
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS
from evolver.optimize.liquidation_reversion import round_trip_cost

DEFAULT_PARAMS = {"entry_phase": 7, "hold_hours": 3, "trade_dir": 1.0, "funding_min": 0.0005,
                  "fee_bps": 6.0, "slip_bps": 6.0, "funding_bps_8h": 1.5}


def run_funding_session(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    phase0 = int(p["entry_phase"]) % 8
    hold = int(p["hold_hours"])
    fmin = p["funding_min"]
    sgn = 1.0 if p["trade_dir"] >= 0 else -1.0     # trade WITH (+) or AGAINST (-) the funding sign
    cost = round_trip_cost(p["fee_bps"], hold, p["slip_bps"], p["funding_bps_8h"])
    out = []
    for coin in universe:
        ts = sorted(universe[coin])
        bars = [universe[coin][t] for t in ts]
        n = len(ts)
        for i in range(n - hold - 1):
            if lo is not None and ts[i] < lo:
                continue
            if hi is not None and ts[i] >= hi:
                break
            if (ts[i] // 3_600_000) % 8 != phase0:          # only the chosen phase-hour fires
                continue
            fnd = bars[i][4] if len(bars[i]) > 4 else 0.0
            if fnd == 0.0 or abs(fnd) < fmin:               # need a funding payment worth gaming
                continue
            entry = bars[i + 1][0]                           # fill at NEXT bar's open
            if entry <= 0:
                continue
            side = sgn * (1.0 if fnd > 0 else -1.0)
            net = side * (bars[i + 1 + hold][3] / entry - 1) - cost
            out.append((ts[i + 1], net))
    out.sort()
    return out
