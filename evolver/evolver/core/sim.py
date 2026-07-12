"""PerpPaperSim — first-pass event-driven convergence simulator.

Models direction, convergence capture, fees, slippage, and funding with the right
*shape* for the closed loop. Deterministic per signal_id (seeded) so cycles are
reproducible.

HONEST CAVEAT: this is a heuristic execution model for the loop's mechanics, NOT a
substitute for live fidelity. The shipped fidelity upgrade is measured shadow
calibration (core/calibration.py): conv_scale is derived from the shadow book's
measured fills, and each Fill is stamped with the calib_version applied.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass

from evolver.core.calibration import CALIBRATED_TYPES, load_calibration, conv_scale
from evolver.core.signal import Signal

# OKX-calibrated execution costs (overridable via env). OKX perp taker ≈ 0.05% = 5 bps;
# spot taker ≈ 0.10%. Default to perp taker; tune per venue/instrument.
TAKER_FEE_BPS = float(os.getenv("OKX_TAKER_FEE_BPS", "5.0"))   # per leg
SLIP_BASE_BPS = float(os.getenv("SLIP_BASE_BPS", "2.0"))
_MAX_TARGET_RETURN = 0.05  # cap gross convergence capture per trade


@dataclass
class Fill:
    signal_id: str
    direction: str           # long_spread | short_spread | neutral
    notional_usd: float
    gross_pnl_usd: float
    cost_usd: float          # fees + slippage
    funding_usd: float
    net_pnl_usd: float
    pnl_pct: float           # on capital
    hold_hours: float
    realized_vol: float
    converged: bool
    max_dd_during: float
    calib_version: str = ""   # measured-reality calibration applied ("" = uncalibrated priors)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _vol(sig: Signal) -> float:
    v = sig.metadata.get("vol_7d")
    if v is not None:
        return float(v)
    return {
        "low_vol": 0.012, "low_vol_mean_revert": 0.015, "contango": 0.018,
        "high_vol": 0.045, "momentum_break": 0.035, "momentum": 0.030,
    }.get(sig.regime, 0.025)


def _gross_target_return(sig: Signal) -> float:
    """Gross capturable return on notional if the trade fully converges (type-aware)."""
    m = sig.metadata
    hrs = sig.expected_convergence_hours
    if sig.type == "funding_arb":
        ann = float(m.get("ann_funding", 0.0)) / 100.0
        return min(abs(sig.spread_value) + ann * (hrs / 8760.0), _MAX_TARGET_RETURN)
    if sig.type == "basis_trade":
        ann = float(m.get("basis_annualized", 0.0)) / 100.0
        return min(abs(sig.spread_value) + abs(ann) * (hrs / 8760.0), _MAX_TARGET_RETURN)
    if sig.type == "triangular":
        return min(float(m.get("deviation_bps", 0.0)) / 1e4, _MAX_TARGET_RETURN)
    # cointegration_spread / stat_arb_pair: capture a fraction of the spread on reversion
    return min(abs(sig.spread_value) * 0.5, _MAX_TARGET_RETURN)


def predicted_p_converge(sig: Signal) -> float:
    """The sim's UNCALIBRATED convergence prior. Exported so the shadow book can record the
    prediction it is measuring against — the honest denominator for calibration (scaling by
    realized ÷ stated-confidence over-shrinks, because entered trades' stated confidence sits
    above this prior)."""
    return _clip(0.5 + (sig.confidence - 0.5) * 0.9 - sig.risk_score * 0.3, 0.05, 0.95)


def _slippage_bps(sig: Signal) -> float:
    impact = float(sig.metadata.get("liq_depth_impact", 0.0)) * 1e4  # 0.003 -> 30 bps
    return SLIP_BASE_BPS + impact


def _funding_usd(sig: Signal, notional: float) -> float:
    """Funding drag over the hold (small; the *edge* funding is already in the target)."""
    m = sig.metadata
    hrs = sig.expected_convergence_hours
    ann = float(m.get("ann_funding", 0.0)) / 100.0
    if ann:
        return notional * ann * (hrs / 8760.0) * 0.5   # ~half leaks if imperfectly hedged
    fd = abs(float(m.get("funding_diff", 0.0)))         # per-8h fraction
    return notional * fd * (hrs / 8.0) * 0.25


class PerpPaperSim:
    def __init__(self, capital: float):
        self.capital = capital

    def execute(self, sig: Signal, decision: dict) -> Fill:
        if decision.get("action") == "neutral" or decision.get("size_usd", 0) <= 0:
            return Fill(
                signal_id=sig.signal_id, direction="neutral", notional_usd=0.0,
                gross_pnl_usd=0.0, cost_usd=0.0, funding_usd=0.0, net_pnl_usd=0.0,
                pnl_pct=0.0, hold_hours=0.0, realized_vol=_vol(sig),
                converged=False, max_dd_during=0.0,
            )

        rng = random.Random(f"{sig.signal_id}:{sig.type}")  # reproducible per signal
        notional = float(decision["size_usd"]) * float(decision.get("leverage", 1.0))

        target = _gross_target_return(sig)
        # P(convergence) rises with confidence, falls with risk_score — then scaled by MEASURED
        # reality (core/calibration.py, from the forward shadow book). Scope-honest: the scale is
        # applied only to the signal types the shadow book actually measures (CALIBRATED_TYPES);
        # other types keep raw priors until a book measures them.
        calib = load_calibration() if sig.type in CALIBRATED_TYPES else None
        p_converge = _clip(predicted_p_converge(sig) * conv_scale(calib), 0.05, 0.95)
        converged = rng.random() < p_converge
        capture = rng.uniform(0.6, 1.1) if converged else rng.uniform(-0.7, -0.1)
        gross_ret = target * capture

        gross = notional * gross_ret
        cost = notional * ((2 * TAKER_FEE_BPS + _slippage_bps(sig)) / 1e4)
        funding = _funding_usd(sig, notional)
        net = gross - cost - funding
        pnl_pct = net / self.capital
        hold = sig.expected_convergence_hours * (0.8 if converged else 1.5)
        mdd = abs(min(0.0, gross_ret))  # adverse excursion proxy

        return Fill(
            signal_id=sig.signal_id,
            direction=decision.get("direction", "neutral"),
            notional_usd=round(notional, 2),
            gross_pnl_usd=round(gross, 2),
            cost_usd=round(cost, 2),
            funding_usd=round(funding, 2),
            net_pnl_usd=round(net, 2),
            pnl_pct=pnl_pct,
            hold_hours=round(hold, 2),
            realized_vol=_vol(sig),
            converged=converged,
            max_dd_during=round(mdd, 4),
            calib_version=(calib or {}).get("version", ""),
        )
