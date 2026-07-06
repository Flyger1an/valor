"""Dependency-light inner-loop orchestrator (core only — no langgraph required).

The FastAPI ingest path and the bus consumer call run_inner(); the LangGraph build
in graph/build.py is the richer equivalent (same core, adds checkpoints + the
human-gated outer loop). Both share graph.runtime state.
"""
from __future__ import annotations

from evolver.core.signal import Signal
from evolver.core.risk import PaperResult
from evolver.agents.analyst import decide_with_meta, build_fast_llm
from evolver.graph import runtime as rt
from evolver.obs.decisions import record_decision
from evolver.obs.mlflow_log import log_cycle
from evolver.safety import kill_switch, trip_circuit_breaker


def run_inner(signal_dict: dict) -> dict:
    if kill_switch.active():
        return {"skipped": "kill_switch_active", "kpis": rt.current_kpis()}

    sig = Signal.from_dict(signal_dict)          # validates the locked contract
    rt.record_signal(signal_dict)
    rm, risk_params, strategy, _ = rt.load_state()   # shared book across all processes

    llm = build_fast_llm()                        # fast model when configured, else None
    decision, meta = decide_with_meta(
        sig, {"regime": sig.regime}, risk_params, rt.LIMITS, llm, strategy)
    record_decision("inner", signal_dict, decision, meta)   # attribution: full decision + provenance
    fill = rt.SIM.execute(sig, decision)
    rt.record_fill(fill, sig)
    new_risk_params = rm.update_from_paper_trade(
        PaperResult(sig.signal_id, fill.pnl_pct, fill.hold_hours,
                    fill.realized_vol, fill.max_dd_during, fill.converged),
        regime=sig.regime, risk_score=sig.risk_score,
    )
    rt.save_state(rm, new_risk_params, strategy)
    if rm.halt:
        trip_circuit_breaker(f"max_dd_kill hit (drawdown={rm.drawdown:.1%})")

    kpis = rt.current_kpis()
    log_cycle(
        signal_dict, decision, fill.__dict__, kpis,
        strategy_version=strategy.get("version", "v1"),
        # HONEST label: meta knows whether the LLM actually answered — the old llm-configured
        # check logged the model name even when llm_decide silently fell back to deterministic.
        model=meta["model"],
    )
    return {"signal_id": sig.signal_id, "decision": decision, "fill": fill.__dict__, "kpis": kpis}
