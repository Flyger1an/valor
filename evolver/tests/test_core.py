"""Core invariants — the safety guarantees that matter most.

Run: cd evolver && python3 -m pytest -q   (or python3 tests/test_core.py for a dep-free check)
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # make `evolver` importable

from evolver.config import DEFAULT_LIMITS, DEFAULT_STRATEGY
from evolver.core.signal import Signal
from evolver.core.risk import AdaptiveRiskManager, PaperResult
from evolver.core.sim import PerpPaperSim
from evolver.core.kpis import compute_kpis
from evolver.agents.analyst import decide

SAMPLES = json.loads(
    (pathlib.Path(__file__).resolve().parents[2] / "shared" / "sample_signals.json").read_text()
)


def test_contract_validation():
    s = Signal.from_dict(SAMPLES[0])
    assert s.type == "cointegration_spread" and s.assets == ("ETH", "BTC")
    bad = dict(SAMPLES[0]); bad["risk_score"] = 1.4
    try:
        Signal.from_dict(bad); assert False, "should reject risk_score>1"
    except ValueError:
        pass
    missing = dict(SAMPLES[0]); del missing["zscore"]
    try:
        Signal.from_dict(missing); assert False, "should reject missing field"
    except ValueError:
        pass


def test_leverage_never_exceeds_cap():
    rm = AdaptiveRiskManager(DEFAULT_LIMITS)
    # feed a long winning streak that would push the reference impl past the cap
    for i in range(40):
        rm.update_from_paper_trade(PaperResult(f"w{i}", +0.05, 3.0, 0.01, 0.0, True))
        assert rm.current_leverage <= DEFAULT_LIMITS.max_leverage + 1e-9


def test_risk_per_trade_never_exceeds_base():
    rm = AdaptiveRiskManager(DEFAULT_LIMITS)
    for i in range(40):
        out = rm.update_from_paper_trade(PaperResult(f"w{i}", +0.08, 3.0, 0.008, 0.0, True))
        assert out["risk_per_trade"] <= DEFAULT_LIMITS.base_risk_per_trade + 1e-9


def test_drawdown_circuit_breaker_trips():
    rm = AdaptiveRiskManager(DEFAULT_LIMITS)
    out = rm.update_from_paper_trade(PaperResult("loss", -0.20, 3.0, 0.05, 0.20, False))
    assert out["halt"] is True and rm.drawdown >= DEFAULT_LIMITS.max_dd_kill


def test_sim_is_deterministic():
    sig = Signal.from_dict(SAMPLES[0])
    sim = PerpPaperSim(DEFAULT_LIMITS.capital)
    dec = decide(sig, {"new_leverage": 3.0, "new_pos_pct": 0.18}, DEFAULT_LIMITS)
    a = sim.execute(sig, dec); b = sim.execute(sig, dec)
    assert a.net_pnl_usd == b.net_pnl_usd and a.converged == b.converged


def test_kpis_shape():
    rm = AdaptiveRiskManager(DEFAULT_LIMITS)
    sim = PerpPaperSim(DEFAULT_LIMITS.capital)
    rp = rm.params() | {"risk_per_trade": DEFAULT_LIMITS.base_risk_per_trade}
    fills = []
    for d in SAMPLES:
        sig = Signal.from_dict(d)
        fills.append(sim.execute(sig, decide(sig, rp, DEFAULT_LIMITS)))
        rp = rm.update_from_paper_trade(
            PaperResult(sig.signal_id, fills[-1].pnl_pct, fills[-1].hold_hours,
                        fills[-1].realized_vol, fills[-1].max_dd_during, fills[-1].converged),
            regime=sig.regime, risk_score=sig.risk_score)
    k = compute_kpis(fills, DEFAULT_LIMITS.capital)
    assert k["trades"] >= 1 and "sharpe_per_trade" in k and "rv_convergence_accuracy" in k


if __name__ == "__main__":
    for fn in [test_contract_validation, test_leverage_never_exceeds_cap,
               test_risk_per_trade_never_exceeds_base, test_drawdown_circuit_breaker_trips,
               test_sim_is_deterministic, test_kpis_shape]:
        fn(); print(f"ok  {fn.__name__}")
    print("all core invariants passed")
