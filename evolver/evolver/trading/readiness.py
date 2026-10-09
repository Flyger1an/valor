"""Evidence counters, never an automatic live-activation switch."""
import datetime as dt
import json

from .contracts import decimal as D


def protection_current(book, now):
    """Assess retained receipts without treating an old complete flag as live evidence.

    This is a reporting gate only. It performs no broker calls, cancellations,
    submissions, or recovery; a failed check means coverage is not verified.
    """
    value = book.get("protection", {})
    if book.policy.mode not in {"demo", "live"} or value.get("complete") is not True:
        return False
    try:
        for stamp in (value.get("checked_at"), book.get("broker_reconciled_at")):
            if not 0 <= D(now) - D(stamp) <= book.policy.max_quote_age_seconds:
                return False
        positions = book.positions()
        coverage = value.get("positions", {})
        if set(positions) != set(coverage):
            return False
        active = {o["client_id"]: o for o in book.orders(pending_only=True, include_protection=True)
                  if o["purpose"] == "protection"}
        covered = set()
        for symbol, position in positions.items():
            proof = coverage[symbol]
            order = active.get(proof.get("client_id"))
            if proof.get("status") != "broker_held" or not order or order["status"] not in {"open", "partial"}:
                return False
            intent = json.loads(order["intent"])
            remaining = D(intent["quantity"]) - D(order["quantity"])
            residual = D(proof.get("residual_quantity", "0"))
            increment = D(book.get("asset_increments", {})[symbol])
            if (intent["instrument"] != symbol or intent["side"] != "sell" or remaining <= 0
                    or remaining != D(proof["quantity"]) or not 0 <= residual < increment
                    or remaining + residual != D(position["quantity"])):
                return False
            covered.add(order["client_id"])
        return covered == set(active)
    except (KeyError, TypeError, ValueError):
        return False


def assess(book, now):
    mode = book.policy.mode
    strategy_entries = {o["client_id"]: o for o in book.orders()
                        if (i := json.loads(o["intent"]))["side"] == "buy"
                        and not i["reason"].startswith("broker_demo_validation") and D(o["quantity"]) > 0}
    days = {dt.datetime.fromtimestamp(o["created"], dt.timezone.utc).date().isoformat()
            for o in strategy_entries.values()}
    closes = [c for c in book.closed_trades() if mode != "paper" and c.get("lot_id") in strategy_entries]
    accounting = book.get("accounting", {})
    fee_days = []
    if book.get("activity_accounting"):
        fee_days = sorted({json.loads(r[0])["date"][:10] for r in book.db.execute(
            "SELECT payload FROM broker_activities WHERE kind IN ('CFEE','FEE')")})
    qualification = book.get("demo_qualification", {})
    gates = {
        "authenticated_broker_demo": mode == "demo" and bool(book.get("broker_admitted_at")),
        "broker_order_drill": qualification.get("status") == "passed",
        "ten_active_strategy_sessions": len(days) >= 10,
        "thirty_strategy_round_trips": len(closes) >= 30,
        "three_fee_posting_days": len(fee_days) >= 3,
        "accounting_current": bool(accounting) and accounting.get("balance_match") == "confirmed"
                              and not accounting.get("fees_provisional") and not accounting.get("order_activity_lag"),
        "broker_protection_current": protection_current(book, now),
        "news_current": not book.get("news", {}).get("required") or not book.get("news", {}).get("entry_blocked", True),
        "no_unresolved_incident": not book.get("halt") and not book.get("entry_pause")
                                 and not book.orders(pending_only=True),
        "profitable_forward_account_sample": bool(closes) and sum((D(c["realized_pnl"]) for c in closes), D(0)) > 0,
    }
    return {"automatic_live_activation": False, "status": "collecting_evidence", "checked_at": now,
            "active_strategy_sessions": len(days), "strategy_round_trips": len(closes),
            "fee_posting_days": fee_days, "gates": gates,
            "remaining_gates": [k for k, ok in gates.items() if not ok],
            "additional_reviews_required": ["portfolio-faithful strategy validation", "independent outage alarm and restore evidence",
                                             "state-specific live eligibility", "owner live activation"],
            "engineering_drill": qualification, "sample_size_is_not_profitability_proof": True}
