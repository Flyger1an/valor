"""Explicit PAPER/DEMO entry-session expansion; never an execution client.

Only the entry window, the daily entry-attempt pace and the model call budget may widen. Every risk, loss, exposure, fee,
spread, freshness, strategy, instrument and spend limit stays byte-identical, so the policy
fingerprint changes only because the session changed. Accounting, approvals, supervisor state
and source events are preserved; the boundary is an append-only event.
"""
from dataclasses import asdict
import json
import math
from pathlib import Path
import sqlite3

from .contracts import Policy, encode

SESSION_FIELDS = ("trading_hours_utc", "trading_weekdays_utc", "max_model_calls_per_day", "max_trades_per_day")
MAX_MODEL_CALLS_PER_DAY = 500
MAX_TRADES_PER_DAY = 48


def validate_session_expansion(old: Policy, new: Policy):
    before, after = asdict(old), asdict(new)
    for key in SESSION_FIELDS:
        before.pop(key), after.pop(key)
    if old.mode not in {"paper", "demo"} or new.mode != old.mode or before != after:
        raise ValueError("session migration may only change entry hours, weekdays, entry pace and model call budget")
    if (not set(old.trading_hours_utc) <= set(new.trading_hours_utc)
            or not set(old.trading_weekdays_utc) <= set(new.trading_weekdays_utc)
            or tuple(sorted(set(new.trading_hours_utc))) != tuple(new.trading_hours_utc)
            or tuple(sorted(set(new.trading_weekdays_utc))) != tuple(new.trading_weekdays_utc)
            or not old.max_model_calls_per_day <= new.max_model_calls_per_day <= MAX_MODEL_CALLS_PER_DAY
            or not old.max_trades_per_day <= new.max_trades_per_day <= MAX_TRADES_PER_DAY):
        raise ValueError("session migration may only widen the sorted entry window and raise the bounded call budget")
    if old.fingerprint == new.fingerprint:
        raise ValueError("session migration must change the session")
    return {"added_hours_utc": sorted(set(new.trading_hours_utc) - set(old.trading_hours_utc)),
            "added_weekdays_utc": sorted(set(new.trading_weekdays_utc) - set(old.trading_weekdays_utc)),
            "model_calls_per_day": [old.max_model_calls_per_day, new.max_model_calls_per_day],
            "entry_attempts_per_day": [old.max_trades_per_day, new.max_trades_per_day]}


def migrate_ledger(path, old, new, now):
    """Offline explicit migration with every writer stopped. Accounting is never reset."""
    change = validate_session_expansion(old, new)
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
        boundary = {"at": now, "from_policy": old.fingerprint, "to_policy": new.fingerprint, **change,
                    "old_policy": asdict(old), "new_policy": asdict(new),
                    "accounting_reset": False, "risk_limits_changed": False}
        row = db.execute("SELECT value FROM meta WHERE key='session_history'").fetchone()
        history = json.loads(row[0]) if row else []
        history.append(boundary)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('session_history',?)", (encode(history),))
        db.execute("UPDATE meta SET value=? WHERE key='identity'", (encode({**identity, "policy": new.fingerprint}),))
        db.execute("INSERT INTO events(timestamp,event,payload) VALUES (?,?,?)",
                   (now, "policy.session_changed", encode(boundary)))
        db.commit()
        return boundary
    finally:
        db.close()


def record_feed_transition(market_dir, old, new, now):
    """Declare the one reviewed provenance change the quote admission guard may accept."""
    from .engine import write_snapshot
    from .ipc import read_object
    validate_session_expansion(old, new)
    path = Path(market_dir) / "policy-transitions.json"
    value = read_object(path, {"transitions": []}, limit=100_000)
    entry = {"from": old.fingerprint, "to": new.fingerprint, "at": now, "kind": "session_expansion"}
    if any(t["from"] == entry["from"] and t["to"] == entry["to"] for t in value["transitions"]):
        return value
    value["transitions"].append(entry)
    write_snapshot(path, value)
    return value
