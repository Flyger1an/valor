"""Read-only, fee-aware memory of executed outcomes. No strategy or risk authority."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json

from .contracts import decimal as D, encode, utc_timestamp


def attribution(trade, fills, orders):
    """Attribute a single non-overlapping, closed lot; never guess across inventory overlaps.

    Fill-price movement already contains spread/slippage. The residual contains charged or
    reserved fees and any unsold rounding dust. Net P&L always comes from the reconciled book.
    """
    lot = trade["lot_id"]
    buys = [f for f in fills if f["cid"] == lot and f["side"] == "buy"]
    interval = [f for f in fills if f["symbol"] == trade["instrument"]
                and trade["opened"] <= f["stamp"] <= trade["closed"]]
    sells = [f for f in interval if f["side"] == "sell"]
    if (not buys or not sells or any(f["cid"] != lot for f in interval if f["side"] == "buy")
            or any(orders[f["cid"]]["intent"]["strategy"] != trade["strategy"] for f in sells)):
        return {"attribution": "unavailable_or_overlapping_inventory"}
    bq = sum((D(f["qty"]) for f in buys), D(0))
    bv = sum((D(f["qty"])*D(f["price"]) for f in buys), D(0))
    sq = sum((D(f["qty"]) for f in sells), D(0))
    sv = sum((D(f["qty"])*D(f["price"]) for f in sells), D(0))
    entry = orders[lot]
    if (not 0 < sq <= bq or bq != D(entry["quantity"]) or bv != D(entry["value"])
            or any(f["stamp"] < trade["opened"] or f["stamp"] > trade["closed"] for f in buys)):
        return {"attribution": "incomplete_fill_evidence"}
    gross = bq*sv/sq-bv
    drag = gross-D(trade["realized_pnl"])
    if drag < 0:
        return {"attribution": "unsupported_fee_or_inventory_effect"}
    return {"attribution": "reconciled_fill_prices", "entry_notional_usd": str(bv),
            "gross_fill_price_pnl_usd": str(gross), "fee_and_quantity_effect_usd": str(drag),
            "fee_and_quantity_effect_bps": str(drag/bv*10000),
            "exit_reasons": sorted({orders[f["cid"]]["intent"]["reason"] for f in sells}),
            "fill_ids": sorted(f["id"] for f in buys+sells)}


def summarize(book):
    """Recompute from current projections so later fee postings revise memory, not raw facts."""
    orders = {o["client_id"]: {**o, "intent": json.loads(o["intent"])} for o in book.orders()}
    engineering = {cid for cid, o in orders.items()
                   if o["intent"]["reason"].startswith("broker_demo_validation")}
    trades = [t for t in book.closed_trades() if t.get("lot_id") not in engineering]
    fills = []
    if book.get("activity_accounting"):
        remote = {r["remote_id"]: r["client_id"] for r in book.db.execute("SELECT * FROM alpaca_receipts")}
        for row in book.db.execute("SELECT payload FROM broker_activities WHERE kind='FILL'"):
            f = json.loads(row[0])
            cid = remote.get(f["order_id"])
            if cid in orders:
                fills.append({**f, "cid": cid, "stamp": utc_timestamp(f["transaction_time"]),
                              "symbol": f["symbol"].replace("/", "-")})
    halts = [r[0] for r in book.db.execute("SELECT timestamp FROM events WHERE event='risk.halted'")]
    rows = []
    for t in trades:
        row = {k: t.get(k) for k in ("lot_id", "instrument", "strategy", "opened", "closed", "fees_provisional")}
        row["net_pnl_usd"] = t["realized_pnl"]
        row["attribution"] = "no_broker_fill_journal"
        if fills and t.get("lot_id") in orders:
            overlaps = any(other.get("lot_id") != t["lot_id"] and other.get("instrument") == t["instrument"]
                           and max(other["opened"], t["opened"]) <= min(other["closed"], t["closed"])
                           for other in trades)
            row.update({"attribution": "overlapping_closed_lots"} if overlaps else attribution(t, fills, orders))
        row["halt_recorded_during_trade"] = any(t.get("opened", t["closed"]) <= at <= t["closed"] for at in halts)
        rows.append(row)
    def totals(selected):
        known = [r for r in selected if r["attribution"] == "reconciled_fill_prices"]
        return {"trades": len(selected), "wins": sum(D(r["net_pnl_usd"]) > 0 for r in selected),
                "losses": sum(D(r["net_pnl_usd"]) < 0 for r in selected),
                "net_pnl_usd": str(sum((D(r["net_pnl_usd"]) for r in selected), D(0))),
                "provisional_trades": sum(bool(r["fees_provisional"]) for r in selected),
                "attributed_trades": len(known),
                "gross_fill_price_pnl_usd": str(sum((D(r["gross_fill_price_pnl_usd"]) for r in known), D(0))),
                "fee_and_quantity_effect_usd": str(sum((D(r["fee_and_quantity_effect_usd"]) for r in known), D(0))),
                "gross_gains_erased_by_costs": sum(D(r["gross_fill_price_pnl_usd"]) > 0 > D(r["net_pnl_usd"]) for r in known),
                "halts_during_trades": sum(r["halt_recorded_during_trade"] for r in selected)}
    groups = defaultdict(list)
    for row in rows:
        groups[(row["instrument"], row["strategy"])].append(row)
    report = {"version": 1, "mode": book.policy.mode, "policy_hash": book.policy.fingerprint,
              "engineering_excluded": True, "summary": totals(rows),
              "groups": [{"instrument": symbol, "strategy": strategy, **totals(selected)}
                         for (symbol, strategy), selected in sorted(groups.items())],
              "exit_counts": dict(Counter(reason for row in rows for reason in row.get("exit_reasons", []))),
              "trades": rows,
              "interpretation": "Executed outcomes, not proof of alpha. Gross uses fill prices (spread/slippage already embedded). Fee/quantity effect includes reserves and rounding dust; unknown attributions excluded from gross/cost totals. Halts during trades are associations, not causal proof. All net losses count."}
    report["evidence_hash"] = hashlib.sha256(encode(report).encode()).hexdigest()
    return report


def compact(report):
    """Bounded model memory; complete precision and every trade remain in the archived report."""
    result = {k: report[k] for k in ("version", "mode", "evidence_hash", "summary", "engineering_excluded", "exit_counts")}
    group_keys = ("instrument", "strategy", "trades", "wins", "net_pnl_usd", "provisional_trades",
                  "attributed_trades", "gross_fill_price_pnl_usd", "fee_and_quantity_effect_usd")
    result["groups"] = [{k: g[k] for k in group_keys} for g in report["groups"][-6:]]
    result["groups_omitted"] = max(0, len(report["groups"])-6)
    keys = ("lot_id", "instrument", "net_pnl_usd", "fees_provisional", "attribution",
            "gross_fill_price_pnl_usd", "fee_and_quantity_effect_usd", "exit_reasons", "halt_recorded_during_trade")
    result["recent"] = [{k: t[k] for k in keys if k in t} for t in report["trades"][-3:]]
    # Exact arithmetic is retained in the full report. Six decimal places are ample for prompt USD.
    def rounded(value):
        if isinstance(value, dict):
            return {k: str(D(v).quantize(D('.000001'))) if k.endswith('_usd') else rounded(v) for k,v in value.items()}
        return [rounded(v) for v in value] if isinstance(value, list) else value
    return rounded(result)
