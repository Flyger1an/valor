"""MLflow run-logging for the inner loop — one run per signal.

Fail-safe by design: a no-op unless MLFLOW_TRACKING_URI is set, and every error is
swallowed. Observability must NEVER break the trading loop.
LangSmith tracing of the LLM calls is separate and env-only (LANGCHAIN_TRACING_V2 +
LANGCHAIN_API_KEY) — langchain auto-instruments, no code here.
"""
from __future__ import annotations

import os

_READY = None


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def log_cycle(signal: dict, decision: dict, fill: dict, kpis: dict,
              strategy_version: str = "v1", model: str = "") -> None:
    if not os.getenv("MLFLOW_TRACKING_URI"):
        return
    try:
        import mlflow

        global _READY
        if _READY is None:
            mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
            mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT", "valor-evolver"))
            _READY = True

        with mlflow.start_run(run_name=signal.get("signal_id", "signal")):
            mlflow.log_params({
                "signal_id": signal.get("signal_id"),
                "type": signal.get("type"),
                "regime": signal.get("regime"),
                "action": decision.get("action"),
                "direction": decision.get("direction"),
                "model": model or "deterministic",
                "strategy_version": strategy_version,
            })
            mlflow.log_metrics({
                "zscore": _f(signal.get("zscore")),
                "risk_score": _f(signal.get("risk_score")),
                "confidence": _f(signal.get("confidence")),
                "size_usd": _f(decision.get("size_usd")),
                "leverage": _f(decision.get("leverage")),
                "net_pnl_usd": _f(fill.get("net_pnl_usd")),
                "pnl_pct": _f(fill.get("pnl_pct")),
                "converged": 1.0 if fill.get("converged") else 0.0,
                "equity": _f(kpis.get("equity")),
                "drawdown": _f(kpis.get("drawdown")),
                "trades": _f(kpis.get("trades")),
                "win_rate": _f(kpis.get("win_rate")),
                "sharpe_per_trade": _f(kpis.get("sharpe_per_trade")),
            })
    except Exception:
        pass  # never let tracing break the loop
