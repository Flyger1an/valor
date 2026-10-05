"""Explicitly synthetic, offline system check. Never opens a broker or model connection."""
from dataclasses import asdict, replace
from pathlib import Path

from .agents import intent_digest
from .broker import PaperBroker
from .contracts import Intent, Quote, decimal, encode
from .engine import Engine, write_snapshot
from .ledger import Ledger


def run(policy, root):
    if policy.mode != "paper":
        raise ValueError("smoke requires a paper policy")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "synthetic-smoke.sqlite"
    if path.exists():
        raise ValueError("smoke requires a new output directory; existing evidence is preserved")
    policy = replace(policy, trading_hours_utc=tuple(range(24)), trading_weekdays_utc=tuple(range(7)),
                     study_start_event="runtime_start", study_end_utc="2027-01-01T06:00:00+00:00")
    now = min(1_791_129_600.0, policy.study_end_timestamp - 3600)
    symbol, strategy = policy.allowed_instruments[0], policy.approved_strategies[0]
    book = Ledger(path, policy, PaperBroker.identity)
    def fixture(_system, prompt):
        import json
        requested = json.loads(prompt)["required_output"]
        return encode({**requested, "verdict": "approve", "reason": "SYNTHETIC fixture; no model was called"})
    engine = Engine(book, policy, PaperBroker(book, policy), analyst=fixture, reviewer=fixture)
    engine.supervise({"action": "resume_entries", "risk_scale": "1", "policy_hash": policy.fingerprint,
                      "issued_at": now, "expires_at": now + policy.supervisor_ttl_seconds, "reason": "synthetic test"}, now)
    quotes = {symbol: Quote(symbol, "99.9", "100", now)}
    intent = Intent("synthetic-1", strategy, symbol, "buy", "0.24", "97", now, "synthetic fixture", "100.05")
    first = engine.tick(quotes, [intent], now)
    assert len(first["positions"]) == 1
    book.close()
    book = Ledger(path, policy, PaperBroker.identity)
    engine = Engine(book, policy, PaperBroker(book, policy))  # no agent available after restart
    replay = engine.tick(quotes, [intent], now+1)
    assert len(book.orders()) == 1 and replay["cash"] == first["cash"]
    stopped = engine.tick({symbol: Quote(symbol, "90", "90.1", now+2)}, [], now+2)
    assert not stopped["positions"] and decimal(stopped["realized_pnl"]) < 0
    final = engine.tick({symbol: Quote(symbol, "90", "90.1", policy.study_end_timestamp)}, [], policy.study_end_timestamp)
    assert final["halt"] == "study_complete"
    result = {"passed": True, "mode": "paper", "evidence_type": "synthetic_offline_fixture",
              "real_orders": 0, "model_calls": 0, "checks": ["two reviews", "fill accounting",
              "restart replay prevention", "protective exit without AI", "study deadline"], "snapshot": final}
    write_snapshot(root / "smoke-result.json", result)
    book.close()
    return {k: v for k, v in result.items() if k != "snapshot"}
