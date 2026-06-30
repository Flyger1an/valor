"""Options pricing + delta-hedged P&L — the greenfield core of the vol-premium build (#4, Phase 2).

The spot families never needed this: to harvest the variance risk premium you SELL an option at implied
vol and delta-hedge it along the realized path. If realized vol comes in below implied you keep the
premium; if it spikes you bleed gamma — the fat left tail. This module is the honest machine that
measures exactly that, net of hedging frictions, so the gate (Phase 3) can judge whether the gross
premium survives as a tradeable one. Pure-Python (no numpy on the box).

Conventions: sigma is annualized vol as a DECIMAL (0.50 = 50% = DVOL 50). T in YEARS. r defaults to 0
(crypto, no carry beyond funding). All checked against textbook Black-Scholes in tests/test_vol_pnl.py.
"""
from __future__ import annotations

import math

_SQRT2PI = math.sqrt(2 * math.pi)


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT2PI


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1d2(S, K, T, sigma, r):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None, None
    v = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / v
    return d1, d1 - v


def bs_price(S, K, T, sigma, r=0.0, kind="call") -> float:
    d1, d2 = _d1d2(S, K, T, sigma, r)
    if d1 is None:                                  # expired / no vol -> intrinsic
        return max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)
    disc = math.exp(-r * T)
    if kind == "call":
        return S * _ncdf(d1) - K * disc * _ncdf(d2)
    return K * disc * _ncdf(-d2) - S * _ncdf(-d1)


def bs_delta(S, K, T, sigma, r=0.0, kind="call") -> float:
    d1, _ = _d1d2(S, K, T, sigma, r)
    if d1 is None:
        if kind == "call":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    return _ncdf(d1) if kind == "call" else _ncdf(d1) - 1.0


def bs_greeks(S, K, T, sigma, r=0.0, kind="call") -> dict:
    """delta, gamma, vega (per 1.00 vol), theta (per YEAR). All standard Black-Scholes."""
    d1, d2 = _d1d2(S, K, T, sigma, r)
    if d1 is None:
        return {"delta": bs_delta(S, K, T, sigma, r, kind), "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    rt = math.sqrt(T)
    gamma = _npdf(d1) / (S * sigma * rt)
    vega = S * _npdf(d1) * rt
    if kind == "call":
        theta = -S * _npdf(d1) * sigma / (2 * rt) - r * K * math.exp(-r * T) * _ncdf(d2)
        delta = _ncdf(d1)
    else:
        theta = -S * _npdf(d1) * sigma / (2 * rt) + r * K * math.exp(-r * T) * _ncdf(-d2)
        delta = _ncdf(d1) - 1.0
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def _straddle_delta(S, K, T, sigma, r):
    return bs_delta(S, K, T, sigma, r, "call") + bs_delta(S, K, T, sigma, r, "put")


def delta_hedged_straddle_pnl(prices, sigma_imp, T_years, r=0.0,
                              fee_bps=5.0, slip_bps=5.0, option_spread_frac=0.02) -> float:
    """P&L of ONE short ATM straddle, delta-hedged along `prices` (underlying closes, len = steps+1,
    spanning T_years), sold at implied vol `sigma_imp`. Returns NET P&L as a fraction of the entry
    underlying (a per-trade return). The realized-vs-implied spread and the vol-spike tail both fall out
    of the actual path — nothing is assumed. Costs: option bid/ask (`option_spread_frac` of premium each
    side) + hedge fee+slippage (bps on each rehedge). Rehedge frequency = the granularity of `prices`."""
    n = len(prices) - 1
    if n < 2 or sigma_imp <= 0 or prices[0] <= 0:
        return 0.0
    S0 = prices[0]
    K = S0
    dt = T_years / n
    cost = (fee_bps + slip_bps) / 1e4

    prem = (bs_price(S0, K, T_years, sigma_imp, r, "call")
            + bs_price(S0, K, T_years, sigma_imp, r, "put"))
    pnl = prem * (1 - option_spread_frac)                  # premium received, minus entry bid/ask
    h = _straddle_delta(S0, K, T_years, sigma_imp, r)      # hold h underlying to neutralize short straddle
    pnl -= abs(h) * S0 * cost                              # cost to put the hedge on

    for i in range(1, n + 1):
        pnl += h * (prices[i] - prices[i - 1])             # hedge P&L over the step
        if i < n:
            tau = max(T_years - i * dt, 1e-9)
            h_new = _straddle_delta(prices[i], K, tau, sigma_imp, r)
            pnl -= abs(h_new - h) * prices[i] * cost       # rehedge cost
            h = h_new

    S_T = prices[n]
    pnl -= abs(S_T - K) * (1 + option_spread_frac)          # buy back at intrinsic + exit bid/ask
    pnl -= abs(h) * S_T * cost                              # close the hedge
    return pnl / S0
