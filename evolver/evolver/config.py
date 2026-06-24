"""Risk envelope + strategy config.

DEFAULT_PAPER_LIMITS is inherited from the Valor platform. The AdaptiveRiskManager
may diverge *within* these hard bounds — never beyond them.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

# Inherited verbatim from the Valor platform (the hard envelope).
DEFAULT_PAPER_LIMITS = {
    "capital": 100_000.0,
    "max_leverage": 5.0,
    "max_position_pct": 0.25,     # 25% of capital, hard cap
    "max_dd_kill": 0.15,          # 15% drawdown -> hard stop + Telegram alert
    "base_risk_per_trade": 0.0075,  # 0.75% of capital max risk per idea (never higher)
}


@dataclass(frozen=True)
class RiskLimits:
    capital: float = 100_000.0
    max_leverage: float = 5.0
    max_position_pct: float = 0.25
    max_dd_kill: float = 0.15
    base_risk_per_trade: float = 0.0075

    @classmethod
    def from_dict(cls, d: dict) -> "RiskLimits":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_LIMITS = RiskLimits.from_dict(DEFAULT_PAPER_LIMITS)


# Whitelisted, optimizer-tunable strategy params (the ONLY things the Critic/Optuna
# may change). The reward function and the hard limits above are intentionally absent.
DEFAULT_STRATEGY = {
    "min_confidence": 0.70,       # skip signals below this
    "max_risk_score": 0.80,       # skip signals above this (0..1, higher=riskier)
    "min_abs_zscore": 0.80,       # require conviction
    "kelly_fraction": 0.30,       # fractional Kelly on top of vol targeting
    "target_vol": 0.012,          # per-position daily vol target
    "outer_loop_every": 50,       # run optimization every N closed trades
    "analyst_prompt_variant": "v1",
}
