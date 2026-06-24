from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict


class LoopState(TypedDict, total=False):
    signal: dict                     # validated raw signal (locked contract)
    thread_id: str
    regime: str
    portfolio: dict
    similar: list                    # vector-recalled analogous past signals
    risk_params: dict
    decision: dict
    fill: dict
    kpis: dict
    critique: dict
    proposal: dict | None            # outer-loop tweak (versioned)
    approved: bool | None
    messages: Annotated[list, add]   # reducer: append
