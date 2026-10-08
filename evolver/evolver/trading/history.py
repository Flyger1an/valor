"""Immutable candle evidence and prospective recovery after provider revisions.

A changed observation is never substituted into history. New valid observations
continue accumulating, but decisions use only a contiguous segment after the
observed integrity boundary. Old decisions and their supporting records survive.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path

from .contracts import decimal as D, encode

FIELDS = ("open", "high", "low", "close", "volume")


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def retain(directory, identity, payload):
    """Content-addressed, append-only record. Repeated observations do not rewrite it."""
    path = Path(directory) / (digest(identity) + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        from .ipc import read_object
        if read_object(path)["identity"] != identity:
            raise ValueError("history evidence identity mismatch")
        return path.stem, False
    data = encode({"identity": identity, **payload}).encode()
    # Publish only a complete durable record; readers never see partial JSON.
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix=".evidence-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            from .ipc import read_object
            if read_object(path)["identity"] != identity:
                raise ValueError("history evidence identity mismatch")
            return path.stem, False
        return path.stem, True
    finally:
        os.unlink(temporary)


def validate_bar(bar, now):
    stamp = float(D(bar["timestamp"]))
    op, hi, lo, close, volume = (D(bar[k]) for k in FIELDS)
    if (stamp <= 0 or stamp % 300 or stamp + 300 > now
            or not 0 < lo <= min(op, close) <= max(op, close) <= hi or volume < 0):
        raise ValueError("invalid or incomplete closed candle")
    return stamp


def contiguous(bars, cutoff=0):
    """Return the latest uninterrupted five-minute segment; never interpolate gaps."""
    result = []
    for bar in bars:
        if bar["timestamp"] < cutoff:
            continue
        if result and bar["timestamp"] != result[-1]["timestamp"] + 300:
            result = []
        result.append(bar)
    return result


def advance(existing, incoming, now, prior, record, legacy_error=None):
    """Append valid bars despite old revisions; record each distinct incident first."""
    integrity = dict(prior or {})
    cutoff = integrity.get("cutoff", 0)
    merged = {b["timestamp"]: b for b in existing}
    pending = dict(integrity.get("pending", {}))
    observations = []

    def incident(kind, incoming_bar=None, original=None):
        nonlocal cutoff
        identity = {"kind": kind, "incoming": incoming_bar, "original": original}
        event_id, created = record(identity, {"observed_at": now})
        try:
            stamp = float(D(incoming_bar["timestamp"]))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            stamp = None
        # Repeated old revisions cannot keep moving the recovery boundary.
        if not cutoff or stamp is not None and stamp >= cutoff or created and stamp is None:
            cutoff = math.ceil(now / 300) * 300
            integrity.update(cutoff=cutoff, invalidated_at=now, generation=event_id)
        integrity["last_incident"] = event_id

    if legacy_error and not integrity:
        incident("legacy_quarantine", {"reason": legacy_error})
    for bar in incoming:
        try:
            stamp = validate_bar(bar, now)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            incident("invalid_closed_bar", bar)
            continue
        previous = merged.get(stamp)
        if previous is not None:
            if any(D(previous[k]) != D(bar[k]) for k in FIELDS):
                incident("closed_bar_revision", bar, previous)
            continue  # Retain original bytes even if numeric encodings are equivalent.
        key = str(stamp)
        candidate = pending.get(key)
        if candidate and all(D(candidate["bar"][k]) == D(bar[k]) for k in FIELDS):
            if now-candidate["first_seen_at"] >= 60:
                merged[stamp] = candidate["bar"]
                del pending[key]
        else:
            # A newly closed candle remains provisional until two observations at
            # least one minute apart agree. Every provisional version is retained;
            # it cannot support an approval or rewrite an accepted observation.
            observations.append({"bar": bar, "previous": candidate["bar"] if candidate else None})
            pending[key] = {"bar": bar, "first_seen_at": now}
    if observations:
        record({"kind": "uncommitted_closed_bars", "observations": observations}, {"observed_at": now})
    history = [merged[t] for t in sorted(merged)]
    clean = contiguous(history, cutoff)
    integrity.update(cutoff=cutoff, generation=integrity.get("generation", "original"), pending=pending,
                     clean_bars=len(clean), latest_closed_bar=clean[-1]["timestamp"] if clean else None)
    return history, integrity


def context(data):
    return {s: {k: v.get(k, 0 if k == "cutoff" else "original") for k in ("cutoff", "generation")}
            for s, v in data.get("history_integrity", {}).items()}


def window(data, symbol, required, now):
    """Validate the exact evidence window used by the deterministic strategy."""
    bars = data.get("histories", {}).get(symbol, [])
    cutoff = data.get("history_integrity", {}).get(symbol, {}).get("cutoff", 0)
    clean = contiguous(bars, cutoff)
    selected = clean[-required:]
    reason = data.get("history_errors", {}).get(symbol)
    try:
        for bar in selected:
            validate_bar(bar, now)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        reason = "invalid_closed_bar"
    if not reason and len(selected) < required:
        reason = "insufficient_contiguous_closed_bars"
    if not reason and not 0 <= now - selected[-1]["timestamp"] - 300 <= 600:
        reason = "stale_closed_bars"
    binding = {"symbol": symbol, "required_bars": required, "cutoff": cutoff,
               "generation": data.get("history_integrity", {}).get(symbol, {}).get("generation", "original"),
               "window_hash": digest(selected), "first_bar": selected[0]["timestamp"] if selected else None,
               "last_bar": selected[-1]["timestamp"] if selected else None, "unavailable_reason": reason}
    return selected, reason, binding


def source_reason(data, policy, now):
    if data.get("policy_hash") != policy.fingerprint or not 0 <= now-data.get("timestamp", 0) <= 600:
        return "market_history_stale_or_policy_mismatch"
    if policy.mode != "paper" and (data.get("source") != "alpaca" or data.get("venue") != "us"):
        return "market_history_source_mismatch"
    if data.get("source") not in {"alpaca", "coinbase_public"}:
        return "market_history_source_mismatch"
    return None


def binding_valid(binding, data, policy, now, *, require_usable=False):
    if not isinstance(binding, dict) or source_reason(data, policy, now):
        return False
    if binding.get("source") != data.get("source") or binding.get("venue") != data.get("venue"):
        return False
    symbols = binding.get("symbols")
    if not isinstance(symbols, dict) or not symbols:
        return False
    for symbol, expected in symbols.items():
        if symbol not in policy.allowed_instruments:
            return False
        try:
            _, reason, actual = window(data, symbol, expected["required_bars"], now)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return False
        if (require_usable and reason) or actual != expected:
            return False
    return True


def authority_valid(binding, data, policy, now):
    """A lease can outlive a candle, but cannot outlive loss of its data provenance."""
    if not isinstance(binding, dict) or source_reason(data, policy, now):
        return False
    if binding.get("source") != data.get("source") or binding.get("venue") != data.get("venue"):
        return False
    for symbol, expected in binding.get("symbols", {}).items():
        if expected.get("unavailable_reason"):
            continue  # This asset was already disclosed as unavailable.
        current = data.get("history_integrity", {}).get(symbol, {})
        if (current.get("cutoff", 0) != expected.get("cutoff")
                or current.get("generation", "original") != expected.get("generation")
                or symbol in data.get("history_errors", {})):
            return False
    return bool(binding.get("symbols"))
