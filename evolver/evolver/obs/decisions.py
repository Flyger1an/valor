"""Decision-attribution ledger — the provenance record the learning loops read from.

One JSONL row per analyst decision (inner loop AND shadow), carrying what the fill ledger drops:
the FULL decision object (incl. rationale/entry/exit), the true source (llm vs deterministic
fallback — previously unknowable: llm_decide fell back silently and MLflow logged the model name
anyway), the exact prompt variant + sha that produced it, the signal snapshot at decision time,
decision latency, and the calibration version applied to sizing. Without this, "why did it trade
and did the stated reason hold?" is unanswerable; with it, calibration can be conditioned on
provenance (LLM-only vs fallback-only tracks).

Append-only, best-effort (a ledger hiccup must never take down the loop), same JSONL-on-shared-
volume pattern as every other book. Path: EVOLVER_DECISIONS.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

_ROOT = pathlib.Path(__file__).resolve().parents[2]   # CWD-independent (host cron vs container)
DECISIONS_PATH = pathlib.Path(os.getenv("EVOLVER_DECISIONS", str(_ROOT / ".evolver/decisions.jsonl")))


def record_decision(service: str, signal: dict, decision: dict, meta: dict,
                    path: pathlib.Path | None = None) -> None:
    """Append one attributed decision. service: 'inner' | 'shadow'. meta comes from
    analyst.decide_with_meta (source/model/prompt variant+sha/latency/calibration)."""
    p = path or DECISIONS_PATH
    # bound the row: several containers append concurrently, and small O_APPEND writes stay
    # atomic — an unbounded LLM rationale could split across syscalls and corrupt the JSONL
    dec = {**decision, "rationale": str(decision.get("rationale", ""))[:500]}
    row = {
        "ts": int(time.time()),
        "service": service,
        "signal": {k: signal.get(k) for k in
                   ("signal_id", "type", "assets", "zscore", "spread_value", "confidence",
                    "risk_score", "regime", "expected_convergence_hours") if k in signal},
        "decision": dec,
        **meta,
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass  # attribution must never break the decision path
