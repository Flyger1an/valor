"""Time-series momentum (trend-following) — per-asset, the most robust documented systematic
effect (Moskowitz-Ooi-Pedersen 2012). NOT cross-sectional: each asset is traded on its OWN
trend. Long if its trailing return is up, short if down (with a deadband), inverse-vol sized
to target equal risk, unit gross. Honest turnover cost (trend persistence => low turnover is
part of the edge). If it works, it works as a PLATEAU across lookbacks — less fragile by nature.

universe: {coin: {day_ms: close}}.
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS

DEFAULT_PARAMS = {"lookback": 60, "holding": 10, "skip": 1, "thr": 0.0, "vol_window": 30, "fee_bps": 5.0}


def run_trend(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    lb, hold, sk = int(p["lookback"]), int(p["holding"]), int(p["skip"])
    thr, vw, fee = p["thr"], int(p["vol_window"]), p["fee_bps"] / 1e4
    coins = sorted(universe)
    dates = sorted(set().union(*[set(universe[c]) for c in coins])) if coins else []
    if len(dates) < lb + vw + hold + 4:
        return []
    close, ret = {}, {}
    for c in coins:
        s, last, a = universe[c], None, []
        for d in dates:
            if d in s:
                last = s[d]
            a.append(last)
        close[c] = a
        ret[c] = [None] + [(a[i] / a[i - 1] - 1) if (a[i] and a[i - 1] and a[i - 1] > 0) else None
                           for i in range(1, len(a))]

    out, n, prev = [], len(dates), {}
    t = lb + sk + vw + 2
    while t + hold < n:
        if lo is not None and dates[t] < lo:
            t += hold
            continue
        if hi is not None and dates[t] >= hi:
            break
        w = {}
        for c in coins:
            a = close[c]
            p0, pl = a[t - sk], a[t - sk - lb]
            if not (p0 and pl and p0 > 0 and pl > 0):
                continue
            trend = p0 / pl - 1
            sig = 1.0 if trend > thr else (-1.0 if trend < -thr else 0.0)
            if sig == 0:
                continue
            rr = [x for x in ret[c][t - vw:t] if x is not None]
            if len(rr) < vw // 2:
                continue
            mn = sum(rr) / len(rr)
            vol = (sum((x - mn) ** 2 for x in rr) / len(rr)) ** 0.5
            if vol > 0:
                w[c] = sig / vol
        if not w:
            t += hold
            continue
        gross = sum(abs(x) for x in w.values())
        w = {c: x / gross for c, x in w.items()}
        port = 0.0
        for c, wi in w.items():
            a = close[c]
            if a[t] and a[t + hold] and a[t] > 0:
                port += wi * (a[t + hold] / a[t] - 1)
        turn = sum(abs(w.get(c, 0.0) - prev.get(c, 0.0)) for c in set(w) | set(prev))
        out.append((dates[t], port - turn * fee))
        prev = w
        t += hold
    return out
