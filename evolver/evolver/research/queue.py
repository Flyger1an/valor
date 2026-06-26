"""File-backed, atomic research-candidate queue.

Writer: the autonomous research loop (`scripts/research_tick.py`) appends gate-passing genomes.
Approvers: the Telegram bot (`/candidates`) or CLI (`research_tick.py --approve`). Promotion is
ALWAYS a human action — this module never auto-approves. Same EVOLVER_RESEARCH path everywhere so
phone and CLI act on one queue.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import pathlib

STATE = pathlib.Path(os.getenv("EVOLVER_RESEARCH",
                               str(pathlib.Path(__file__).resolve().parents[2] / ".research_state.json")))


def _p(path):
    return pathlib.Path(path) if path else STATE   # path=None -> the env-configured default queue


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def update(mutate, path=None):
    """Atomic read-modify-write under an exclusive file lock. `mutate(s)` edits the state in place and
    may return a value (returned by update). This serializes the research loop's commit against the
    bot's /approve and /reject — without it, the loop's seconds-long load→cycle→save window lets a
    stale save() clobber a human Promote/Reject. `path` selects the queue (crypto vs fx)."""
    st = _p(path)
    lock = st.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        s = load(path)
        out = mutate(s)
        save(s, path)
        return out


def load(path=None):
    st = _p(path)
    if st.exists():
        return json.loads(st.read_text())
    return {"cycles": 0, "pending": [], "approved": [], "started": _now()}


def save(s, path=None):
    st = _p(path)
    tmp = st.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    os.replace(tmp, st)               # atomic: a crash mid-write can't corrupt the queue


def _sig(c):
    """Family-agnostic signature for dedup (so any family's genome works)."""
    g = c["genome"]
    return (c.get("family", ""),
            tuple(sorted((k, round(v, 2) if isinstance(v, float) else v) for k, v in g.items())))


def add_candidate(c, path=None) -> bool:
    """Append a candidate unless a near-identical one is already pending/approved. Returns added?"""
    def mut(s):
        known = {_sig(p) for p in s["pending"] + s["approved"]}
        if _sig(c) in known:
            return False
        s["pending"].append(c)
        return True
    return update(mut, path)


def list_pending(path=None):
    return load(path).get("pending", [])


def approve(cid, actor="cli", path=None):
    def mut(s):
        hit = next((c for c in s["pending"] if c["id"] == cid), None)
        if not hit:
            return None
        s["pending"] = [c for c in s["pending"] if c["id"] != cid]
        hit.update(approved_at=_now(), approved_by=actor)
        s["approved"].append(hit)
        return hit
    return update(mut, path)


def reject(cid, actor="cli", path=None):
    def mut(s):
        hit = next((c for c in s["pending"] if c["id"] == cid), None)
        if not hit:
            return None
        s["pending"] = [c for c in s["pending"] if c["id"] != cid]
        return hit
    return update(mut, path)
