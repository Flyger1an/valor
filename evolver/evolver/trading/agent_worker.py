"""Model-only process. It cannot open the execution book, read broker keys, or modify requests."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import time
import urllib.request
import urllib.error
from dataclasses import asdict
from pathlib import Path

from .agents import SYSTEM, parse_object, review_entry, supervision_digest
from .contracts import Intent, Policy, decimal, encode
from .engine import write_snapshot


class BudgetedModel:
    # Explicit price assumptions, not a billing API. Unknown models require configured rates.
    def __init__(self, path, policy, model, key, input_rate="0.10", output_rate="0.50"):
        self.policy, self.model, self.key = policy, model, key
        self.input_rate, self.output_rate = decimal(input_rate), decimal(output_rate)
        if self.input_rate <= 0 or self.output_rate <= 0:
            raise ValueError("positive model price assumptions required")
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS calls(
            id INTEGER PRIMARY KEY, timestamp REAL, day TEXT, model TEXT, cost TEXT,
            input_tokens INTEGER, output_tokens INTEGER, status TEXT)""")
        self.db.execute("CREATE TABLE IF NOT EXISTS model_access(fingerprint TEXT PRIMARY KEY, status TEXT, http_status INTEGER)")
        self.db.commit()
        self.credential_fingerprint = hashlib.sha256((model + ':' + key).encode()).hexdigest()

    def access_status(self):
        row = self.db.execute("SELECT status,http_status FROM model_access WHERE fingerprint=?",
                              (self.credential_fingerprint,)).fetchone()
        return {"status": row[0], "http_status": row[1]} if row else {"status": "unverified"}

    def record_access(self, status, http_status):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO model_access VALUES (?,?,?)",
                            (self.credential_fingerprint, status, http_status))

    def __call__(self, system, prompt):
        if not self.key:
            raise RuntimeError("model credentials missing")
        if self.access_status()["status"] == "blocked":
            raise RuntimeError("model authentication/permissions blocked; replace credentials before retrying")
        if len((system + prompt).encode()) > 24_000:
            raise ValueError("review input exceeds budgeted size")
        now = time.time()
        day = dt.datetime.fromtimestamp(now, dt.timezone.utc).date().isoformat()
        # Reserve a conservative byte-based input bound plus the fixed output ceiling BEFORE calling.
        reserve = (len((system + prompt).encode()) + 1000) * self.input_rate / 1_000_000 + 1500 * self.output_rate / 1_000_000
        self.db.execute("BEGIN IMMEDIATE")
        try:
            rows = self.db.execute("SELECT cost FROM calls WHERE day=?", (day,)).fetchall()
            if len(rows) >= self.policy.max_model_calls_per_day or sum((decimal(r[0]) for r in rows), decimal(0)) + reserve > self.policy.max_model_cost_per_day:
                raise RuntimeError("daily model budget exhausted")
            rowid = self.db.execute("INSERT INTO calls VALUES (NULL,?,?,?,?,?,?,?)",
                                   (now, day, self.model, str(reserve), 0, 0, "reserved")).lastrowid
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        body = {"model": self.model, "store": False, "max_completion_tokens": 1500,
                "reasoning_effort": "none", "response_format": {"type": "json_object"},
                "messages": [{"role": "developer", "content": system}, {"role": "user", "content": prompt}]}
        request = urllib.request.Request("https://api.openai.com/v1/chat/completions",
                  data=encode(body).encode(), method="POST",
                  headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        # No automatic retries: an uncertain call remains charged at its conservative reservation.
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                value = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                self.record_access("blocked", exc.code)
            raise
        self.record_access("available", 200)
        usage = value["usage"]
        tokens_in, tokens_out = int(usage["prompt_tokens"]), int(usage["completion_tokens"])
        if tokens_in < 0 or tokens_out < 0:
            raise ValueError("invalid usage receipt")
        cost = (tokens_in * self.input_rate + tokens_out * self.output_rate) / 1_000_000
        with self.db:
            self.db.execute("UPDATE calls SET cost=?,input_tokens=?,output_tokens=?,status='complete' WHERE id=?",
                            (str(cost), tokens_in, tokens_out, rowid))
        choice = value["choices"][0]
        if choice["finish_reason"] != "stop" or not isinstance(choice["message"].get("content"), str):
            raise ValueError("incomplete model response")
        return choice["message"]["content"]

    def usage(self):
        rows = self.db.execute("SELECT cost,input_tokens,output_tokens,status FROM calls").fetchall()
        return {"calls": len(rows), "estimated_cost_usd": str(sum((decimal(r[0]) for r in rows), decimal(0))),
                "input_tokens": sum(r[1] for r in rows), "output_tokens": sum(r[2] for r in rows),
                "uncertain_calls": sum(r[3] != "complete" for r in rows),
                "model_connection": self.access_status(),
                "pricing": "configured estimates; excludes VPS, data and taxes"}


def promotion_reviews(policy, evidence, analyst, reviewer):
    result = {}
    expected = {"verdict": "approve or reject", "evidence_hash": evidence["evidence_hash"],
                "policy_hash": policy.fingerprint, "reason": "explain evidence and limitations"}
    for role, model in (("analyst", analyst), ("reviewer", reviewer)):
        response = parse_object(model(SYSTEM + "\nIndependently assess a strategy promotion, not an order.",
                                      encode({"policy": policy.fingerprint, "evidence": evidence,
                                              "required_output": expected})))
        if (set(response) != set(expected) or response["evidence_hash"] != expected["evidence_hash"]
                or response["policy_hash"] != policy.fingerprint or response["verdict"] not in {"approve", "reject"}
                or not isinstance(response["reason"], str) or not 1 <= len(response["reason"]) <= 1500):
            raise ValueError("invalid promotion review")
        result[role] = response
    return result


def process_request(request, policy, analyst, reviewer, now):
    body = request["body"]
    if body["policy_hash"] != policy.fingerprint:
        raise ValueError("request policy mismatch")
    if request["kind"] == "trade":
        result = {}
        review_entry(Intent(**body["intent"]), policy, body["snapshot"], analyst, reviewer,
                     lambda role, decision: result.update({role: decision}))
        return result
    if request["kind"] == "supervision":
        current_scale = body["snapshot"]["supervisor"]["scale"]
        schema = {"action": "pause_entries, resume_entries, or reduce_risk",
                  "risk_scale": f"A decimal string greater than 0 and at most 1. Current scale is {current_scale}. pause_entries keeps the current scale; reduce_risk can only lower it. resume_entries may restore toward 1 when evidence supports recovery, subject to an independent review. Never use zero.",
                  "reason": "Explain opportunity, recent strategy outcomes, costs, news and health. Restore risk only on improved evidence, never just to recover losses."}
        result = parse_object(analyst(SYSTEM + "\nSupervise an experiment. Paper exploration is allowed inside the policy; simulated profits do not validate live trading.",
                              encode({"policy": asdict(policy), "snapshot": body["snapshot"], "required_output": schema})))
        if set(result) != set(schema):
            raise ValueError("invalid supervision output")
        if result["action"] not in {"pause_entries", "resume_entries", "reduce_risk"} or not 0 < decimal(result["risk_scale"]) <= 1:
            raise ValueError("supervisor exceeded approved risk scale")
        command = {**result, "policy_hash": policy.fingerprint, "issued_at": now,
                   "expires_at": now + policy.supervisor_ttl_seconds}
        if decimal(result["risk_scale"]) > decimal(current_scale):
            expected = {"verdict": "approve or reject", "command_hash": supervision_digest(command),
                        "policy_hash": policy.fingerprint, "reason": "Explain whether evidence supports restoring risk within the original cap, at most 800 characters"}
            review = parse_object(reviewer(SYSTEM + "\nIndependently review restoration of risk toward the owner's original cap. Reject loss-chasing or unsupported restoration.",
                                           encode({"command": command, "snapshot": body["snapshot"], "required_output": expected})))
            if (set(review) != set(expected) or review["command_hash"] != expected["command_hash"]
                    or review["policy_hash"] != policy.fingerprint or review["verdict"] != "approve"
                    or not isinstance(review["reason"], str) or not 1 <= len(review["reason"]) <= 800):
                raise ValueError("independent risk restoration review did not approve")
            command["risk_restore_review"] = review
        return command
    if request["kind"] == "promotion":
        return promotion_reviews(policy, body["evidence"], analyst, reviewer)
    raise ValueError("unsupported agent request")


def agent_tick(mailbox, policy, analyst, reviewer, usage, now=None):
    now = time.time() if now is None else now
    # Supervision first, then near-expiry trade requests. Requests have immutable deadlines.
    requests = sorted(mailbox.pending(now), key=lambda r: (r["kind"] != "supervision", r["expires_at"]))
    for request in requests:
        if request["expires_at"] <= time.time():
            continue
        try:
            result = process_request(request, policy, analyst, reviewer, time.time())
        except Exception as exc:
            result = {"error_type": type(exc).__name__, "verdict": "reject"}
        mailbox.answer(request, result, time.time())
    write_snapshot(mailbox.responses / "usage.json", {"timestamp": time.time(), **usage()})
