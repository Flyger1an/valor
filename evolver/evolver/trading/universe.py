"""Explicit additive PAPER/DEMO universe boundaries; never an execution client."""
from dataclasses import asdict
import json
import math
from pathlib import Path
import sqlite3

from .contracts import Policy, encode


def validate_expansion(old: Policy, new: Policy):
    before, after = asdict(old), asdict(new)
    a, b = before.pop("allowed_instruments"), after.pop("allowed_instruments")
    if (old.mode not in {"paper", "demo"} or before != after or not set(a) < set(b)
            or len(b) > 64 or tuple(sorted(b)) != b):
        raise ValueError("universe migration must only add sorted paper/demo instruments; all limits stay fixed")
    return sorted(set(b)-set(a))


def migrate_ledger(path, old, new, now):
    """Offline explicit migration. Preserve all accounting, approvals and source events."""
    added = validate_expansion(old, new)
    if not Path(path).is_file() or not math.isfinite(now) or now <= 0:
        raise ValueError("existing ledger and valid boundary required")
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("BEGIN IMMEDIATE")
        identity = json.loads(db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()[0])
        if identity["policy"] != old.fingerprint:
            raise ValueError("source policy changed before migration")
        latest = db.execute("SELECT MAX(timestamp) FROM events").fetchone()[0]
        if latest is not None and now < latest:
            raise ValueError("migration cannot precede source events")
        boundary = {"at": now, "from_policy": old.fingerprint, "to_policy": new.fingerprint,
                    "added": added, "old_policy": asdict(old), "new_policy": asdict(new),
                    "accounting_reset": False, "risk_limits_changed": False}
        row = db.execute("SELECT value FROM meta WHERE key='universe_history'").fetchone()
        history = json.loads(row[0]) if row else []
        history.append(boundary)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('universe_history',?)", (encode(history),))
        db.execute("UPDATE meta SET value=? WHERE key='identity'", (encode({**identity, "policy": new.fingerprint}),))
        db.execute("INSERT INTO events(timestamp,event,payload) VALUES (?,?,?)",
                   (now, "policy.universe_changed", encode(boundary)))
        db.commit()
        return boundary
    finally:
        db.close()
