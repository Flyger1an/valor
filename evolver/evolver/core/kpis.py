"""KPI / Evaluator math — Sharpe, Sortino, Calmar, maxDD, win-rate, profit-factor,
plus RV-specific convergence accuracy. Pure stdlib, trade-level returns.

Honesty rules (hard-won lessons from the Valor platform):
  * Primary risk-adjusted stats are PER-TRADE (mean/std) — bounded and meaningful.
  * Annualized Sharpe/return are EXTRAPOLATED and statistically meaningless on tiny
    samples, so they are only emitted once trades >= MIN_SAMPLE, clearly flagged.
  * Undefined ratios return None (never a magic sentinel like 99 or 1e18).
"""
from __future__ import annotations

import statistics as _st

MIN_SAMPLE = 30  # below this, annualized metrics are omitted as meaningless


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    return _st.pstdev(xs) if len(xs) > 1 else 0.0


def compute_kpis(fills: list, capital: float) -> dict:
    closed = [f for f in fills if getattr(f, "direction", "neutral") != "neutral"]
    rets = [f.pnl_pct for f in closed]
    n = len(rets)
    if n == 0:
        return {"trades": 0, "note": "no executed trades (all neutral / gated)"}

    # equity curve + max drawdown
    eq, e = [], 1.0
    for r in rets:
        e *= (1.0 + r)
        eq.append(e)
    peak, maxdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        maxdd = min(maxdd, v / peak - 1.0)

    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    mean, sd = _mean(rets), _std(rets)
    dsd = _std(losses)  # downside dispersion

    sharpe_pt = (mean / sd) if sd > 0 else None
    sortino_pt = (mean / dsd) if dsd > 0 else None
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else None
    avg_hold = _mean([f.hold_hours for f in closed]) or 1.0
    converged = [f for f in closed if getattr(f, "converged", False)]

    out = {
        "trades": n,
        "total_return": round(eq[-1] - 1.0, 4),
        "mean_trade_return": round(mean, 5),
        "sharpe_per_trade": round(sharpe_pt, 2) if sharpe_pt is not None else None,
        "sortino_per_trade": round(sortino_pt, 2) if sortino_pt is not None else None,
        "max_dd": round(maxdd, 4),
        "win_rate": round(len(wins) / n, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "avg_hold_hours": round(avg_hold, 1),
        # RV-specific
        "rv_convergence_accuracy": round(len(converged) / n, 2),
        "net_pnl_usd": round(sum(f.net_pnl_usd for f in closed), 2),
        "fees_paid_usd": round(sum(f.cost_usd + f.funding_usd for f in closed), 2),
    }

    if n >= MIN_SAMPLE and sharpe_pt is not None:
        tpy = 8760.0 / avg_hold  # trades per year from realized hold time
        out["annualized"] = {
            "note": "extrapolated; assumes iid trades — treat as indicative only",
            "sharpe": round(sharpe_pt * (tpy ** 0.5), 2),
            "return_est": round(mean * tpy, 4),
            "calmar": round((mean * tpy) / abs(maxdd), 2) if maxdd < 0 else None,
        }
    else:
        out["sample_warning"] = (
            f"n={n} < {MIN_SAMPLE}: annualized Sharpe/return/Calmar omitted "
            f"(statistically meaningless on this sample)"
        )
    return out
