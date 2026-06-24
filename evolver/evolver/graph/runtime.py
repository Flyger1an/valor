"""Shared, file-backed loop state so EVERY process (api, loop, bot, dashboard) reads
one book. Ledger, raw signals, and risk/strategy state live on the shared volume
(EVOLVER_LEDGER / EVOLVER_SIGNALS / EVOLVER_STATE), written atomically. This unifies
/kpis, /status, and the dashboard.

Concurrency: atomic writes (os.replace) prevent corruption; last-writer-wins on the
state doc. Fine at paper signal rates with the loop as primary writer. The cloud /
single-writer-safe version is Postgres with a row lock (roadmap).
"""
from __future__ import annotations

import json
import os
import pathlib
from types import SimpleNamespace

from evolver.config import DEFAULT_LIMITS, DEFAULT_STRATEGY
from evolver.core.risk import AdaptiveRiskManager
from evolver.core.sim import PerpPaperSim
from evolver.core.kpis import compute_kpis

LIMITS = DEFAULT_LIMITS
SIM = PerpPaperSim(LIMITS.capital)

LEDGER_PATH = pathlib.Path(os.getenv("EVOLVER_LEDGER", ".evolver/ledger.jsonl"))
SIGNALS_PATH = pathlib.Path(os.getenv("EVOLVER_SIGNALS", ".evolver/signals.jsonl"))
STATE_PATH = pathlib.Path(os.getenv("EVOLVER_STATE", ".evolver/state.json"))


def _read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _atomic_write(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)  # atomic on POSIX


def _append_jsonl(path: pathlib.Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


# ---- state (risk manager + strategy + pending) ----
def load_state():
    s = _read_json(STATE_PATH, {})
    rm = AdaptiveRiskManager.from_state(s.get("rm"), LIMITS)
    risk_params = s.get("risk_params") or {**rm.params(), "risk_per_trade": LIMITS.base_risk_per_trade}
    strategy = s.get("strategy") or {**DEFAULT_STRATEGY, "version": "v1"}
    return rm, risk_params, strategy, s


def save_state(rm: AdaptiveRiskManager, risk_params: dict, strategy: dict) -> None:
    prev = _read_json(STATE_PATH, {})
    _atomic_write(STATE_PATH, {
        "rm": rm.to_state(),
        "risk_params": risk_params,
        "strategy": strategy,
        "pending": prev.get("pending", {}),
        "signals_count": prev.get("signals_count", 0),
    })


def is_halted() -> bool:
    return load_state()[0].halt


# ---- signals + ledger ----
def record_signal(d: dict) -> None:
    _append_jsonl(SIGNALS_PATH, d)
    s = _read_json(STATE_PATH, {})
    s["signals_count"] = int(s.get("signals_count", 0)) + 1
    _atomic_write(STATE_PATH, s)


def read_signals() -> list:
    if not SIGNALS_PATH.exists():
        return []
    out = []
    for line in SIGNALS_PATH.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def record_fill(fill, signal) -> None:
    _append_jsonl(LEDGER_PATH, {**fill.__dict__, "type": signal.type, "regime": signal.regime})


def _read_ledger() -> list:
    if not LEDGER_PATH.exists():
        return []
    out = []
    for line in LEDGER_PATH.read_text().splitlines():
        try:
            out.append(SimpleNamespace(**json.loads(line)))
        except Exception:
            continue
    return out


def current_kpis() -> dict:
    rm, _risk_params, strategy, s = load_state()
    k = compute_kpis(_read_ledger(), LIMITS.capital)
    k.update({
        "equity": round(rm.equity, 2),
        "drawdown": round(rm.drawdown, 4),
        "halt": rm.halt,
        "active_version": strategy.get("version", "v1"),
        "loop_state": "halted" if rm.halt else "running",
        "open_positions": 0,
        "signals_seen": int(s.get("signals_count", 0)),
    })
    return k


# ---- pending proposals (human-gated deploy) ----
def register_pending(thread_id: str, proposal: dict) -> None:
    s = _read_json(STATE_PATH, {})
    s.setdefault("pending", {})[thread_id] = proposal
    _atomic_write(STATE_PATH, s)


def get_pending():
    s = _read_json(STATE_PATH, {})
    for tid, p in (s.get("pending") or {}).items():
        return {"thread_id": tid, **p}
    return None


def apply_pending(thread_id: str, approved: bool):
    from evolver.safety import audit
    s = _read_json(STATE_PATH, {})
    prop = (s.get("pending") or {}).pop(thread_id, None)
    if prop is None:
        return None
    if approved:
        strat = s.get("strategy") or {**DEFAULT_STRATEGY, "version": "v1"}
        for p in prop.get("proposals", []):
            strat[p["param"]] = p["to"]
        strat["version"] = prop.get("version", strat.get("version", "v1"))
        s["strategy"] = strat
    _atomic_write(STATE_PATH, s)
    audit("deploy.decision", {"approved": approved, "version": prop.get("version"),
                              "proposals": prop.get("proposals")})
    return prop
