"""The locked signal contract (mirrors shared/signal.schema.json)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SIGNAL_TYPES = {
    "cointegration_spread",
    "funding_arb",
    "basis_trade",
    "stat_arb_pair",
    "triangular",
}

_REQUIRED = {
    "signal_id", "timestamp", "type", "assets", "zscore", "spread_value",
    "expected_convergence_hours", "risk_score", "confidence", "regime",
}


@dataclass(frozen=True)
class Signal:
    signal_id: str
    timestamp: str
    type: str
    assets: tuple[str, ...]
    zscore: float
    spread_value: float
    expected_convergence_hours: float
    risk_score: float          # 0..1, lower = safer
    confidence: float          # 0..1
    regime: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "Signal":
        missing = _REQUIRED - set(d)
        if missing:
            raise ValueError(f"signal {d.get('signal_id','?')} missing fields: {sorted(missing)}")
        if d["type"] not in SIGNAL_TYPES:
            raise ValueError(f"unknown signal type: {d['type']}")
        for f in ("risk_score", "confidence"):
            if not 0.0 <= float(d[f]) <= 1.0:
                raise ValueError(f"{f} must be in [0,1], got {d[f]}")
        if float(d["expected_convergence_hours"]) <= 0:
            raise ValueError("expected_convergence_hours must be > 0")
        return Signal(
            signal_id=str(d["signal_id"]),
            timestamp=str(d["timestamp"]),
            type=str(d["type"]),
            assets=tuple(d["assets"]),
            zscore=float(d["zscore"]),
            spread_value=float(d["spread_value"]),
            expected_convergence_hours=float(d["expected_convergence_hours"]),
            risk_score=float(d["risk_score"]),
            confidence=float(d["confidence"]),
            regime=str(d["regime"]),
            metadata=dict(d.get("metadata", {})),
        )
