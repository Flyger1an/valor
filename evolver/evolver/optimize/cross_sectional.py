"""Cross-sectional long/short factor backtest over a large universe.

Each rebalance: score every coin by a blend of factors (momentum, short-term reversal,
low-vol), cross-sectionally z-scored; go long the top quantile, short the bottom quantile,
dollar-neutral, equal-weight; hold `holding` days; charge turnover. Returns a per-rebalance
(ts, return) series for the engine's honest fitness.

No look-ahead: factors at date t use closes up to t-skip; entry at close[t], exit close[t+hold].
This is the classic factor-zoo search — high-dimensional and maximally prone to overfitting,
which is the point: it stress-tests both the search and the deflation/PBO gate.
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS

DEFAULT_PARAMS = {"w_mom": 1.0, "w_rev": 0.0, "w_vol": 0.0, "lookback": 30,
                  "holding": 7, "quantile": 0.2, "skip": 1, "fee_bps": 4.0}


def _z(vals):
    xs = [v for v in vals if v is not None]
    if len(xs) < 3:
        return [0.0 if v is not None else None for v in vals]
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5
    if sd == 0:
        return [0.0 if v is not None else None for v in vals]
    return [((v - m) / sd if v is not None else None) for v in vals]


def run_cross_sectional(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    coins = sorted(universe)
    dates = sorted(set().union(*[set(universe[c]) for c in coins])) if coins else []
    if len(dates) < 120:
        return []
    # forward-filled close + daily-return arrays, indexed by the global date grid
    close, ret = {}, {}
    for c in coins:
        s, last, ca = universe[c], None, []
        for d in dates:
            if d in s:
                last = s[d]
            ca.append(last)
        close[c] = ca
        ret[c] = [None] + [(ca[i] / ca[i - 1] - 1) if (ca[i] and ca[i - 1] and ca[i - 1] > 0) else None
                           for i in range(1, len(ca))]

    lb, hold, sk = int(p["lookback"]), int(p["holding"]), int(p["skip"])
    q, wm, wr, wv = p["quantile"], p["w_mom"], p["w_rev"], p["w_vol"]
    fee = 2 * p["fee_bps"] / 1e4                       # two-sided turnover per rebalance
    out, n = [], len(dates)
    t = lb + sk + 4
    while t + hold < n:
        if lo is not None and dates[t] < lo:
            t += hold
            continue
        if hi is not None and dates[t] >= hi:
            break
        mom, rev, vol, present = [], [], [], []
        for c in coins:
            a = close[c]
            p0, pl, ps = a[t - sk], a[t - sk - lb], a[t - sk - 3]
            if p0 and pl and ps and p0 > 0 and pl > 0 and ps > 0:
                rr = [x for x in ret[c][t - lb:t] if x is not None]
                if len(rr) < lb // 2:
                    continue
                mom.append(p0 / pl - 1)
                rev.append(p0 / ps - 1)
                mn = sum(rr) / len(rr)
                vol.append((sum((x - mn) ** 2 for x in rr) / len(rr)) ** 0.5)
                present.append(c)
        if len(present) < 10:
            t += hold
            continue
        zm, zr, zv = _z(mom), _z(rev), _z(vol)
        score = [wm * (zm[i] or 0) + wr * (zr[i] or 0) + wv * (zv[i] or 0) for i in range(len(present))]
        order = sorted(range(len(present)), key=lambda i: -score[i])
        k = max(1, int(len(present) * q))
        longs, shorts = order[:k], order[-k:]

        def fwd(i):
            a = close[present[i]]
            return (a[t + hold] / a[t] - 1) if (a[t] and a[t + hold] and a[t] > 0) else 0.0

        lr = sum(fwd(i) for i in longs) / len(longs)
        sr = sum(fwd(i) for i in shorts) / len(shorts)
        out.append((dates[t], (lr - sr) - fee))
        t += hold
    return out
