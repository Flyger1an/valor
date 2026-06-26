"""Regime/family budget allocation — a self-limiting bandit over WHICH family to search next.

Instead of round-robin, weight the next family by its recent PROMISE (a decayed average of the best
holdout Sharpe it produced) CONDITIONED on the current volatility regime, with epsilon exploration so
nothing is permanently starved. The system learns where to spend search effort — and which families
work in which regime.

HONESTY (load-bearing): concentrating budget on a "promising" family canNOT manufacture an edge — the
extra attempts it gets are still gated by CONFIRM (non-overlapping data), the DSR multiplicity, and the
forward-feedback decay. On noise a family's holdout Sharpe is ~0, so its promise stays ~0 and it gets
no extra budget; only a family producing REAL signal earns more search. The bandit just finds a real
edge faster (if one exists), it never lowers the bar.
"""
from __future__ import annotations

import math
import random


def universe_vol(data):
    """Median recent realized vol across the universe (handles OHLC tuples or bare closes)."""
    vols = []
    for series in list(data.values())[:8]:
        ts = sorted(series)[-60:]
        cl = [(series[t][3] if isinstance(series[t], (tuple, list)) else series[t]) for t in ts]
        rr = [cl[i] / cl[i - 1] - 1 for i in range(1, len(cl)) if cl[i - 1]]
        if len(rr) > 5:
            mu = sum(rr) / len(rr)
            vols.append((sum((x - mu) ** 2 for x in rr) / len(rr)) ** 0.5)
    return sorted(vols)[len(vols) // 2] if vols else 0.0


def regime(uvol, prev_ref, alpha=0.1):
    """('hi'|'lo'|'mid', new_ref) — vol bucket self-calibrated vs a running mean (no fixed per-asset
    thresholds; works for hourly crypto, daily, or FX alike)."""
    if not prev_ref:
        return "mid", uvol
    new_ref = round((1 - alpha) * prev_ref + alpha * uvol, 8)
    return ("hi" if uvol > new_ref else "lo"), new_ref


def pick(n, state, current_regime, names, rng, eps=0.30, temp=2.0):
    """Index of the next family: epsilon-greedy, exploiting softmax(promise[name|regime])."""
    promise = state.get("promise", {})
    if rng.random() < eps or not promise:
        return rng.randrange(n)                          # explore: uniform
    w = [math.exp(temp * promise.get(f"{names[i]}|{current_regime}", 0.0)) for i in range(n)]
    tot = sum(w) or 1.0
    x, acc = rng.random() * tot, 0.0
    for i, wi in enumerate(w):
        acc += wi
        if x <= acc:
            return i
    return n - 1


def update(state, family, current_regime, osr, decay=0.8):
    """EMA the family's promise in this regime with the cycle's best holdout Sharpe (floored at 0)."""
    p = state.setdefault("promise", {})
    k = f"{family}|{current_regime}"
    p[k] = round(decay * p.get(k, 0.0) + (1 - decay) * max(osr, 0.0), 4)
