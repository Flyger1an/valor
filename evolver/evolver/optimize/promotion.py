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


def deflated_sharpe(rets: list[float], n_trials: int) -> float:
    """Haircut the Sharpe for multiple-testing/overfitting (more trials => bigger penalty)."""
    if len(rets) < 2:
        return -1e9
    return sharpe(rets) - 0.5 * math.log(max(n_trials, 1)) / math.sqrt(len(rets))


def bootstrap_pvalue(chal: list[float], base: list[float], iters: int = 2000, seed: int = 7) -> float:
    """P(challenger NOT better than incumbent) via paired bootstrap of Sharpe diff."""
    if len(chal) < 2 or len(base) < 2:
        return 1.0
    rng = random.Random(seed)
    not_better = 0
    for _ in range(iters):
        cs = sharpe([rng.choice(chal) for _ in chal])
        bs = sharpe([rng.choice(base) for _ in base])
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
