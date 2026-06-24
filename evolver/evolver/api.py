"""FastAPI: signal ingestion (validates the locked contract) + dashboard API.

Run: uvicorn evolver.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator

from evolver.core.signal import SIGNAL_TYPES
from evolver.loop import run_inner
from evolver.graph import runtime as rt
from evolver.safety import kill_switch


class SignalIn(BaseModel):
    signal_id: str
    timestamp: str
    type: str
    assets: list[str]
    zscore: float
    spread_value: float
    expected_convergence_hours: float = Field(gt=0)
    risk_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    regime: str
    metadata: dict = {}

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in SIGNAL_TYPES:
            raise ValueError(f"unknown signal type: {v}")
        return v


app = FastAPI(title="Valor Evolver", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "halted": rt.is_halted(), "kill_switch": kill_switch.active()}


@app.get("/kpis")
def kpis() -> dict:
    return rt.current_kpis()


@app.post("/ingest")
def ingest(sig: SignalIn) -> dict:
    """Runs one inner-loop cycle for the signal and returns the decision + fill + KPIs."""
    return run_inner(sig.model_dump())
