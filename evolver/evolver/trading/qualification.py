"""Explicit, paper-endpoint-only API exercise. Never counts as strategy performance.

This is an operator-invoked plumbing test, including outside strategy entry hours.
It cannot run in live/local-simulation mode or alongside the execution worker.
The fixed $12 notional is inside the study's cash/position limits. It does not
simulate an AI approval or grant a strategy any qualification evidence.
"""
from __future__ import annotations

import fcntl
import time
from dataclasses import asdict
from pathlib import Path

from .alpaca import latest_quotes
from .contracts import Intent, decimal as D
from .engine import write_snapshot


def run(book, broker, policy, root, *, clock=time.time, sleep=time.sleep, retry_terminal=False):
    if policy.mode != "demo" or broker.http.base != "https://paper-api.alpaca.markets":
        raise ValueError("qualification exercises are restricted to Alpaca paper")
    root = Path(root)
    with (book.path.parent / "worker.lock").open("a") as lock, book.path.with_suffix(".worker.lock").open("a") as cycle_lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(cycle_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = book.get("demo_qualification")
        no_order_in_attempt = prior and not any(o["created"] >= prior["started_at"] for o in book.orders(include_protection=True))
        retry = (retry_terminal and prior and (prior["status"] in {"entry_unfilled", "entry_rejected", "preflight_blocked"} or no_order_in_attempt)
                 and not book.positions() and not book.orders(pending_only=True, include_protection=True))
        if prior and not retry:
            return prior  # An interrupted drill requires inspection, never an automatic new buy.
        broker.reconcile(clock(), protect=False)
        if book.positions() or (book.orders(include_protection=True) and not retry) or book.get("halt"):
            raise ValueError("qualification needs an admitted, unused, flat paper book")
        if retry:
            with book.db:
                book.set("prior_qualification_runs", book.get("prior_qualification_runs", [])+[prior])
        report = {"kind": "broker_demo_plumbing_only", "mode": "demo", "policy_hash": policy.fingerprint,
                  "started_at": clock(), "status": "running", "counts_as_strategy_evidence": False,
                  "max_entry_notional_usd": "12", "checks": {"account_authenticated": True}}
        def save():
            with book.db:
                book.set("demo_qualification", report)
            write_snapshot(root / "qualification.json", report)
        save()
        def settle(cid):
            deadline = clock()+45
            while clock() < deadline:
                if broker.reconcile(clock(), protect=False):
                    order = book.order(cid)
                    if order and order["status"] in {"filled", "cancelled", "rejected"}:
                        return order
                sleep(1)
            raise TimeoutError("paper order did not reconcile within the qualification deadline")
        symbol = policy.allowed_instruments[0]
        try:
            quote = latest_quotes(broker.http, (symbol,))[symbol]
            now = clock()
            if not quote.fresh(now, policy.max_quote_age_seconds):
                raise ValueError("qualification requires a fresh broker quote")
            if (quote.ask-quote.bid)/quote.ask*10000 > policy.max_spread_bps:
                raise ValueError("qualification quote exceeds the spread limit")
            limit = quote.ask*(1+policy.slippage_bps/10000)
            budget = min(D("12"), policy.max_position_notional, D(book.get("cash"))/(1+policy.fee_bps/10000))
            qty = broker.round_quantity(symbol, budget/limit)
            stop = quote.bid*D("0.99")
            if qty < D(broker.asset(symbol)["min_order_size"]):
                raise ValueError("qualification amount is below the broker minimum")
            if (limit-stop)*qty + budget*policy.fee_bps/5000 > policy.max_loss_per_trade:
                raise ValueError("qualification would exceed the loss budget")
            entry = Intent(f"qualification-entry:{now}", policy.approved_strategies[0], symbol, "buy", qty, stop, now,
                           "broker_demo_validation", limit)
            book.reserve(entry, now)
            broker.submit(entry, quote)
            order = settle(entry.client_id)
            if not D(order["quantity"]):
                report["status"] = "entry_rejected" if order["status"] == "rejected" else "entry_unfilled"
                report["checks"]["ioc_terminal_state"] = order["status"] == "cancelled"
                report["broker_rejections"] = [e["payload"] for e in book.recent_events()
                                                if e["event"] == "order.broker_rejected"]
                save()
                return report
            report["checks"]["entry_fill_reconciled"] = True
            protection = broker.ensure_protection(clock())
            report["checks"]["native_stop_accepted"] = protection
            report["protection"] = book.get("protection")
            save()
            pos = book.positions().get(symbol)
            if pos:
                qty = broker.round_quantity(symbol, pos["quantity"])
                quote = latest_quotes(broker.http, (symbol,))[symbol]
                exit_intent = Intent("qualification-exit", entry.strategy, symbol, "sell", qty, 0, clock(),
                                     "broker_demo_validation_exit")
                if not broker.prepare(exit_intent, clock()):
                    raise ValueError("stop cancellation or exit inventory is uncertain")
                report["checks"]["stop_cancel_confirmed"] = not broker.active_protection(symbol)
                book.reserve(exit_intent, clock())
                broker.submit(exit_intent, quote)
                settle(exit_intent.client_id)
            broker.reconcile(clock(), protect=False)
            report["checks"]["flat_after_exit"] = not book.positions() and not broker.active_protection()
            report["checks"]["no_duplicate_client_ids"] = len(book.orders(include_protection=True)) == len({o["client_id"] for o in book.orders(include_protection=True)})
            report["status"] = "passed" if all(report["checks"].values()) else "failed"
            report["completed_at"] = clock()
            report["accounting"] = book.get("accounting")
            report["net_trading_pnl_usd"] = str(D(book.get("cash"))-policy.starting_cash)
        except Exception as exc:
            reserved = any(o["created"] >= report["started_at"] for o in book.orders(include_protection=True))
            report["status"] = "needs_reconciliation" if reserved else "preflight_blocked"
            report["error_type"] = type(exc).__name__
            report["reason"] = str(exc)[:200] if isinstance(exc, (ValueError, TimeoutError)) else "inspect persisted broker events"
            # Do not guess whether an uncertain buy/stop/exit exists, resend it, or claim cleanup.
            report["positions_remaining"] = bool(book.positions())
            report["pending_orders_remaining"] = len(book.orders(pending_only=True, include_protection=True))
            save()
            raise
        save()
        return report
