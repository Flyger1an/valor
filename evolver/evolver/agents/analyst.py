"""Analyst agent — turns a signal into a paper TradeDecision.

Two modes:
  * decide()      — deterministic policy fallback (no LLM). Used by the smoke test
                    and as a safe default when the model is unavailable/timed out.
  * llm_decide()  — fast-model (gpt-5-mini / Haiku / Grok) JSON decision, gated by
                    the SAME deterministic risk rules so the LLM can never exceed limits.

Learning-loop closures (2026-07):
  * decide_with_meta() wraps both modes and returns (decision, meta) where meta carries true
    provenance — source llm|deterministic (a silent LLM fallback is now VISIBLE), model, prompt
    variant + sha8, decision latency, calibration version — for the decision-attribution ledger.
  * The system prompt is a versioned artifact (agents/prompts.py); which variant runs is chosen
    by strategy["analyst_prompt_variant"] (human-gated deploys only).
  * Measured calibration (core/calibration.py, written by the forward shadow book) scales SIZING
    down when stated confidence historically over-promised. It never touches the gate thresholds
    (those stay in stated-confidence units; recalibrating them is the critic+human's job) and can
    never scale anything UP.
"""
from __future__ import annotations

import json
import math
import time

from evolver.core.signal import Signal
from evolver.core.calibration import CALIBRATED_TYPES, load_calibration, conv_scale
from evolver.config import RiskLimits, DEFAULT_STRATEGY
from evolver.agents.prompts import ANALYST_V1, get_analyst_prompt

ANALYST_SYS = ANALYST_V1   # back-compat alias (the v1 incumbent lives in agents/prompts.py)


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


def _num(v, lo: float, hi: float, default: float) -> float:
    """NaN/inf/type-safe numeric clamp — model output is untrusted (json.loads accepts NaN, and
    min(NaN, cap) is NaN; a negative leverage would sign-invert P&L)."""
    try:
        x = float(v)
    except Exception:
        return default
    if not math.isfinite(x):
        return default
    return max(lo, min(hi, x))


def _apply_calibration(decision: dict, calib: dict | None, sig_type: str) -> dict:
    """Humility scaling: shrink size by the measured convergence ratio. Sizing only —
    direction/action/gates untouched; scale is clamped ≤ 1.0 upstream. Scope-honest: applied
    only to the signal types the shadow book measures (CALIBRATED_TYPES)."""
    if not calib or sig_type not in CALIBRATED_TYPES or decision.get("action") == "neutral":
        return decision
    scale = conv_scale(calib)
    decision["size_usd"] = round(float(decision.get("size_usd", 0.0)) * scale, 2)
    decision["confidence_calibrated"] = round(
        float(decision.get("confidence", 0.0)) * scale, 4)
    decision["calib_version"] = calib.get("version", "")
    return decision


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
    out = {
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
    return _apply_calibration(out, load_calibration(), sig.type)


def _llm_raw_decide(sig: Signal, ctx: dict, risk_params: dict, limits: RiskLimits, llm,
                    strat: dict, calib: dict | None) -> dict | None:
    """The bare LLM call. Returns the clamped decision, or None so the caller can fall back
    VISIBLY (the old path swallowed failures and made fallbacks indistinguishable)."""
    text, _variant, _sha = get_analyst_prompt(strat.get("analyst_prompt_variant"))
    try:
        sys = text.format(max_leverage=limits.max_leverage,
                          max_position_pct=limits.max_position_pct, **strat)
    except Exception:
        sys = text   # challenger prompt with odd braces: use raw text, harness still clamps
    # NOTE (review finding): calibration stats are deliberately NOT injected into the model's
    # context. In-context feedback measured from the model's own decisions creates a one-way
    # behavioral ratchet (conservative behavior -> worse measured stats -> more conservative
    # prompt -> ...). Behavior changes go through the challenger-prompt A/B gate instead;
    # calibration acts mechanically on sizing and the sim only.
    user = json.dumps({"signal": sig.__dict__, "context": ctx, "risk_params": risk_params})
    try:
        raw = llm.invoke([("system", sys), ("user", user)]).content
        out = json.loads(_strip_fences(raw))
        if _gate(sig, strat) or not isinstance(out, dict) \
                or out.get("action") not in {"long", "short", "neutral"}:
            return None
        # Clamp whatever the model said to hard limits — NaN/inf/negative/typed-garbage safe
        # (untrusted output; challenger prompts make loose JSON contracts a supported input).
        out["leverage"] = _num(out.get("leverage", 1.0), 0.1, limits.max_leverage, 1.0)
        out["size_usd"] = _num(out.get("size_usd", 0.0), 0.0,
                               limits.capital * limits.max_position_pct, 0.0)
        out["confidence"] = _num(out.get("confidence", 0.0), 0.0, 1.0, 0.0)
        out.setdefault("direction", "long_spread" if sig.zscore < 0 else "short_spread")
    except Exception:
        return None   # ANY weirdness -> visible deterministic fallback, never a crashed tick
    return _apply_calibration(out, calib, sig.type)


def llm_decide(sig: Signal, ctx: dict, risk_params: dict, limits: RiskLimits, llm, strat: dict = None) -> dict:
    """Fast-model decision, then clamped by the deterministic gate/limits (defense-in-depth).
    Back-compat wrapper: falls back to the deterministic policy on any failure."""
    strat = strat or DEFAULT_STRATEGY
    out = _llm_raw_decide(sig, ctx, risk_params, limits, llm, strat, load_calibration())
    return out if out is not None else decide(sig, risk_params, limits, strat)


def decide_with_meta(sig: Signal, ctx: dict, risk_params: dict, limits: RiskLimits, llm,
                     strat: dict = None) -> tuple[dict, dict]:
    """(decision, meta) — the attribution-aware entry point. meta records the TRUE source
    (a silent LLM fallback is labelled deterministic), model, prompt variant+sha, latency,
    and the calibration version applied."""
    import os
    strat = strat or DEFAULT_STRATEGY
    calib = load_calibration()
    _text, variant, sha = get_analyst_prompt(strat.get("analyst_prompt_variant"))
    t0 = time.time()
    out = None
    if llm is not None:
        out = _llm_raw_decide(sig, ctx, risk_params, limits, llm, strat, calib)
    source = "llm" if out is not None else "deterministic"
    if out is None:
        out = decide(sig, risk_params, limits, strat)
    meta = {
        "source": source,
        "model": os.getenv("FAST_MODEL", "gpt-5-mini") if source == "llm" else "deterministic",
        "prompt_variant": variant if source == "llm" else None,
        "prompt_sha": sha if source == "llm" else None,
        "latency_ms": int((time.time() - t0) * 1000),
        "calib_version": (calib or {}).get("version"),
        # what was actually APPLIED to this decision (type-scoped), not just what exists on disk
        "conv_scale": conv_scale(calib) if sig.type in CALIBRATED_TYPES else 1.0,
    }
    return out, meta


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
