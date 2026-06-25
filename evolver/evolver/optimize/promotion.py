"""Promotion gate — the anti-overfitting / anti-reward-hacking firewall.

A challenger is promoted ONLY if, on OUT-OF-SAMPLE data, it beats the incumbent on
risk-adjusted return, with statistical significance, and stays inside hard limits.
Even then `requires_human=True` — nothing auto-deploys.
"""
from __future__ import annotations

import math
import random
import statistics as _st

from evolver.config import RiskLimits


def sharpe(rets: list[float]) -> float:
    if len(rets) < 2:
        return 0.0
    sd = _st.pstdev(rets)
    return (sum(rets) / len(rets)) / sd if sd > 0 else 0.0


def penalized_sharpe(rets: list[float], n_trials: int) -> float:
    """Selection score: Sharpe haircut by the expected MAX Sharpe of `n_trials` zero-edge configs,
    in the Sharpe's own standard-error units. E[max of N standard normals] ≈ √(2·ln N); the Sharpe's
    SE ≈ √((1 + SR²/2)/T) (Lo 2002). So subtract √(2·ln N)·SE(SR) — a statistically-scaled penalty,
    not the old ad-hoc `0.5·ln(N)/√T`.

    NOTE: this is a complexity-penalized Sharpe NUMBER for RANKING grid configs — it is NOT the
    Bailey/López de Prado probability Deflated Sharpe Ratio (that lives in `evolve.fitness.
    deflated_sharpe` and returns P(true Sharpe > 0)). Named honestly so the two are never confused.
    """
    if len(rets) < 3:
        return -1e9
    sr = sharpe(rets)
    se = math.sqrt((1 + 0.5 * sr * sr) / len(rets))
    e_max = math.sqrt(2 * math.log(max(n_trials, 2)))
    return sr - e_max * se


def bootstrap_pvalue(chal: list[float], base: list[float], iters: int = 2000, seed: int = 7) -> float:
    """P(challenger NOT better than incumbent) via a PAIRED stationary-block bootstrap of the Sharpe
    difference. Paired (the SAME resampled index set is applied to both legs, since they trade the
    same OOS window) and block (geometric-length blocks preserve autocorrelation). The old version
    was neither — independent IID draws — which broke the pairing AND ignored serial dependence.
    """
    n = min(len(chal), len(base))
    if n < 4:
        return 1.0
    chal, base = chal[:n], base[:n]
    p_geom = 1.0 / max(2.0, n ** (1.0 / 3.0))
    rng = random.Random(seed)
    not_better = 0
    for _ in range(iters):
        idx = []
        while len(idx) < n:
            k = rng.randrange(n)
            idx.append(k)
            while len(idx) < n and rng.random() > p_geom:
                k = (k + 1) % n
                idx.append(k)
        cs = sharpe([chal[i] for i in idx])
        bs = sharpe([base[i] for i in idx])      # SAME indices -> genuinely paired
        if cs - bs <= 0:
            not_better += 1
    return not_better / iters


def promotion_decision(base: dict, chal: dict, n_trials: int, limits: RiskLimits) -> dict:
    """`base`/`chal` are run_strategy() outputs evaluated on the SAME OOS window."""
    bk, ck = base["kpis"], chal["kpis"]
    base_sr = sharpe(base["returns"])
    chal_sr = sharpe(chal["returns"])
    oos_better = chal_sr > base_sr and ck.get("max_dd", -1) >= bk.get("max_dd", -1)  # not worse DD
    pval = bootstrap_pvalue(chal["returns"], base["returns"])
    risk_ok = (ck.get("max_dd", -1) > -limits.max_dd_kill) and (not chal.get("halt", False))
    promote = bool(oos_better and pval < 0.05 and risk_ok)
    return {
        "promote": promote,
        "requires_human": True,                # ALWAYS — even winners need /approve
        "oos_delta_sharpe": round(chal_sr - base_sr, 3),
        "pvalue": round(pval, 3),
        "risk_ok": risk_ok,
        "base_sharpe": round(base_sr, 3),
        "chal_sharpe": round(chal_sr, 3),
    }
