"""Bounded model facts with full, locally retained candle support and exact bindings."""
from __future__ import annotations

import statistics
from dataclasses import asdict

from .contracts import decimal as D
from .history import digest, source_reason, window
from .strategies import BY_VERSION, ema, entry_signal, exit_signal, make_intents


def indicators(spec, bars):
    closes = [float(b["close"]) for b in bars]
    if len(closes) < spec.slow * 3 + 1:
        return {}
    if spec.family == "ema_trend":
        return {"previous_fast": ema(closes[:-1], spec.fast), "previous_slow": ema(closes[:-1], spec.slow),
                "current_fast": ema(closes, spec.fast), "current_slow": ema(closes, spec.slow)}
    if spec.family == "breakout":
        return {"last_close": closes[-1], "previous_high": max(float(b["high"]) for b in bars[-spec.slow-1:-1])}
    mean, sd = statistics.mean(closes[-spec.slow:]), statistics.pstdev(closes[-spec.slow:])
    return {"last_close": closes[-1], "mean": mean, "sd": sd, "entry_threshold": mean-2*sd}


def describe(data, snapshot, quotes, policy, now, gate):
    """Show pre-approval opportunities even while the supervisor pauses execution."""
    spec = BY_VERSION[snapshot["active_strategy"]]
    bad_source = source_reason(data, policy, now)
    positions = {p["instrument"]: p for p in snapshot["positions"]}
    cards, raw, bindings, clean = {}, {}, {}, {}
    for symbol in sorted(policy.allowed_instruments):
        active = BY_VERSION.get(positions.get(symbol, {}).get("strategy"), spec)
        selected, blocked, binding = window(data, symbol, active.slow*3+1, now)
        blocked = bad_source or blocked
        q = quotes.get(symbol)
        market_blocks = [blocked] if blocked else []
        if q is None or not q.fresh(now, policy.max_quote_age_seconds):
            market_blocks.append("stale_quote")
        spread = (q.ask-q.bid)/q.ask*10000 if q else None
        if spread is not None and spread > policy.max_spread_bps:
            market_blocks.append("spread_limit")
        clean[symbol] = [] if blocked else selected
        raw[symbol] = selected
        bindings[symbol] = binding
        cards[symbol] = {"strategy": active.version, "candle_interval_seconds": 300,
            "closed_candles": len(selected), "required_candles": active.slow*3+1,
            "history_status": blocked or "usable", "history_binding": binding,
            "recent_candles": selected[-2:], "indicators": indicators(active, selected) if not blocked else {},
            "entry_signal": bool(not blocked and entry_signal(active, selected)),
            "exit_signal": bool(not blocked and exit_signal(active, selected)),
            "quote_age_seconds": now-q.timestamp if q else None,
            "spread_bps": str(spread) if spread is not None else None, "market_blocks": market_blocks,
            "candidate": None}
    for intent in make_intents(spec, clean, quotes, snapshot, policy, now, data.get("increments")):
        card = cards[intent.instrument]
        card["candidate"] = {**asdict(intent), "client_id": intent.client_id,
                             "notional": str(intent.quantity*(intent.limit_price or quotes[intent.instrument].bid)),
                             "execution_block": gate(intent) if intent.side == "buy" else None}
    binding = {"source": data.get("source"), "venue": data.get("venue"), "symbols": bindings}
    archive = {"schema_version": 1, "policy_hash": policy.fingerprint, "binding": binding, "windows": raw}
    facts = {"schema_version": 1, "observed_at": now, "source_received_at": data.get("timestamp"),
             "source": data.get("source"), "venue": data.get("venue"),
             "candle_evidence_hash": digest(archive), "symbols": cards,
             "meaning": "Pre-approval deterministic signals and blockers; none is an order authorization."}
    return facts, binding, archive
