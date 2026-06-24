"""Analyst agent — turns a signal into a paper TradeDecision.

Two modes:
  * decide()      — deterministic policy fallback (no LLM). Used by the smoke test
                    and as a safe default when the model is unavailable/timed out.
  * llm_decide()  — fast-model (gpt-5-mini / Haiku / Grok) JSON decision, gated by
                    the SAME deterministic risk rules so the LLM can never exceed limits.
"""
from __future__ import annotations

import json
from evolver.core.signal import Signal
from evolver.config import RiskLimits, DEFAULT_STRATEGY

ANALYST_SYS = """You are Valor's RV Paper-Trade Analyst (fast inner-loop model).
Inputs: one relative-value signal, current REGIME, PORTFOLIO state, and SIMILAR past
signals with realized outcomes.
Hard rules (the harness also enforces these — do not fight them):
- A signal is a mean-reversion/convergence idea. zscore<0 => expect spread up (LONG spread);
  zscore>0 => expect spread down (SHORT spread).
- risk_score is 0..1 (higher = riskier). If risk_score > {max_risk_score} OR
  confidence < {min_confidence} OR |zscore| < {min_abs_zscore} OR regime is defensive,
  action MUST be "neutral".
- Never propose leverage above {max_leverage} or size above {max_position_pct:.0%} of capital.
Output ONLY valid JSON, EXACTLY these fields and allowed values (no prose, no markdown):
  "action": one of "long" | "short" | "neutral"   (NEVER "enter"/"exit"/"buy"/"sell")
  "direction": one of "long_spread" | "short_spread" | "neutral"
  "size_usd": number, "leverage": number,
  "entry": object, "exit": object, "confidence": number, "rationale": string"""


def _gate(sig: Signal, strat: dict) -> str | None:
    if sig.confidence < strat["min_confidence"]:
        return f"confidence {sig.confidence} < {strat['min_confidence']}"
    if sig.risk_score > strat["max_risk_score"]:
        return f"risk_score {sig.risk_score} > {strat['max_risk_score']}"
    if abs(sig.zscore) < strat["min_abs_zscore"]:
        return f"|zscore| {abs(sig.zscore)} < {strat['min_abs_zscore']}"
    if sig.regime in {"high_vol", "momentum_break", "black", "crisis"}:
        return f"defensive regime {sig.regime}"
    return None


def _neutral(reason: str) -> dict:
    return {"action": "neutral", "direction": "neutral", "size_usd": 0.0,
            "leverage": 1.0, "entry": {}, "exit": {}, "confidence": 0.0,
            "rationale": f"gated: {reason}"}


def decide(sig: Signal, risk_params: dict, limits: RiskLimits, strat: dict = None) -> dict:
    """Deterministic policy — safe, explainable, no LLM required."""
    strat = strat or DEFAULT_STRATEGY
    gate = _gate(sig, strat)
    if gate:
        return _neutral(gate)

    direction = "long_spread" if sig.zscore < 0 else "short_spread"
    action = "long" if sig.zscore < 0 else "short"
    size = limits.capital * risk_params["new_pos_pct"]
    size = min(size, limits.capital * limits.max_position_pct)
    leverage = min(risk_params["new_leverage"], limits.max_leverage)
    return {
        "action": action,
        "direction": direction,
        "size_usd": round(size, 2),
        "leverage": round(leverage, 2),
        "entry": {"zscore": sig.zscore, "spread_value": sig.spread_value},
        "exit": {"target_zscore": 0.0, "max_hold_hours": round(sig.expected_convergence_hours * 1.5, 1)},
        "confidence": sig.confidence,
        "rationale": f"{sig.type}: zscore {sig.zscore} -> revert to mean within "
                     f"~{sig.expected_convergence_hours}h (conf {sig.confidence}).",
    }


def llm_decide(sig: Signal, ctx: dict, risk_params: dict, limits: RiskLimits, llm, strat: dict = None) -> dict:
    """Fast-model decision, then clamped by the deterministic gate/limits (defense-in-depth)."""
    strat = strat or DEFAULT_STRATEGY
    sys = ANALYST_SYS.format(max_leverage=limits.max_leverage,
                             max_position_pct=limits.max_position_pct, **strat)
    user = json.dumps({"signal": sig.__dict__, "context": ctx, "risk_params": risk_params})
    try:
        raw = llm.invoke([("system", sys), ("user", user)]).content
        out = json.loads(_strip_fences(raw))
    except Exception:
        return decide(sig, risk_params, limits, strat)  # fail safe to deterministic
    # Clamp whatever the model said to hard limits and re-apply the gate.
    if _gate(sig, strat) or out.get("action") not in {"long", "short", "neutral"}:
        return decide(sig, risk_params, limits, strat)
    out["leverage"] = min(float(out.get("leverage", 1.0)), limits.max_leverage)
    out["size_usd"] = min(float(out.get("size_usd", 0.0)), limits.capital * limits.max_position_pct)
    out.setdefault("direction", "long_spread" if sig.zscore < 0 else "short_spread")
    return out


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw[raw.find("\n") + 1:]
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
    return raw.strip()


_FAST_LLM = None


def build_fast_llm():
    """Fast inner-loop model (lazy, cached). temperature=1 is the only value gpt-5.x
    accepts (harmless for others); response_format=json keeps output parseable.
    Returns None when OPENAI_API_KEY is unset or langchain is unavailable, so callers
    fall back to the deterministic policy — the loop never hard-depends on the LLM."""
    global _FAST_LLM
    if _FAST_LLM is not None:
        return _FAST_LLM
    import os

    if not os.getenv("OPENAI_API_KEY"):
        return None
    try:
        from langchain_openai import ChatOpenAI

        _FAST_LLM = ChatOpenAI(
            model=os.getenv("FAST_MODEL", "gpt-5-mini"),
            temperature=1,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
        return _FAST_LLM
    except Exception:
        return None
