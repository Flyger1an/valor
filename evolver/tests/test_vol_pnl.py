"""Phase 2 functional proof: Black-Scholes matches textbook values, and the delta-hedged simulator
profits when realized vol < implied, loses in a vol spike, and makes NO free money when implied =
realized. If any of these fail, the core doesn't function and the build stops."""
import math
import random

from evolver.optimize.vol_pnl import bs_price, bs_delta, bs_greeks, delta_hedged_straddle_pnl


def test_bs_atm_call_textbook():
    # S=K=100, T=1, sigma=0.20, r=0 -> classic 7.966
    assert abs(bs_price(100, 100, 1.0, 0.20, 0.0, "call") - 7.9656) < 0.01
    return True


def test_put_call_parity():
    # call - put == S - K*e^{-rT} for any inputs
    for S, K, T, sig, r in [(100, 100, 1, 0.2, 0.0), (120, 100, 0.5, 0.4, 0.0), (90, 110, 0.25, 0.6, 0.0)]:
        c, p = bs_price(S, K, T, sig, r, "call"), bs_price(S, K, T, sig, r, "put")
        assert abs((c - p) - (S - K * math.exp(-r * T))) < 1e-6
    return True


def test_delta_and_greek_signs():
    d = bs_delta(100, 100, 1, 0.2, 0, "call")
    assert abs(d - 0.5398) < 0.005                       # N(0.1)
    assert abs(bs_delta(100, 100, 1, 0.2, 0, "put") - (d - 1)) < 1e-9
    g = bs_greeks(100, 100, 0.25, 0.5, 0, "call")
    assert g["gamma"] > 0 and g["vega"] > 0 and g["theta"] < 0   # long option: +gamma/+vega, time decay
    return True


def _path(seed, n, daily_vol, drift=0.0, spike_at=None, spike=0.0):
    rng = random.Random(seed)
    S, out = 100.0, [100.0]
    for i in range(n):
        r = rng.gauss(drift, daily_vol) + (spike if spike_at == i else 0.0)
        S *= math.exp(r)
        out.append(S)
    return out


def test_short_vol_profits_when_realized_below_implied():
    # sell at 60% implied, realize ~10% (quiet) -> keep the premium on the vast majority of paths
    wins = sum(delta_hedged_straddle_pnl(_path(s, 30, 0.10 / math.sqrt(365)), 0.60, 30 / 365) > 0
               for s in range(30))
    assert wins >= 26, wins
    return True


def test_short_vol_loses_in_a_vol_spike():
    # sell at 60% implied, then a ~57% jump mid-life (vol event) -> lose on the vast majority
    losses = sum(delta_hedged_straddle_pnl(_path(s, 30, 0.10 / math.sqrt(365), spike_at=15, spike=0.45),
                                           0.60, 30 / 365) < 0 for s in range(30))
    assert losses >= 26, losses
    return True


def test_no_premium_no_free_money():
    # implied == realized (~30%) -> roughly break-even minus frictions; NOT a windfall (honesty check)
    pnls = [delta_hedged_straddle_pnl(_path(s, 30, 0.30 / math.sqrt(365)), 0.30, 30 / 365) for s in range(40)]
    mean = sum(pnls) / len(pnls)
    assert -0.06 < mean < 0.015, mean
    return True
