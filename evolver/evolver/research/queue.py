"""File-backed, atomic research-candidate queue.

Writer: the autonomous research loop (`scripts/research_tick.py`) appends gate-passing genomes.
Approvers: the Telegram bot (`/candidates`) or CLI (`research_tick.py --approve`). Promotion is
ALWAYS a human action — this module never auto-approves. Same EVOLVER_RESEARCH path everywhere so
phone and CLI act on one queue.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib

STATE = pathlib.Path(os.getenv("EVOLVER_RESEARCH",
                               str(pathlib.Path(__file__).resolve().parents[2] / ".research_state.json")))


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def load():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"cycles": 0, "pending": [], "approved": [], "started": _now()}


def save(s):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    os.replace(tmp, STATE)            # atomic: a crash mid-write can't corrupt the queue


def _sig(c):
    """Family-agnostic signature for dedup (so any family's genome works)."""
    g = c["genome"]
    return (c.get("family", ""),
            tuple(sorted((k, round(v, 2) if isinstance(v, float) else v) for k, v in g.items())))


def add_candidate(c) -> bool:
    """Append a candidate unless a near-identical one is already pending/approved. Returns added?"""
    s = load()
    known = {_sig(p) for p in s["pending"] + s["approved"]}
    if _sig(c) in known:
        return False
    s["pending"].append(c)
    save(s)
    return True


def list_pending():
    return load().get("pending", [])


def approve(cid, actor="cli"):
    s = load()
    hit = next((c for c in s["pending"] if c["id"] == cid), None)
    if not hit:
        return None
    s["pending"] = [c for c in s["pending"] if c["id"] != cid]
    hit.update(approved_at=_now(), approved_by=actor)
    s["approved"].append(hit)
    save(s)
    return hit


def reject(cid, actor="cli"):
    s = load()
    hit = next((c for c in s["pending"] if c["id"] == cid), None)
    if not hit:
        return None
    s["pending"] = [c for c in s["pending"] if c["id"] != cid]
    save(s)
    return hit
