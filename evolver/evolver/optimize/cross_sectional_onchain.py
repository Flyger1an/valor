"""Cross-sectional long/short with ON-CHAIN factors (DefiLlama TVL) added to price factors.

Tests whether on-chain fundamentals carry alpha that price alone doesn't. Five factors,
cross-sectionally z-scored each rebalance, blended by signed weights:
  price:    momentum, short-term reversal, volatility
  on-chain: TVL momentum (capital flowing into the network/protocol),
            price-vs-TVL divergence (price ran ahead of / behind the locked value)
Long top quantile / short bottom, dollar-neutral, hold `holding` days, charge turnover.

universe: {coin: (px{day_ms:close}, tvl{day_ms:tvl})}. No look-ahead (factors use t-skip).
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS

DEFAULT_PARAMS = {"w_mom": 1.0, "w_rev": 0.0, "w_vol": 0.0, "w_tvlmom": 0.0, "w_tvldiv": 0.0,
                  "lookback": 30, "holding": 7, "quantile": 0.2, "skip": 1, "fee_bps": 5.0}


def _z(vals):
    xs = [v for v in vals if v is not None]
    if len(xs) < 3:
        return [0.0 for _ in vals]
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5
    if sd == 0:
        return [0.0 for _ in vals]
    return [((v - m) / sd if v is not None else 0.0) for v in vals]


def _ffill(series, dates):
    last, out = None, []
    for d in dates:
        if d in series:
            last = series[d]
        out.append(last)
    return out


def run_cross_sectional_onchain(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    coins = sorted(universe)
    dates = sorted(set().union(*[set(universe[c][0]) for c in coins])) if coins else []
    if len(dates) < 120:
        return []
    px, tv, ret = {}, {}, {}
    for c in coins:
        pxs, tvs = universe[c]
        a = _ffill(pxs, dates)
        px[c] = a
        tv[c] = _ffill(tvs, dates)
        ret[c] = [None] + [(a[i] / a[i - 1] - 1) if (a[i] and a[i - 1] and a[i - 1] > 0) else None
                           for i in range(1, len(a))]

    lb, hold, sk = int(p["lookback"]), int(p["holding"]), int(p["skip"])
    q = p["quantile"]
    W = (p["w_mom"], p["w_rev"], p["w_vol"], p["w_tvlmom"], p["w_tvldiv"])
    fee = 2 * p["fee_bps"] / 1e4
    out, n = [], len(dates)
    t = lb + sk + 4
    while t + hold < n:
        if lo is not None and dates[t] < lo:
            t += hold
            continue
        if hi is not None and dates[t] >= hi:
            break
        mom, rev, vol, tmom, tdiv, present = [], [], [], [], [], []
        for c in coins:
            a = px[c]
            p0, pl, ps = a[t - sk], a[t - sk - lb], a[t - sk - 3]
            if not (p0 and pl and ps and p0 > 0 and pl > 0 and ps > 0):
                continue
            rr = [x for x in ret[c][t - lb:t] if x is not None]
            if len(rr) < lb // 2:
                continue
            pmom = p0 / pl - 1
            mom.append(pmom)
            rev.append(p0 / ps - 1)
            mn = sum(rr) / len(rr)
            vol.append((sum((x - mn) ** 2 for x in rr) / len(rr)) ** 0.5)
            tv0, tvl_ = tv[c][t - sk], tv[c][t - sk - lb]
            if tv0 and tvl_ and tv0 > 0 and tvl_ > 0:
                tm = tv0 / tvl_ - 1
                tmom.append(tm)
                tdiv.append(tm - pmom)              # TVL grew faster than price => undervalued
            else:
                tmom.append(None)
                tdiv.append(None)
            present.append(c)
        if len(present) < 10:
            t += hold
            continue
        zs = [_z(mom), _z(rev), _z(vol), _z(tmom), _z(tdiv)]
        score = [sum(W[k] * zs[k][i] for k in range(5)) for i in range(len(present))]
        order = sorted(range(len(present)), key=lambda i: -score[i])
        k = max(1, int(len(present) * q))
        longs, shorts = order[:k], order[-k:]

        def fwd(i):
            a = px[present[i]]
            return (a[t + hold] / a[t] - 1) if (a[t] and a[t + hold] and a[t] > 0) else 0.0

        lr = sum(fwd(i) for i in longs) / len(longs)
        sr = sum(fwd(i) for i in shorts) / len(shorts)
        out.append((dates[t], (lr - sr) - fee))
        t += hold
    return out
