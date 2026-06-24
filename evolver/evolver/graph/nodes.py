"""LangGraph nodes — thin wrappers over the verified pure-stdlib core."""
from __future__ import annotations

from evolver.core.signal import Signal
from evolver.core.risk import PaperResult
from evolver.agents.analyst import decide, llm_decide, build_fast_llm
from evolver.agents.critic import reflect
from evolver.optimize.optuna_study import run_optimization
from evolver.graph import runtime as rt


def ingest(state: dict) -> dict:
    sig = Signal.from_dict(state["signal"])
    rt.record_signal(state["signal"])
    # similar = vector recall (memory.vectorstore.recall(sig)) — stubbed here
    return {"regime": sig.regime, "similar": [], "messages": [f"ingest {sig.signal_id}"]}


def analyst_node(state: dict) -> dict:
    sig = Signal.from_dict(state["signal"])
    _rm, risk_params, strategy, _ = rt.load_state()
    llm = build_fast_llm()  # fast model when configured, else deterministic fallback
    if llm is not None:
        ctx = {"regime": sig.regime, "similar": state.get("similar", [])}
        return {"decision": llm_decide(sig, ctx, risk_params, rt.LIMITS, llm, strategy)}
    return {"decision": decide(sig, risk_params, rt.LIMITS, strategy)}


def paper_trade_node(state: dict) -> dict:
    sig = Signal.from_dict(state["signal"])
    fill = rt.SIM.execute(sig, state["decision"])
    rt.record_fill(fill, sig)
    return {"fill": fill.__dict__}


def evaluate_node(state: dict) -> dict:
    sig = Signal.from_dict(state["signal"])
    f = state["fill"]
    rm, _risk_params, strategy, _ = rt.load_state()
    new_risk_params = rm.update_from_paper_trade(
        PaperResult(sig.signal_id, f["pnl_pct"], f["hold_hours"],
                    f["realized_vol"], f["max_dd_during"], f["converged"]),
        regime=sig.regime, risk_score=sig.risk_score,
    )
    rt.save_state(rm, new_risk_params, strategy)
    if rm.halt:
        from evolver.safety import trip_circuit_breaker
        trip_circuit_breaker(f"max_dd_kill hit (drawdown={rm.drawdown:.1%})")
    return {"kpis": rt.current_kpis(), "risk_params": new_risk_params}


def critic_node(state: dict) -> dict:
    """Outer loop: optimize + (if a winner) register a human-gated proposal."""
    _rm, _risk_params, strategy, _ = rt.load_state()
    proposal = run_optimization(rt.read_signals(), strategy, rt.LIMITS)
    proposal["reflection"] = reflect(state["kpis"], [], strategy).get("reflection", "")
    if proposal.get("promote"):
        tid = state.get("thread_id", "default")
        rt.register_pending(tid, proposal)
        try:
            from evolver.telegram.notify import notify_admin_proposal
            notify_admin_proposal(tid, proposal)
        except Exception:
            pass  # bot optional in MVP
    return {"proposal": proposal}


def deploy_node(state: dict) -> dict:
    """Reached only AFTER the human gate (interrupt_before=['deploy']) is resumed."""
    tid = state.get("thread_id", "default")
    rt.apply_pending(tid, approved=True)
    return {"approved": True}


# ---- routing ----
def route_after_eval(state: dict) -> str:
    n = state["kpis"].get("trades", 0)
    _rm, _rp, strategy, _ = rt.load_state()
    every = strategy["outer_loop_every"]
    return "optimize" if (n and n % every == 0 and not rt.is_halted()) else "done"


def route_after_critic(state: dict) -> str:
    p = state.get("proposal")
    return "deploy" if (p and p.get("promote")) else "done"
