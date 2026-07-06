"""Critic / Optimizer agent — reflection + self-critique (outer loop, strong model).

Proposes SAFE tweaks from the whitelist only. It cannot touch the reward function
or the hard risk limits (those aren't in DEFAULT_STRATEGY) — structural guard
against reward hacking.
"""
from __future__ import annotations

import json
from evolver.config import DEFAULT_STRATEGY

CRITIC_SYS = """You are Valor's Optimizer/Critic (outer-loop, strong model).
Given recent KPIs and per-trade attribution, REFLECT then propose tweaks.
You may ONLY change these whitelisted params: {whitelist}.
You may NOT: change the reward/KPI definitions, raise any hard risk limit, or emit code.
For each proposal predict the expected KPI delta and the worst-case drawdown.
Return ONLY JSON: {{"reflection": str, "proposals": [{{"param","from","to","hypothesis","expected_delta"}}]}}."""

WHITELIST = ["min_confidence", "max_risk_score", "min_abs_zscore",
             "kelly_fraction", "target_vol", "outer_loop_every"]


def reflect(kpis: dict, recent_fills: list, strategy: dict, llm=None) -> dict:
    """LLM reflection when a model is provided; otherwise a deterministic heuristic."""
    if llm is not None:
        sys = CRITIC_SYS.format(whitelist=WHITELIST)
        user = json.dumps({"kpis": kpis, "strategy": strategy,
                           "recent": [getattr(f, "__dict__", f) for f in recent_fills[-20:]]})
        try:
            out = json.loads(llm.invoke([("system", sys), ("user", user)]).content)
            # valid JSON that isn't the contract (a bare string/list) must not crash the caller
            if isinstance(out, dict) and isinstance(out.get("proposals", []), list):
                return {"reflection": str(out.get("reflection", "")),
                        "proposals": out.get("proposals", [])}
        except Exception:
            pass  # fall through to heuristic

    # Deterministic fallback: simple, conservative, explainable nudges.
    proposals = []
    wr = kpis.get("win_rate", 0.5)
    conv = kpis.get("rv_convergence_accuracy", 0.5)
    if wr < 0.45 and strategy["min_confidence"] < 0.9:
        proposals.append({"param": "min_confidence", "from": strategy["min_confidence"],
                          "to": round(min(0.9, strategy["min_confidence"] + 0.05), 2),
                          "hypothesis": "low win-rate -> demand higher-confidence signals",
                          "expected_delta": "+win_rate, -trade_count"})
    if conv < 0.5 and strategy["min_abs_zscore"] < 2.0:
        proposals.append({"param": "min_abs_zscore", "from": strategy["min_abs_zscore"],
                          "to": round(strategy["min_abs_zscore"] + 0.2, 2),
                          "hypothesis": "poor convergence -> require stronger dislocation",
                          "expected_delta": "+convergence_accuracy"})
    return {"reflection": f"win_rate={wr}, convergence={conv}; {len(proposals)} nudge(s).",
            "proposals": proposals}
