"""Strategy backtest used by the optimizer's A/B — reuses the SAME core sim as the
inner loop, so optimization and live paper trading can never silently diverge.

For speed at scale, swap `run_strategy` for a vectorbt implementation; keep this as
the fidelity reference.
"""
from __future__ import annotations

from evolver.config import RiskLimits
from evolver.core.signal import Signal
from evolver.core.risk import AdaptiveRiskManager, PaperResult
from evolver.core.sim import PerpPaperSim
from evolver.core.kpis import compute_kpis
from evolver.agents.analyst import decide


def run_strategy(signals: list[dict], strategy: dict, limits: RiskLimits) -> dict:
    rm = AdaptiveRiskManager(limits)
    sim = PerpPaperSim(limits.capital)
    rp = rm.params() | {"risk_per_trade": limits.base_risk_per_trade}
    fills = []
    for d in signals:
        sig = Signal.from_dict(d)
        fill = sim.execute(sig, decide(sig, rp, limits, strategy))
        fills.append(fill)
        rp = rm.update_from_paper_trade(
            PaperResult(sig.signal_id, fill.pnl_pct, fill.hold_hours,
                        fill.realized_vol, fill.max_dd_during, fill.converged),
            regime=sig.regime, risk_score=sig.risk_score,
        )
    rets = [f.pnl_pct for f in fills if f.direction != "neutral"]
    return {"kpis": compute_kpis(fills, limits.capital), "returns": rets,
            "equity": rm.equity, "halt": rm.halt}
