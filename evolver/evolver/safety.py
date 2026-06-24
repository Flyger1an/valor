"""Safety layer: kill-switch, drawdown circuit breaker, RBAC, append-only audit log.

Mirrors the Valor TS platform's guardrail philosophy. No real money path exists here;
even paper promotion is human-gated.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

_AUDIT = pathlib.Path(os.getenv("EVOLVER_AUDIT", ".evolver/audit.jsonl"))
_KILL = pathlib.Path(os.getenv("EVOLVER_KILL", ".evolver/kill.flag"))


def audit(action: str, payload: dict, actor: str = "system") -> None:
    _AUDIT.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "actor": actor, "action": action, "payload": payload}
    with _AUDIT.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


# ---- RBAC ----
def _ids(env: str) -> set[str]:
    return {x.strip() for x in os.getenv(env, "").split(",") if x.strip()}


def is_admin(chat_id) -> bool:
    return str(chat_id) in _ids("TELEGRAM_ADMIN_CHAT_IDS")


def is_observer(chat_id) -> bool:
    return str(chat_id) in _ids("TELEGRAM_OBSERVER_CHAT_IDS") or is_admin(chat_id)


# ---- kill switch / circuit breaker ----
class _KillSwitch:
    def active(self) -> bool:
        return _KILL.exists()

    def activate(self, actor: str = "system", reason: str = "") -> None:
        _KILL.parent.mkdir(parents=True, exist_ok=True)
        _KILL.write_text(json.dumps({"actor": actor, "reason": reason}))
        audit("kill_switch.activate", {"reason": reason}, actor)

    def reset(self, actor: str = "system") -> None:
        if _KILL.exists():
            _KILL.unlink()
        audit("kill_switch.reset", {}, actor)


kill_switch = _KillSwitch()


def trip_circuit_breaker(reason: str) -> None:
    """Auto-halt the loop and alert Ops. Called when drawdown >= max_dd_kill."""
    kill_switch.activate(actor="circuit_breaker", reason=reason)
    try:
        from evolver.telegram.notify import alert_admins
        alert_admins(f"🛑 CIRCUIT BREAKER: {reason}. Loop halted.")
    except Exception:
        pass
