"""AdaptiveRiskManager — the recalibration node.

Improved over the reference implementation (safety fixes called out inline):
  * leverage is HARD-capped at limits.max_leverage (reference allowed 5.5-6.0x > 5.0 cap)
  * risk/trade NEVER exceeds base_risk_per_trade (reference allowed 1.05% via a 1.4x
    multiplier, violating the stated 0.75% ceiling) — the multiplier only scales DOWN
  * tracks equity + drawdown -> circuit breaker trips `halt` at max_dd_kill
  * regime- and risk_score-aware shrink
  * pure stdlib (no numpy) -> runs anywhere, deterministic
"""
from __future__ import annotations

import statistics as _st
from dataclasses import dataclass

from evolver.config import RiskLimits, DEFAULT_LIMITS


@dataclass
class PaperResult:
    signal_id: str
    pnl_pct: float            # return on capital for the trade (e.g. +0.034)
    hold_hours: float
    realized_vol: float
    max_dd_during: float
    converged: bool = True


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    return _st.pstdev(xs) if len(xs) > 1 else 0.0


_DEFENSIVE_REGIMES = {"high_vol", "momentum_break", "black", "crisis"}


class AdaptiveRiskManager:
    def __init__(self, limits: RiskLimits = DEFAULT_LIMITS):
        self.lim = limits
        self.history: list[PaperResult] = []
        self.current_leverage = 3.0
        self.current_pos_pct = 0.18
        self.performance_multiplier = 1.0
        self.equity = limits.capital
        self.peak_equity = limits.capital
        self.halt = False

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)

    def params(self) -> dict:
        return {
            "new_leverage": round(self.current_leverage, 2),
            "new_pos_pct": round(self.current_pos_pct, 4),
            "performance_multiplier": round(self.performance_multiplier, 2),
            "equity": round(self.equity, 2),
            "drawdown": round(self.drawdown, 4),
            "halt": self.halt,
        }

    def to_state(self) -> dict:
        """Serialize the full mutable state (for the shared store)."""
        return {
            "equity": self.equity,
            "peak_equity": self.peak_equity,
            "current_leverage": self.current_leverage,
            "current_pos_pct": self.current_pos_pct,
            "performance_multiplier": self.performance_multiplier,
            "halt": self.halt,
            "history": [vars(r) for r in self.history],
        }

    @classmethod
    def from_state(cls, state: dict | None, limits: RiskLimits = DEFAULT_LIMITS) -> "AdaptiveRiskManager":
        rm = cls(limits)
        if state:
            rm.equity = float(state.get("equity", limits.capital))
            rm.peak_equity = float(state.get("peak_equity", limits.capital))
            rm.current_leverage = float(state.get("current_leverage", 3.0))
            rm.current_pos_pct = float(state.get("current_pos_pct", 0.18))
            rm.performance_multiplier = float(state.get("performance_multiplier", 1.0))
            rm.halt = bool(state.get("halt", False))
            rm.history = [PaperResult(**h) for h in state.get("history", [])]
        return rm

    def update_from_paper_trade(
        self,
        result: PaperResult,
        regime: str = "",
        risk_score: float = 0.5,
        verbose: bool = False,
    ) -> dict:
        self.history.append(result)
        if len(self.history) > 20:
            self.history.pop(0)

        # --- equity + drawdown circuit breaker (new) ---
        self.equity *= (1.0 + result.pnl_pct)
        self.peak_equity = max(self.peak_equity, self.equity)
        if self.drawdown >= self.lim.max_dd_kill:
            self.halt = True

        # --- rolling performance ---
        recent = [r.pnl_pct for r in self.history[-10:]]
        win_rate = (len([p for p in recent if p > 0]) / len(recent)) if recent else 0.5
        avg_pnl = _mean(recent)
        sharpe_approx = avg_pnl / (_std(recent) + 1e-6) if recent else 0.8

        # multiplier kept in [0.5, 1.4] for transparency, but only ever SCALES RISK DOWN
        self.performance_multiplier = _clip(
            0.6 + win_rate * 0.6 + sharpe_approx * 0.3, 0.5, 1.4
        )

        # regime / signal-risk shrink (new)
        regime_mult = 0.5 if regime in _DEFENSIVE_REGIMES else 1.0
        safety_mult = _clip(1.0 - risk_score * 0.5, 0.5, 1.0)  # higher risk_score -> smaller

        # volatility targeting
        target_vol = 0.012
        vol_scale = target_vol / (result.realized_vol + 1e-5)

        # FIX: risk/trade can only shrink below the base ceiling, never exceed it
        risk_per_trade = (
            self.lim.base_risk_per_trade
            * min(self.performance_multiplier, 1.0)
            * regime_mult
            * safety_mult
        )
        risk_per_trade = min(risk_per_trade, self.lim.base_risk_per_trade)

        new_pos = risk_per_trade / (result.realized_vol * self.current_leverage + 1e-5)
        self.current_pos_pct = _clip(new_pos * vol_scale, 0.05, self.lim.max_position_pct)

        # leverage: defensive on losses, modest add on a strong streak, HARD cap at max_leverage
        if avg_pnl < -0.005:
            self.current_leverage = max(1.5, self.current_leverage * 0.75)
        elif win_rate > 0.7 and sharpe_approx > 1.2:
            self.current_leverage = min(self.lim.max_leverage, self.current_leverage * 1.15)
        self.current_leverage = _clip(self.current_leverage, 1.5, self.lim.max_leverage)

        out = {**self.params(), "risk_per_trade": round(risk_per_trade, 5), "win_rate": round(win_rate, 2)}
        if verbose:
            print(
                f"  recalibrate -> lev {out['new_leverage']}x | pos {out['new_pos_pct']*100:.1f}% | "
                f"risk {out['risk_per_trade']*100:.2f}% | mult {out['performance_multiplier']} | "
                f"wr {out['win_rate']:.0%} | dd {out['drawdown']*100:.1f}% | halt={out['halt']}"
            )
        return out
