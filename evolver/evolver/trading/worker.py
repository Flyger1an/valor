"""Execution coordinator. Model and research work happens in other processes."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path

from .contracts import Intent, Policy, Quote, decimal, encode
from .engine import Engine, write_snapshot
from .ipc import Mailbox, read_object
from .strategies import BY_VERSION, make_intents


class Worker:
    def __init__(self, book, policy: Policy, broker, state, outbox, inbox, market, research, news=None):
        self.book, self.policy = book, policy
        self.state, self.outbox, self.inbox = Path(state), Path(outbox), Path(inbox)
        self.market, self.research = Path(market), Path(research)
        self.news = Path(news) if news is not None else None
        self.mailbox = Mailbox(self.outbox / "requests", self.inbox / "responses")
        self.engine = Engine(book, policy, broker, kill_path=self.state / "kill.flag")
        if not all(v in BY_VERSION for v in policy.approved_strategies):
            raise ValueError("policy references a strategy absent from this build")
        with book.db:
            if not book.get("active_strategy"):
                book.set("active_strategy", policy.approved_strategies[0])

    def quotes(self):
        value = read_object(self.market / "quotes.json", {})
        if value.get("policy_hash") != self.policy.fingerprint:
            return {}
        if self.policy.mode != "paper" and value.get("source") != "alpaca":
            return {}
        return {s: Quote(**q) for s, q in value.get("quotes", {}).items() if s in self.policy.allowed_instruments}

    def _facts(self, snapshot, data=None):
        # Do not copy the growing activity journal/reserve list or engineering drill into prompts.
        # Requests retain the complete selected news evidence for later outcome evaluation.
        keys = ("mode", "policy_hash", "timestamp", "cash", "equity", "exposure", "realized_pnl",
                "marks_fresh", "entry_pause", "halt", "supervisor", "starting_cash", "day_start_equity",
                "peak_equity", "broker_reconciled_at", "active_strategy", "positions", "quotes", "news")
        facts = {k: snapshot.get(k) for k in keys}
        orders = self.book.orders()
        engineering = {o["client_id"] for o in orders
                       if json.loads(o["intent"])["reason"].startswith("broker_demo_validation")}
        outcomes = [c for c in self.book.closed_trades() if c.get("lot_id") not in engineering]
        facts.update(recent_strategy_outcomes=outcomes[-6:],
                     strategy_completed_trades=len(outcomes),
                     strategy_realized_pnl=str(sum((decimal(c["realized_pnl"]) for c in outcomes), decimal(0))),
                     engineering_excluded=True, daily_equity=snapshot["daily_equity"][-7:],
                     pending_orders=[{"client_id": o["client_id"], "status": o["status"]} for o in snapshot["pending_orders"]])
        strategy = BY_VERSION.get(snapshot.get("active_strategy"))
        facts["strategy_parameters"] = asdict(strategy) if strategy else None
        facts["accounting"] = {k: snapshot.get("accounting", {}).get(k) for k in
                               ("balance_match", "fees_provisional", "net_cash_flows")}
        facts["protection"] = snapshot.get("protection", {})
        usage = read_object(self.inbox / "responses" / "usage.json", {})
        from .costs import cost_summary
        facts["costs"] = cost_summary(snapshot, usage, snapshot["timestamp"])
        from .evidence import describe
        from .history import retain
        quotes = {s: Quote(**q) for s, q in snapshot["quotes"].items()}
        data = self._history_data() if data is None else data
        evidence, binding, archive = describe(data, snapshot, quotes, self.policy, snapshot["timestamp"],
            lambda intent: self.engine._entry_reason(intent, quotes, snapshot["timestamp"], snapshot))
        retain(self.outbox / "candle-evidence", archive, {})
        facts["market_evidence"] = evidence
        facts["history_binding"] = binding
        return facts

    def _history_data(self):
        try:
            return read_object(self.market / "signals.json", {})
        except (ValueError, TypeError, KeyError, OSError):
            return {}

    def _invalidate(self, request, reason, now):
        self.mailbox.invalidate(request, reason, now)
        self.book.event(now, reason, {"request": request["id"]})

    def _history_matches(self, request, now, symbol=None):
        from .history import binding_valid
        binding = request["body"].get("snapshot", {}).get("history_binding")
        if symbol and binding:
            binding = {**binding, "symbols": {symbol: binding.get("symbols", {}).get(symbol, {})}}
        return binding_valid(binding, self._history_data(), self.policy, now, require_usable=bool(symbol))

    def _news(self, now):
        if self.news is None:
            return
        from .news import assess
        try:
            value = assess(read_object(self.news / "snapshot.json", {}, limit=30_000), now)
        except (ValueError, TypeError, KeyError, OSError):
            value = assess({}, now)
        previous = self.book.get("news", {})
        with self.book.db:
            self.book.set("news", value)
            if (previous.get("status"), previous.get("evidence_hash")) != (value["status"], value["evidence_hash"]):
                self.book.event(now, "news.context", {"status": value["status"], "evidence_hash": value["evidence_hash"],
                                                     "headline_count": len(value["items"])})

    def _supervision(self, snapshot, now):
        request = self.book.get("supervision_request")
        if request:
            result = self.mailbox.result(request, now)
            # Only a pause cannot authorize entries. reduce_risk also permits
            # entries at a smaller scale, so it needs still-valid evidence.
            needs_history = result is None or result.get("action") != "pause_entries"
            if needs_history and not self._history_matches(request, now):
                with self.book.db:
                    self._invalidate(request, "supervisor.history_changed", now)
                    self.book.set("supervision_request", None)
                request = None
        if request:
            result = self.mailbox.result(request, now)
            if result is not None:
                try:
                    reviewed = request["body"]["snapshot"].get("news", {})
                    current = snapshot.get("news", {})
                    if current.get("required") and (reviewed.get("evidence_hash"), reviewed.get("status")) != (current.get("evidence_hash"), current.get("status")):
                        raise ValueError("supervision news changed")
                    self.engine.supervise(result, now)
                    with self.book.db:
                        self.book.set("supervision_history_binding", request["body"]["snapshot"].get("history_binding"))
                except (ValueError, TypeError, KeyError):
                    with self.book.db:
                        self.book.event(now, "supervisor.rejected", {"request": request["id"]})
                with self.book.db:
                    self.book.set("supervision_request", None)
            elif now < request["expires_at"]:
                return
        lease = self.book.get("supervisor")
        news_context = {k: snapshot.get("news", {}).get(k) for k in ("evidence_hash", "status")}
        news_changed = news_context != self.book.get("supervision_news_context", news_context)
        utc = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        session = utc.hour in self.policy.trading_hours_utc and utc.weekday() in self.policy.trading_weekdays_utc
        if (not session or snapshot["halt"] or self.engine._expiry_reason(now)
                or (lease["expires_at"] - now > self.policy.supervisor_ttl_seconds / 2 and not news_changed)
                or now - self.book.get("last_supervision_request", 0) < 300):
            return
        request = self.mailbox.put("supervision", {"policy_hash": self.policy.fingerprint,
                                   "snapshot": self._facts(snapshot)}, now, 120)
        with self.book.db:
            self.book.set("supervision_request", request)
            self.book.set("last_supervision_request", now)
            self.book.set("supervision_news_context", news_context)

    def _learning(self, snapshot, now):
        if snapshot["halt"] or self.engine._expiry_reason(now):
            return  # Do not spend review calls or promote strategies after the operating window.
        request = self.book.get("promotion_request")
        if request:
            if not self._promotion_history_matches(request["body"]["evidence"], now):
                with self.book.db:
                    self._invalidate(request, "promotion.history_changed", now)
                    self.book.set("promotion_request", None)
                return
            result = self.mailbox.result(request, now)
            evidence = request["body"]["evidence"]
            if result is None and now < request["expires_at"]:
                return
            approved = result is not None and set(result) == {"analyst", "reviewer"}
            if approved:
                for role in ("analyst", "reviewer"):
                    decision = result[role]
                    approved = approved and (set(decision) == {"verdict", "reason", "evidence_hash", "policy_hash"}
                        and decision["verdict"] == "approve" and decision["evidence_hash"] == evidence["evidence_hash"]
                        and decision["policy_hash"] == self.policy.fingerprint
                        and isinstance(decision["reason"], str) and 1 <= len(decision["reason"]) <= 1500)
            if approved and (snapshot["positions"] or snapshot["pending_orders"]):
                return  # strategy ownership cannot change while a position or order is outstanding
            with self.book.db:
                if approved and not snapshot["halt"] and not self.engine._expiry_reason(now):
                    self.book.set("previous_strategy", self.book.get("active_strategy"))
                    self.book.set("active_strategy", evidence["challenger"])
                    self.book.set("promoted_at", now)
                    if evidence["source"] == "alpaca":
                        self.book.set("validated_strategy", evidence["challenger"])
                    self.book.event(now, "strategy.promoted", {"evidence": evidence, "reviews": result})
                    status = "promoted"
                else:
                    status = "review_rejected"
                    self.book.event(now, "strategy.rejected", {"evidence_hash": evidence["evidence_hash"]})
                self.book.set("learning", {**evidence, "status": status,
                                          "finalized_evidence_hash": evidence["evidence_hash"]})
                self.book.set("promotion_request", None)
            return
        report = read_object(self.research / "assessment.json", {})
        if (report.get("policy_hash") != self.policy.fingerprint
                or not 0 <= now - report.get("evaluated_at", 0) <= 7200):
            return
        if not self._promotion_history_matches(report, now):
            return
        current = self.book.get("learning", {})
        if report.get("evidence_hash") and report["evidence_hash"] == current.get("finalized_evidence_hash"):
            return
        with self.book.db:
            self.book.set("learning", report)
        if report.get("status") != "review_required" or report.get("challenger") not in self.policy.approved_strategies:
            return
        if self.policy.mode != "paper" and report.get("source") != "alpaca":
            return
        # The research volume is writable only by the deterministic research worker, never agents.
        # Repeat the numerical admission checks before spending model calls on a promotion.
        c, baseline = report["candidate"], report["baseline"]
        if (c["trades"] < 30 or c["net_return"] <= 0 or c["net_return"] < baseline["net_return"]
                or c["profit_factor"] < 1.2 or c["drawdown"] > .10 or report["end"] <= report["cutoff"]):
            return
        request = self.mailbox.put("promotion", {"policy_hash": self.policy.fingerprint, "evidence": report}, now, 3600)
        with self.book.db:
            self.book.set("promotion_request", request)

    def _promotion_history_matches(self, report, now):
        from .history import context, contiguous, digest, source_reason
        try:
            data = read_object(self.market / "history.json", {})
            binding = report.get("history_binding")
            signals = self._history_data()
            if (not binding or source_reason(data, self.policy, now) or data.get("history_errors")
                    or binding.get("context") != context(data) or binding.get("source") != data.get("source")
                    or binding.get("venue") != data.get("venue") or source_reason(signals, self.policy, now)
                    or signals.get("history_errors") or context(signals) != context(data)):
                return False
            end = binding["end"]
            if end is None:
                return False
            clean = {s: contiguous(b, binding["context"].get(s, {}).get("cutoff", 0))
                     for s, b in data["histories"].items()}
            return binding["hashes"] == {s: digest([b for b in bars if b["timestamp"] <= end]) for s, bars in clean.items()}
        except (ValueError, TypeError, KeyError, OSError):
            return False

    def _rollback(self, now):
        previous, current = self.book.get("previous_strategy"), self.book.get("active_strategy")
        if not previous or previous == current or self.book.positions() or self.book.orders(pending_only=True):
            return
        outcomes = [r for r in self.book.closed_trades() if r["closed"] >= self.book.get("promoted_at", now)][-3:]
        if len(outcomes) == 3 and all(o["strategy"] == current and decimal(o["realized_pnl"]) < 0 for o in outcomes):
            with self.book.db:
                self.book.set("active_strategy", previous)
                self.book.set("previous_strategy", None)
                self.book.set("validated_strategy", None)
                lease = self.book.get("supervisor")
                self.book.set("supervisor", {**lease, "action": "pause_entries", "expires_at": now})
                self.book.event(now, "strategy.rolled_back", {"from": current, "to": previous,
                               "reason": "three consecutive losing closes after promotion"})

    def tick(self, now):
        self._news(now)
        quotes = self.quotes()
        snapshot = self.engine.tick(quotes, [], now)  # protective checks NEVER wait for a model
        from .history import authority_valid
        lease = self.book.get("supervisor")
        binding = self.book.get("supervision_history_binding")
        if (lease.get("action") != "pause_entries" and binding is not None
                and not authority_valid(binding, self._history_data(), self.policy, now)):
            with self.book.db:
                self.book.set("supervisor", {**lease, "action": "pause_entries", "expires_at": now})
                self.book.event(now, "supervisor.history_invalidated", {"reason": "supporting_history_unreliable"})
            snapshot = self.engine.snapshot(quotes, now)
        self._supervision(snapshot, now)
        self._learning(snapshot, now)
        self._rollback(now)
        pending = self.book.get("pending_reviews", {})
        for cid, item in list(pending.items()):
            request = item["request"]
            if not self._history_matches(request, now, item["intent"]["instrument"]):
                with self.book.db:
                    self._invalidate(request, "review.history_changed", now)
                    seen = self.book.get("seen_signals", {})
                    self.book.set("seen_signals", {k: v for k, v in seen.items() if v != cid})
                del pending[cid]
                continue
            result = self.mailbox.result(request, now)
            if result is None and now < request["expires_at"]:
                continue
            if result is not None:
                # A changed headline bundle needs fresh independent reviews; never use an old approval.
                reviewed = request["body"]["snapshot"].get("news", {})
                current = self.book.get("news", {})
                if current.get("required") and (reviewed.get("evidence_hash") != current.get("evidence_hash")
                                                or (item["intent"]["side"] == "buy" and current.get("entry_blocked", True))):
                    with self.book.db:
                        self.book.event(now, "review.news_changed", {"client_id": cid})
                        seen = self.book.get("seen_signals", {})
                        self.book.set("seen_signals", {k: v for k, v in seen.items() if v != cid})
                    del pending[cid]
                    continue
                self.engine.analyst = lambda *_: encode(result.get("analyst", {}))
                self.engine.reviewer = lambda *_: encode(result.get("reviewer", {}))
                self.engine.context_valid = lambda intent, stamp: (intent.client_id != cid or
                    self._history_matches(request, stamp, intent.instrument))
                try:
                    snapshot = self.engine.tick(quotes, [Intent(**item["intent"])], now)
                finally:
                    self.engine.analyst = self.engine.reviewer = self.engine.context_valid = None
            del pending[cid]
        from .history import source_reason, window
        bars = self._history_data()
        if not source_reason(bars, self.policy, now):
            snapshot = self.engine.snapshot(quotes, now)
            seen = self.book.get("seen_signals", {})
            spec = BY_VERSION[self.book.get("active_strategy")]
            clean = {}
            positions = self.book.positions()
            for symbol in self.policy.allowed_instruments:
                active = BY_VERSION.get(positions.get(symbol, {}).get("strategy"), spec)
                selected, reason, _ = window(bars, symbol, active.slow*3+1, now)
                clean[symbol] = [] if reason else selected
            for intent in make_intents(spec, clean, quotes,
                                       snapshot, self.policy, now, bars.get("increments")):
                key = intent.instrument + ":" + intent.side
                if seen.get(key) == intent.client_id or intent.client_id in pending or self.book.order(intent.client_id):
                    continue
                if intent.side == "buy" and self.engine._entry_reason(intent, quotes, now, snapshot):
                    continue
                if not clean.get(intent.instrument):
                    continue
                request = self.mailbox.put("trade", {"policy_hash": self.policy.fingerprint,
                                "intent": asdict(intent), "snapshot": self._facts(snapshot, bars)}, now,
                                self.policy.max_quote_age_seconds)
                pending[intent.client_id] = {"request": request, "intent": asdict(intent)}
                seen[key] = intent.client_id
            with self.book.db:
                self.book.set("seen_signals", seen)
        with self.book.db:
            self.book.set("pending_reviews", pending)
        snapshot = self.engine.snapshot(quotes, now)
        quote_data = read_object(self.market / "quotes.json", {})
        snapshot["market_feed_received_at"] = quote_data.get("timestamp", 0)
        snapshot["allowed_instruments"] = list(self.policy.allowed_instruments)
        snapshot["market_venue"] = quote_data.get("venue")
        utc = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        snapshot["entry_session_open"] = utc.hour in self.policy.trading_hours_utc and utc.weekday() in self.policy.trading_weekdays_utc
        usage = read_object(self.inbox / "responses" / "usage.json", {})
        snapshot["model_usage"] = usage
        snapshot["market_source"] = bars.get("source", "unavailable")
        assessment = read_object(self.research / "assessment.json", {})
        snapshot["pipeline"] = {"signals_updated_at": bars.get("timestamp", 0),
                                "research_updated_at": assessment.get("evaluated_at", 0)}
        from .costs import cost_summary
        snapshot["costs"] = cost_summary(snapshot, usage, now)
        from .readiness import assess
        snapshot["readiness"] = assess(self.book, now)
        snapshot["unaccounted_costs"] = snapshot["costs"]["unverified_costs"]
        if "estimated_cost_usd" in usage:
            snapshot["estimated_pnl_after_model_costs"] = str(decimal(snapshot["costs"]["net_trading_pnl_usd"]) - decimal(usage["estimated_cost_usd"]))
        write_snapshot(self.outbox / "snapshot.json", snapshot)
        return snapshot
