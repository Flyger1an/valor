"""First-cycle smoke test — runs the inner loop on shared/sample_signals.json.

Zero external deps (pure stdlib). Run:
    cd evolver && python3 scripts/first_cycle.py
or from repo root:
    python3 evolver/scripts/first_cycle.py
"""
from __future__ import annotations

import json
import pathlib
import sys

# Make the `evolver` package importable regardless of cwd.
ROOT = pathlib.Path(__file__).resolve().parents[1]          # .../Valor/evolver
sys.path.insert(0, str(ROOT))
REPO = ROOT.parent                                          # .../Valor

from evolver.config import DEFAULT_LIMITS, DEFAULT_STRATEGY   # noqa: E402
from evolver.core.signal import Signal                        # noqa: E402
from evolver.core.risk import AdaptiveRiskManager, PaperResult  # noqa: E402
from evolver.core.sim import PerpPaperSim                      # noqa: E402
from evolver.core.kpis import compute_kpis                     # noqa: E402
from evolver.agents.analyst import decide                      # noqa: E402


def run(samples: list[dict]) -> dict:
    lim = DEFAULT_LIMITS
    rm = AdaptiveRiskManager(lim)
    sim = PerpPaperSim(lim.capital)
    risk_params = rm.params() | {"risk_per_trade": lim.base_risk_per_trade}
    fills = []

    print(f"{'signal':<18}{'type':<20}{'act':<8}{'size$':>10}{'lev':>6}{'net$':>10}  conv")
    print("-" * 80)
    for d in samples:
        sig = Signal.from_dict(d)
        decision = decide(sig, risk_params, lim, DEFAULT_STRATEGY)
        fill = sim.execute(sig, decision)
        fills.append(fill)
        risk_params = rm.update_from_paper_trade(
            PaperResult(sig.signal_id, fill.pnl_pct, fill.hold_hours,
                        fill.realized_vol, fill.max_dd_during, fill.converged),
            regime=sig.regime, risk_score=sig.risk_score,
        )
        print(f"{sig.signal_id:<18}{sig.type:<20}{decision['action']:<8}"
              f"{decision['size_usd']:>10,.0f}{decision['leverage']:>6.1f}"
              f"{fill.net_pnl_usd:>10,.2f}  {'Y' if fill.converged else 'n'}"
              f"   | lev'={risk_params['new_leverage']}x pos'={risk_params['new_pos_pct']*100:.1f}%"
              f" dd={risk_params['drawdown']*100:.1f}% halt={risk_params['halt']}")

    kpis = compute_kpis(fills, lim.capital)
    print("\n=== KPIs ===")
    print(json.dumps(kpis, indent=2))
    print(f"\nfinal equity: ${rm.equity:,.2f}  | peak: ${rm.peak_equity:,.2f}  | halt: {rm.halt}")
    return kpis


if __name__ == "__main__":
    samples = json.loads((REPO / "shared" / "sample_signals.json").read_text())
    run(samples)
