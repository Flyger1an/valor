"""Single order writer. Strategy workers submit intents; policy and broker facts decide admission."""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from .agents import review_entry, validate_supervision
from .contracts import Intent, Policy, Quote, decimal, encode
from .ledger import Ledger


class Engine:
    def __init__(self, ledger: Ledger, policy: Policy, broker, *, analyst=None, reviewer=None,
                 kill_path: str | Path | None = None, refresh_quotes=None, monotonic=time.monotonic):
        if policy.mode != broker.mode:
            raise ValueError("policy and broker modes differ")
        self.book, self.policy, self.broker = ledger, policy, broker
        self.analyst, self.reviewer = analyst, reviewer
        self.kill_path = Path(kill_path) if kill_path else None
        self.refresh_quotes, self.monotonic = refresh_quotes, monotonic

    def supervise(self, command: dict, now: float):
        current = self.book.get("supervisor")
        validated = validate_supervision(command, self.policy, current, now)
        with self.book.db:
            self.book.set("supervisor", validated)
            self.book.event(now, "supervisor.decision", validated)

    def _reconcile(self, now):
        if hasattr(self.broker, "reconcile"):
            try:
                return self.broker.reconcile(now)
            except Exception as exc:
                from .accounting import AccountingError
                if isinstance(exc, AccountingError):
                    self.book.halt("broker_accounting_mismatch", now)
                with self.book.db:
                    self.book.set("entry_pause", "broker_reconciliation_unavailable")
                return False
        for order in self.book.orders(pending_only=True):
            try:
                report = self.broker.lookup(order["client_id"])
                if report is None:
                    # An absent receipt is NOT permission to resend. The first request may have filled.
                    self.book.unknown(order["client_id"], now)
                    self.book.halt("unresolved_order", now)
                    return False
                self.book.apply_report(report, now)
            except Exception:
                self.book.halt("reconciliation_failed", now)
                return False
        try:
            account = self.broker.account()
            ours = {s: decimal(p["quantity"]) for s, p in self.book.positions().items()}
            theirs = {s: decimal(q) for s, q in account["positions"].items() if decimal(q)}
            expected_open = {o["client_id"] for o in self.book.orders(pending_only=True)}
            if (ours != theirs or abs(decimal(account["cash"]) - decimal(self.book.get("cash"))) > decimal("0.00000001")
                    or set(account["open_order_ids"]) != expected_open):
                self.book.halt("broker_ledger_mismatch", now)
                return False
        except Exception:
            self.book.halt("account_unavailable", now)
            return False
        return True

    def _submit(self, intent, quote, now):
        if hasattr(self.broker, "prepare"):
            try:
                if not self.broker.prepare(intent, now):
                    return False
            except Exception:
                with self.book.db:
                    self.book.set("entry_pause", "order_preparation_unconfirmed")
                return False
        self.book.reserve(intent, now)  # durable reservation BEFORE the side effect
        try:
            report = self.broker.submit(intent, quote)
            if report.client_id != intent.client_id:
                raise ValueError("broker returned another order")
            if hasattr(self.broker, "apply_report"):
                return self.broker.apply_report(report, now)
            self.book.apply_report(report, now)
        except Exception:
            self.book.unknown(intent.client_id, now)
            self.book.halt("unresolved_order", now)
            return False
        return True

    def _cancel_pending(self, now, force=False):
        for order in self.book.orders(pending_only=True):
            if not force and now - order["created"] <= self.policy.max_quote_age_seconds:
                continue
            try:
                report = self.broker.cancel(order["client_id"])
                if report is None or report.client_id != order["client_id"]:
                    raise ValueError("unconfirmed cancellation")
                if hasattr(self.broker, "apply_report"):
                    if not self.broker.apply_report(report, now):
                        return False
                else:
                    self.book.apply_report(report, now)
            except Exception:
                self.book.halt("cancellation_unconfirmed", now)
                return False
        return True

    def snapshot(self, quotes: dict[str, Quote], now: float):
        positions = self.book.positions()
        equity = decimal(self.book.get("cash"))
        fresh = True
        exposure = decimal(0)
        for symbol, pos in positions.items():
            quote = quotes.get(symbol)
            if quote is None or not quote.fresh(now, self.policy.max_quote_age_seconds):
                fresh = False
                value = decimal(pos["cost_basis"])
            else:
                value = decimal(pos["quantity"]) * quote.bid
            exposure += value
            equity += value
        dust = self.book.get("dust", {})
        for symbol, pos in dust.items():
            quote = quotes.get(symbol)
            if quote is None or not quote.fresh(now, self.policy.max_quote_age_seconds):
                fresh = False
                value = decimal(pos["cost_basis"])
            else:
                value = decimal(pos["quantity"])*quote.bid
            exposure += value
            equity += value
        return {"schema_version": 1, "mode": self.policy.mode, "quote_currency": self.policy.quote_currency,
                "policy_hash": self.policy.fingerprint, "revision": self.book.revision, "timestamp": now,
                "cash": self.book.get("cash"), "equity": str(equity), "exposure": str(exposure),
                "realized_pnl": self.book.get("realized_pnl"), "marks_fresh": fresh,
                "entry_pause": self.book.get("entry_pause", ""),
                "news": self.book.get("news", {"required": False}),
                "accounting": self.book.get("accounting", {}),
                "protection": self.book.get("protection", {"complete": self.policy.mode == "paper", "kind": "local_paper"}),
                "dust": dust,
                "protective_orders": [o for o in self.book.orders(pending_only=True, include_protection=True)
                                      if o["purpose"] == "protection"],
                "halt": self.book.get("halt") or ("daily_loss_limit" if self._daily_halted(now) else ""),
                "supervisor": self.book.get("supervisor"), "study_end_utc": self.policy.study_end_utc,
                "study_started_at": self.book.get("study_started_at"),
                "study_deadline": self._study_deadline(),
                "study_start_event": self.policy.study_start_event,
                "study_phase": "live_study" if self.policy.mode == "live" else "paper_preflight",
                "runtime_started_at": self.book.get("runtime_started_at"),
                "paper_deadline": self._paper_deadline(),
                "starting_cash": str(self.policy.starting_cash),
                "day_start_equity": self.book.get("day_start_equity"),
                "peak_equity": self.book.get("peak_equity"),
                "broker_reconciled_at": self.book.get("broker_reconciled_at"),
                "active_strategy": self.book.get("active_strategy"), "learning": self.book.get("learning", {}),
                "positions": list(positions.values()), "pending_orders": self.book.orders(pending_only=True),
                "performance": self.book.performance(),
                "daily_equity": [dict(r) for r in self.book.db.execute("SELECT * FROM equity_days ORDER BY day")],
                "quotes": {s: asdict(q) for s, q in quotes.items()},
                "events": self.book.recent_events()}

    def _entry_reason(self, intent, quotes, now, snapshot):
        p = self.policy
        if self.book.get("halt") or self._daily_halted(now) or (self.kill_path and self.kill_path.exists()):
            return "hard_halt"
        if self.book.get("entry_pause"):
            return self.book.get("entry_pause")
        news = snapshot.get("news", {})
        if news.get("required") and (news.get("entry_blocked", True)
                                      or not 0 <= now-news.get("fetched_at", 0) <= 600):
            return "news_unavailable_or_stale"
        if hasattr(self.broker, "entry_minimum_reason"):
            reason = self.broker.entry_minimum_reason(intent)
            if reason:
                return reason
        if self._expiry_reason(now):
            return self._expiry_reason(now)
        if p.mode == "live" and self.book.get("validated_strategy") != intent.strategy:
            return "strategy_validation_required"
        supervisor = self.book.get("supervisor")
        if supervisor["action"] == "pause_entries" or now >= supervisor["expires_at"]:
            return "supervisor_paused_or_expired"
        if intent.strategy not in p.approved_strategies or intent.instrument not in p.allowed_instruments:
            return "strategy_or_instrument_not_approved"
        if not 0 <= now - intent.timestamp <= p.max_quote_age_seconds:
            return "stale_intent"
        quote = quotes.get(intent.instrument)
        if quote is None or not quote.fresh(now, p.max_quote_age_seconds) or not snapshot["marks_fresh"]:
            return "stale_quote"
        if (quote.ask - quote.bid) / quote.ask * 10000 > p.max_spread_bps:
            return "spread_limit"
        utc = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        if utc.hour not in p.trading_hours_utc or utc.weekday() not in p.trading_weekdays_utc:
            return "outside_trading_hours"
        if intent.instrument in self.book.positions():
            return "position_already_open"
        if self.book.orders(pending_only=True):
            return "pending_order"  # reserve centrally, no concurrent exposure races
        day = dt.datetime.fromtimestamp(now, dt.timezone.utc).date().isoformat()
        entries = [o for o in self.book.orders() if json.loads(o["intent"])["side"] == "buy" and
                   dt.datetime.fromtimestamp(o["created"], dt.timezone.utc).date().isoformat() == day]
        if len(entries) >= p.max_trades_per_day:
            return "daily_order_limit"
        entry_price = intent.limit_price
        if quote.ask > entry_price or entry_price > quote.ask * (1 + p.slippage_bps / 10000):
            return "entry_price_limit"
        notional = intent.quantity * entry_price
        scale = decimal(supervisor["scale"])
        # Stair-step growth: 25 stays 25 until starting capital doubles; drawdowns reduce the cap.
        equity = decimal(snapshot["equity"])
        multiple = decimal(1)
        while equity >= p.starting_cash * multiple * 2:
            multiple *= 2
        size_cap = min(p.max_position_notional * multiple,
                       max(equity, decimal(0)) * p.max_position_notional / p.starting_cash) * scale
        total_cap = min(p.max_total_notional * multiple,
                        max(equity, decimal(0)) * p.max_total_notional / p.starting_cash)
        if notional > size_cap or decimal(snapshot["exposure"]) + notional > total_cap:
            return "exposure_limit"
        fees = notional * p.fee_bps / 10000
        if notional + fees > decimal(snapshot["cash"]):
            return "insufficient_cash"
        if not 0 < intent.stop_price < quote.bid:
            return "invalid_protective_stop"
        stop_fill = intent.stop_price * (1 - p.slippage_bps / 10000)
        estimated_loss = (entry_price - stop_fill) * intent.quantity + fees + stop_fill * intent.quantity * p.fee_bps / 10000
        if estimated_loss > p.max_loss_per_trade * max(equity, decimal(0)) / p.starting_cash * scale:
            return "trade_loss_limit"
        day_start = decimal(self.book.get("day_start_equity", str(equity)))
        remaining = p.daily_loss_limit * day_start / p.starting_cash - max(decimal(0), day_start - equity)
        reserved = decimal(0)
        for symbol, pos in self.book.positions().items():
            stop_receipt = (decimal(pos["stop_price"]) * (1 - p.slippage_bps / 10000)
                            * (1 - p.fee_bps / 10000))
            reserved += max(decimal(0), (quotes[symbol].bid - stop_receipt) * decimal(pos["quantity"]))
        if estimated_loss + reserved > remaining:
            return "remaining_daily_loss_budget"
        return None

    def _daily_halted(self, now):
        return self.book.get("daily_halt") == dt.datetime.fromtimestamp(now, dt.timezone.utc).date().isoformat()

    def _study_deadline(self):
        started = self.book.get("study_started_at")
        deadlines = [d for d in (self.policy.study_end_timestamp,
                     started + self.policy.max_study_days * 86400 if started else None) if d is not None]
        return min(deadlines) if deadlines else None

    def _paper_deadline(self):
        started = self.book.get("runtime_started_at")
        return started + self.policy.max_paper_days * 86400 if started and self.policy.mode != "live" else None

    def _expiry_reason(self, now):
        if self._study_deadline() is not None and now >= self._study_deadline():
            return "study_complete"
        if self._paper_deadline() is not None and now >= self._paper_deadline():
            return "paper_preflight_complete"
        return None

    def tick(self, quotes: dict[str, Quote], intents: list[Intent], now: float):
        if not decimal(now) > 0:
            raise ValueError("invalid clock")
        if any(symbol != q.instrument for symbol, q in quotes.items()):
            raise ValueError("quote map identity mismatch")
        # Serialize the full risk-check/network/reconciliation cycle, including across processes.
        with self.book.path.with_suffix(".worker.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self._tick(quotes, intents, now)

    def _tick(self, quotes, intents, now):
        with self.book.db:
            if now < self.book.get("last_tick", 0) - 1:
                self.book.halt("clock_moved_backwards", now)
            self.book.set("last_tick", max(now, self.book.get("last_tick", 0)))
            if not self.book.get("runtime_started_at"):
                self.book.set("runtime_started_at", now)
                self.book.event(now, "runtime.started", {"mode": self.policy.mode})
            if not self.book.get("study_started_at") and self.policy.study_start_event == "runtime_start":
                self.book.set("study_started_at", now)
                self.book.event(now, "study.started", {"mode": self.policy.mode, "deadline": self._study_deadline()})
        if self._expiry_reason(now):
            self.book.halt(self._expiry_reason(now), now)
        reconciled = self._reconcile(now)
        if not reconciled:
            return self.snapshot(quotes, now)  # broker-held stops remain active during data/API outages
        with self.book.db:
            self.book.set("broker_reconciled_at", now)
        snapshot = self.snapshot(quotes, now)
        if snapshot["marks_fresh"]:
            equity = decimal(snapshot["equity"])
            day = dt.datetime.fromtimestamp(now, dt.timezone.utc).date().isoformat()
            with self.book.db:
                if self.book.get("day") != day:
                    self.book.set("day", day)
                    self.book.set("day_start_equity", self.book.get("last_equity", str(equity)))
                peak = max(decimal(self.book.get("peak_equity")), equity)
                self.book.set("peak_equity", str(peak))
                self.book.set("last_equity", str(equity))
                self.book.set("max_observed_drawdown_pct", str(max(
                    decimal(self.book.get("max_observed_drawdown_pct", "0")), (peak-equity) / peak * 100)))
                self.book.db.execute("INSERT OR REPLACE INTO equity_days VALUES (?,?,?)", (day, now, str(equity)))
            day_start = decimal(self.book.get("day_start_equity"))
            if day_start - equity >= self.policy.daily_loss_limit * day_start / self.policy.starting_cash:
                with self.book.db:
                    if not self._daily_halted(now):
                        self.book.set("daily_halt", day)
                        self.book.event(now, "risk.daily_halt", {"day": day})
            if peak - equity >= self.policy.max_drawdown * peak / self.policy.starting_cash:
                self.book.halt("drawdown_limit", now)
        if self.kill_path and self.kill_path.exists():
            self.book.halt("operator_kill_switch", now)
        if not self._cancel_pending(now, bool(self.book.get("halt") or self._daily_halted(now))):
            return self.snapshot(quotes, now)

        # Stop-loss and risk liquidation are pre-authorized in the entry plan. AI outages cannot veto them.
        protective = []
        pending_symbols = {json.loads(o["intent"])["instrument"] for o in self.book.orders(pending_only=True)}
        for symbol, pos in self.book.positions().items():
            quote = quotes.get(symbol)
            if symbol in pending_symbols or quote is None or not quote.fresh(now, self.policy.max_quote_age_seconds):
                continue
            if self.book.get("halt") or self._daily_halted(now) or quote.bid <= decimal(pos["stop_price"]):
                quantity = decimal(pos["quantity"])
                if hasattr(self.broker, "round_quantity"):
                    quantity = self.broker.round_quantity(symbol, quantity)
                if quantity <= 0:
                    continue
                protective.append(Intent(f"protect:{pos['opened']}:{now}", pos["strategy"], symbol, "sell",
                                         quantity, decimal(0), now, "protective_exit"))
        for intent in protective + intents:
            if self.book.order(intent.client_id):
                continue
            quote = quotes.get(intent.instrument)
            if intent.side == "sell":
                pos = self.book.positions().get(intent.instrument)
                if (not pos or pos["strategy"] != intent.strategy or intent.quantity > decimal(pos["quantity"])
                        or quote is None or not quote.fresh(now, self.policy.max_quote_age_seconds)
                        or self.book.orders(pending_only=True)):
                    continue
                if intent not in protective:
                    def record_exit(role, decision):
                        with self.book.db:
                            self.book.event(now, "agent.review", {"role": role, **decision})
                    started = self.monotonic()
                    approved = review_entry(intent, self.policy, self.snapshot(quotes, now),
                                            self.analyst, self.reviewer, record_exit)
                    now += max(0, self.monotonic() - started)
                    if not approved:
                        continue
                    if self.refresh_quotes:
                        try:
                            quotes = self.refresh_quotes()
                        except Exception:
                            continue
                    quote = quotes.get(intent.instrument)
                    if (quote is None or not quote.fresh(now, self.policy.max_quote_age_seconds)
                            or not 0 <= now - intent.timestamp <= self.policy.max_quote_age_seconds):
                        continue
            else:
                snapshot = self.snapshot(quotes, now)
                reason = self._entry_reason(intent, quotes, now, snapshot)
                if reason:
                    with self.book.db:
                        self.book.event(now, "entry.blocked", {"client_id": intent.client_id, "reason": reason})
                    continue
                def record(role, decision):
                    with self.book.db:
                        self.book.event(now, "agent.review", {"role": role, **decision})
                started = self.monotonic()
                approved = review_entry(intent, self.policy, snapshot, self.analyst, self.reviewer, record)
                now += max(0, self.monotonic() - started)
                if not approved:
                    continue
                if self.refresh_quotes:
                    try:
                        quotes = self.refresh_quotes()
                    except Exception:
                        continue
                snapshot = self.snapshot(quotes, now)
                reason = self._entry_reason(intent, quotes, now, snapshot)
                if reason:
                    with self.book.db:
                        self.book.event(now, "entry.blocked", {"client_id": intent.client_id, "reason": reason})
                    continue
                quote = quotes[intent.instrument]
            if not self._submit(intent, quote, now):
                break
        self._reconcile(now)
        return self.snapshot(quotes, now)


def write_snapshot(path: str | Path, snapshot: dict):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
    tmp.write_text(encode(snapshot))
    os.replace(tmp, target)
