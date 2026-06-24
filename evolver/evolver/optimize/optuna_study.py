"""Optuna walk-forward optimization over the whitelisted strategy params.

Search on the train window (deflated Sharpe objective), then validate the best
candidate OUT-OF-SAMPLE against the incumbent before proposing a versioned tweak.
"""
from __future__ import annotations

from evolver.config import RiskLimits
from evolver.optimize.backtest import run_strategy
from evolver.optimize.promotion import deflated_sharpe, promotion_decision

MIN_HISTORY = 60          # need enough signals for a meaningful split
SEARCH = {                # (low, high) — the ONLY tunable surface
    "min_confidence": (0.55, 0.95),
    "max_risk_score": (0.40, 0.90),
    "min_abs_zscore": (0.50, 2.50),
    "kelly_fraction": (0.10, 0.60),
    "target_vol": (0.006, 0.020),
}


def _next_version(active: dict) -> str:
    cur = str(active.get("version", "v1")).lstrip("v")
    try:
        return f"v{int(cur) + 1}"
    except ValueError:
        return "v2"


def run_optimization(signals: list[dict], active: dict, limits: RiskLimits,
                     n_trials: int = 80) -> dict:
    if len(signals) < MIN_HISTORY:
        return {"promote": False, "requires_human": True,
                "note": f"insufficient history ({len(signals)}<{MIN_HISTORY}) — skipping optimize",
                "proposals": [], "version": active.get("version", "v1")}

    import optuna  # declared dep; imported lazily so the rest of the pkg loads without it
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    split = int(len(signals) * 0.7)            # walk-forward: past -> future
    train, valid = signals[:split], signals[split:]

    def objective(trial):
        params = {**active, **{k: trial.suggest_float(k, *rng) for k, rng in SEARCH.items()}}
        return deflated_sharpe(run_strategy(train, params, limits)["returns"], n_trials)

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    candidate = {**active, **study.best_params}

    base = run_strategy(valid, active, limits)         # incumbent, OOS
    chal = run_strategy(valid, candidate, limits)      # challenger, OOS
    verdict = promotion_decision(base, chal, n_trials, limits)

    proposals = [{"param": k, "from": active.get(k), "to": round(study.best_params[k], 4),
                  "hypothesis": "Optuna OOS-validated", "expected_delta": verdict["oos_delta_sharpe"]}
                 for k in study.best_params if active.get(k) != study.best_params[k]]
    return {**verdict, "proposals": proposals, "version": _next_version(active),
            "candidate": candidate}
