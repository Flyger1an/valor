"""Atomic, expiring requests across read-only container boundaries; no executable payloads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .agents import parse_object
from .contracts import encode
from .engine import write_snapshot


def read_object(path, default=None, limit=20_000_000):
    target = Path(path)
    if not target.exists():
        return default
    if target.stat().st_size > limit:
        raise ValueError("runtime document exceeds size limit")
    return parse_object(target.read_text())


class Mailbox:
    def __init__(self, requests, responses):
        self.requests, self.responses = Path(requests), Path(responses)

    def put(self, kind, body, now, ttl):
        request = {"kind": kind, "body": body, "created_at": now, "expires_at": now + ttl}
        key = hashlib.sha256(encode(request).encode()).hexdigest()
        request["id"] = key
        write_snapshot(self.requests / (key + ".json"), request)
        return request

    def result(self, request, now):
        if (self.requests.parent / "invalidations" / (request["id"] + ".json")).exists():
            return None
        if not request["created_at"] <= now < request["expires_at"]:
            return None
        value = read_object(self.responses / (request["id"] + ".json"), limit=30_000)
        if value is None:
            return None
        if (set(value) != {"id", "completed_at", "result"} or value["id"] != request["id"]
                or not request["created_at"] <= value["completed_at"] <= now
                or not isinstance(value["result"], dict)):
            raise ValueError("invalid or replayed response envelope")
        return value["result"]

    def pending(self, now):
        for path in sorted(self.requests.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            request = read_object(path, limit=100_000)
            if (set(request) != {"id", "kind", "body", "created_at", "expires_at"}
                    or request["id"] != path.stem):
                continue
            digest = hashlib.sha256(encode({k: v for k, v in request.items() if k != "id"}).encode()).hexdigest()
            if digest != request["id"] or not request["created_at"] <= now < request["expires_at"]:
                continue
            if not (self.responses / path.name).exists() and not (self.requests.parent / "invalidations" / path.name).exists():
                yield request

    def invalidate(self, request, reason, now):
        path = self.requests.parent / "invalidations" / (request["id"] + ".json")
        if not path.exists():
            write_snapshot(path, {"id": request["id"], "invalidated_at": now, "reason": reason})

    def answer(self, request, result, now):
        write_snapshot(self.responses / (request["id"] + ".json"),
                       {"id": request["id"], "completed_at": now, "result": result})
