"""Forward-feedback learning — the one kind of learning here that does NOT overfit.

The shadows generate REAL out-of-sample forward track records of promoted candidates. This module
learns the per-family backtest->forward Sharpe DECAY from them — the haircut the gate's predictions
actually suffer in reality — and feeds it back as an extra gate term: a family whose promoted edges
decayed forward must clear a proportionally higher bar. Because it learns from data the system NEVER
trained on (the forward slice), it sharpens the gate WITHOUT mining the backtest harder (which is how
you overfit). Empirical-Bayes shrinkage toward a conservative prior; a no-op for families without
enough forward evidence — the gate is unchanged until reality has spoken.

Inputs:
  approved : the queue's approved list — each candidate has {id, family, oos_sharpe} (backtest, at promotion)
  snapshot : a shadow's per-candidate forward slice — each has {id, fwd_sharpe, fwd_n}
"""
from __future__ import annotations


def family_decays(approved, snapshot, prior_decay=0.7, prior_w=2.0, min_n=2, min_fwd_n=10):
    """{family: decay_factor} in ~(0, 1.2] for families with >= min_n promoted candidates that each
    accrued >= min_fwd_n forward trades. decay = forward_sharpe / backtest_oos_sharpe, clamped and
    shrunk toward `prior_decay`. Families WITHOUT enough forward evidence are absent from the map, so
    the caller defaults them to 1.0 (no haircut) — the loop changes nothing until it has real data."""
    fwd = {x.get("id"): x for x in (snapshot or [])}
    by_fam: dict[str, list[float]] = {}
    for c in (approved or []):
        s = fwd.get(c.get("id"))
        bt = c.get("oos_sharpe")
        if not s or not bt or bt <= 0 or s.get("fwd_n", 0) < min_fwd_n:
            continue
        d = max(0.0, min(s.get("fwd_sharpe", 0.0) / bt, 1.2))   # clamp: forward >> backtest = luck, floor 0
        by_fam.setdefault(c.get("family", "?"), []).append(d)
    out = {}
    for fam, ds in by_fam.items():
        if len(ds) < min_n:
            continue
        # empirical-Bayes shrinkage toward the conservative prior (don't overreact to 1-2 samples)
        out[fam] = round((sum(ds) + prior_w * prior_decay) / (len(ds) + prior_w), 3)
    return out
