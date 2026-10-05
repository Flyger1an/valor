"""Replayable broker activity journal and fee accruals for a dedicated cash account.

Raw broker facts are immutable. Late fees rebuild lots and closed-trade results; they
are never charged to whichever position happens to be open when the fee arrives.
Daily fees lacking order IDs are explicitly allocated pro rata, not called exact
per-order attribution. Account totals always use the original broker amounts.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import defaultdict
from decimal import ROUND_UP

from .contracts import Intent, TERMINAL, decimal as D, encode, utc_timestamp


class AccountingError(ValueError):
    pass


def day_at(stamp):
    return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).date().isoformat()


class ActivityJournal:
    def __init__(self, book):
        self.book = book
        book.db.executescript("""
            CREATE TABLE IF NOT EXISTS broker_activities(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, seen_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS broker_fee_allocations(
                activity_id TEXT NOT NULL, fill_id TEXT NOT NULL, amount TEXT NOT NULL,
                currency TEXT NOT NULL, method TEXT NOT NULL, PRIMARY KEY(activity_id,fill_id));
            CREATE TABLE IF NOT EXISTS broker_closed_trades(
                lot_id TEXT PRIMARY KEY, instrument TEXT NOT NULL, strategy TEXT NOT NULL,
                opened REAL NOT NULL, closed REAL NOT NULL, realized_pnl TEXT NOT NULL,
                fees_provisional INTEGER NOT NULL);
        """)
        with book.db:
            book.set("activity_accounting", True)

    def ingest(self, activities, now):
        if not isinstance(activities, list):
            raise AccountingError("activity response must be a list")
        with self.book.db:
            for item in activities:
                if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                        or not item["id"] or not isinstance(item.get("activity_type"), str)):
                    raise AccountingError("invalid activity identity")
                payload = encode(item)
                existing = self.book.db.execute("SELECT payload FROM broker_activities WHERE id=?", (item["id"],)).fetchone()
                if existing and existing[0] != payload:
                    raise AccountingError("broker changed an existing activity; explicit correction review required")
                if not existing:
                    self.book.db.execute("INSERT INTO broker_activities VALUES (?,?,?,?)",
                                         (item["id"], item["activity_type"], payload, now))

    def rebuild(self, now):
        book, policy = self.book, self.book.policy
        orders = {o["client_id"]: o for o in book.orders(include_protection=True)}
        receipts = {r["remote_id"]: dict(r) for r in book.db.execute(
            "SELECT * FROM alpaca_receipts WHERE remote_id IS NOT NULL")}
        ignored = set(book.get("opening_activity_ids", []))
        activities = [json.loads(r[0]) for r in book.db.execute("SELECT payload FROM broker_activities ORDER BY id")]
        fills, fees, flows = [], [], []
        for item in activities:
            if item["id"] in ignored:
                continue
            kind = item["activity_type"]
            if kind == "FILL":
                receipt = receipts.get(item.get("order_id"))
                if receipt is None or receipt["client_id"] not in orders:
                    raise AccountingError("activity belongs to an unknown order")
                local = orders[receipt["client_id"]]
                intent = Intent(**json.loads(local["intent"]))
                stamp = utc_timestamp(item["transaction_time"])
                symbol = normalize_symbol(item["symbol"], policy)
                qty, price = D(item["qty"]), D(item["price"])
                if (item["side"] != intent.side or symbol != intent.instrument or qty <= 0 or price <= 0
                        or not local["created"] - 5 <= stamp <= now):
                    raise AccountingError("fill does not match the reserved order")
                if intent.side == "buy" and price > intent.limit_price:
                    raise AccountingError("entry filled above its maximum price")
                fills.append({"id": item["id"], "order_id": item["order_id"], "cid": intent.client_id,
                              "intent": intent, "stamp": stamp, "day": day_at(stamp), "symbol": symbol,
                              "qty": qty, "value": qty * price, "side": intent.side})
            elif kind in {"CFEE", "FEE"}:
                if item.get("status", "executed") != "executed":
                    raise AccountingError("fee is not an executed activity")
                fees.append(item)
            elif kind in {"CSD", "CSW"}:
                amount = D(item["net_amount"])
                if (kind == "CSD" and amount < 0) or (kind == "CSW" and amount > 0):
                    raise AccountingError("cash flow sign mismatch")
                flows.append(amount)
            else:
                raise AccountingError("unsupported account activity requires operator review")
        fills.sort(key=lambda f: (f["stamp"], f["id"]))
        by_cid = defaultdict(list)
        for f in fills:
            by_cid[f["cid"]].append(f)
        for cid, group in by_cid.items():
            if sum((f["qty"] for f in group), D(0)) > Intent(**json.loads(orders[cid]["intent"])).quantity:
                raise AccountingError("cumulative fills exceed the reserved quantity")

        # A fee activity may represent a day or an individual order. Preserve that distinction.
        actual_quote, actual_base = defaultdict(lambda: D(0)), defaultdict(lambda: D(0))
        covered, allocations = set(), []
        for fee in fees:
            base = fee["activity_type"] == "CFEE"
            amount = -D(fee["qty"] if base else fee["net_amount"])
            if amount < 0:
                raise AccountingError("fee rebates/corrections require explicit review")
            symbol = normalize_symbol(fee["symbol"], policy) if fee.get("symbol") else None
            if base and (not symbol or D(fee.get("net_amount", 0)) != 0):
                raise AccountingError("invalid base-asset fee")
            fee_day = dt.date.fromisoformat(fee["date"][:10]).isoformat()
            candidates = [f for f in fills if (not symbol or f["symbol"] == symbol)
                          and (f["side"] == "buy" if base else f["side"] == "sell")
                          and (f["order_id"] == fee["order_id"] if fee.get("order_id") else f["day"] == fee_day)]
            if not candidates:
                raise AccountingError("fee cannot be attributed to a known fill; new entries remain blocked")
            weights = [f["qty"] if base else f["value"] for f in candidates]
            total, used = sum(weights, D(0)), D(0)
            for index, (fill, weight) in enumerate(zip(candidates, weights)):
                part = amount-used if index == len(candidates)-1 else amount*weight/total
                used += part
                (actual_base if base else actual_quote)[fill["id"]] += part
                covered.add(fill["id"])
                allocations.append((fee["id"], fill["id"], str(part), symbol if base else "USD",
                                    "order_id" if fee.get("order_id") else "daily_pro_rata"))

        rate = policy.fee_bps / 10000
        increments = book.get("asset_increments", {})
        quote_fee, base_fee, reserves = {}, {}, []
        for f in fills:
            expected = (f["qty"] if f["side"] == "buy" else f["value"]) * rate
            unit = D(increments.get(f["symbol"], "0.000000001")) if f["side"] == "buy" else D("0.01")
            expected = (expected/unit).to_integral_value(rounding=ROUND_UP)*unit
            confirmed = (actual_base if f["side"] == "buy" else actual_quote)[f["id"]]
            # Until a reported fee exists, keep the whole conservative accrual. Same-day partial
            # fee postings cannot prematurely release the accrual for later fills that day.
            effective = max(expected, confirmed) if f["id"] not in covered or f["day"] == day_at(now) else confirmed
            if confirmed > expected + D("0.00000001"):
                raise AccountingError("broker fees exceed the approved fee assumption")
            base_fee[f["id"]] = effective if f["side"] == "buy" else D(0)
            quote_fee[f["id"]] = effective if f["side"] == "sell" else D(0)
            if effective > confirmed:
                reserves.append({"fill_id": f["id"], "instrument": f["symbol"],
                                 "currency": f["symbol"] if f["side"] == "buy" else "USD",
                                 "amount": str(effective-confirmed), "trade_day": f["day"]})

        cash = policy.starting_cash + sum(flows, D(0))
        actual_cash, actual_qty = cash, defaultdict(lambda: D(0))
        lots, closed, realized = {}, {}, D(0)
        for f in fills:
            fid, intent = f["id"], f["intent"]
            sign = 1 if f["side"] == "buy" else -1
            actual_cash -= sign*f["value"] + actual_quote[fid]
            actual_qty[f["symbol"]] += sign*f["qty"] - actual_base[fid]
            cash -= sign*f["value"] + quote_fee[fid]
            if f["side"] == "buy":
                lot = lots.setdefault(f["cid"], {"id": f["cid"], "instrument": f["symbol"],
                    "strategy": intent.strategy, "quantity": D(0), "cost_basis": D(0),
                    "stop_price": intent.stop_price, "opened": f["stamp"], "realized_pnl": D(0),
                    "fees_provisional": False})
                lot["quantity"] += f["qty"]-base_fee[fid]
                lot["cost_basis"] += f["value"]
                lot["fees_provisional"] |= base_fee[fid] > actual_base[fid]
                closed.pop(f["cid"], None)
                continue
            remaining = f["qty"]
            for lot in lots.values():
                if lot["instrument"] != f["symbol"] or lot["quantity"] <= 0 or remaining <= 0:
                    continue
                qty = min(remaining, lot["quantity"])
                removed_basis = lot["cost_basis"] * qty / lot["quantity"]
                profit = (f["value"] - quote_fee[fid])*qty/f["qty"] - removed_basis
                lot["quantity"] -= qty
                lot["cost_basis"] -= removed_basis
                lot["realized_pnl"] += profit
                lot["fees_provisional"] |= quote_fee[fid] > actual_quote[fid]
                realized += profit
                remaining -= qty
                if lot["quantity"] == 0 or (lot["quantity"] < D(increments.get(f["symbol"], "0"))
                                             and lot["cost_basis"] < D("0.01")):
                    closed[lot["id"]] = {**lot, "closed": f["stamp"]}
            if remaining > D("0.000000000000000001"):
                raise AccountingError("sell exceeds fee-adjusted inventory")

        positions = {}
        for lot in lots.values():
            if lot["quantity"] <= 0:
                continue
            existing = positions.get(lot["instrument"])
            if existing and existing["strategy"] != lot["strategy"]:
                raise AccountingError("different strategies own the same asset")
            if existing:
                existing["quantity"] += lot["quantity"]
                existing["cost_basis"] += lot["cost_basis"]
                existing["stop_price"] = max(existing["stop_price"], lot["stop_price"])
            else:
                positions[lot["instrument"]] = dict(lot)
        dust = {s: p for s, p in positions.items() if p["quantity"] < D(increments.get(s, "0")) and p["cost_basis"] < D("0.01")}
        positions = {s: p for s, p in positions.items() if s not in dust}
        if cash < 0 or any(q < -D("0.000000000000000001") for q in actual_qty.values()):
            raise AccountingError("journal would require borrowing")

        lag = []
        order_updates = []
        for cid, order in orders.items():
            group = by_cid[cid]
            qty, value = sum((f["qty"] for f in group), D(0)), sum((f["value"] for f in group), D(0))
            row = book.db.execute("SELECT report FROM alpaca_receipts WHERE client_id=?", (cid,)).fetchone()
            report = json.loads(row[0]) if row and row[0] else None
            state = order["status"]
            if report:
                if qty != D(report["filled_quantity"]):
                    lag.append(cid)
                    state = "partial" if qty else "open"
                else:
                    state = report["status"]
            else:
                lag.append(cid)
            order_updates.append((state, str(qty), str(value),
                                  str(sum((actual_quote[f["id"]] for f in group), D(0))),
                                  str(sum((actual_base[f["id"]] for f in group), D(0))), cid))
        accounting = {"source": "alpaca_activity_journal", "activities": len(activities),
                      "fills": len(fills), "fee_activities": len(fees), "fees_provisional": bool(reserves),
                      "fee_reserves": reserves, "order_activity_lag": lag,
                      "actual_quote_fees": str(sum(actual_quote.values(), D(0))),
                      "actual_base_fees": {s: str(sum((actual_base[f["id"]] for f in fills if f["symbol"] == s), D(0)))
                                           for s in policy.allowed_instruments},
                      "net_cash_flows": str(sum(flows, D(0))), "cash_flow_count": len(flows),
                      "daily_fee_allocation": "proportional to filled quantity for base fees; proceeds for USD fees",
                      "last_ingested_at": now}
        digest = hashlib.sha256(encode({"positions": positions, "dust": dust, "cash": cash, "closed": closed,
                                        "orders": order_updates, "allocations": allocations}).encode()).hexdigest()
        with book.db:
            if digest != book.get("broker_projection_hash"):
                old_closed = {r["lot_id"]: dict(r) for r in book.db.execute("SELECT * FROM broker_closed_trades")}
                book.db.execute("DELETE FROM positions")
                for s, p in positions.items():
                    book.db.execute("INSERT INTO positions VALUES (?,?,?,?,?,?)", (s, p["strategy"],
                        str(p["quantity"]), str(p["cost_basis"]), str(p["stop_price"]), p["opened"]))
                book.db.executemany("UPDATE orders SET status=?,quantity=?,value=?,fees=?,base_fees=? WHERE client_id=?", order_updates)
                book.db.execute("DELETE FROM broker_fee_allocations")
                book.db.executemany("INSERT INTO broker_fee_allocations VALUES (?,?,?,?,?)", allocations)
                book.db.execute("DELETE FROM broker_closed_trades")
                for cid, lot in closed.items():
                    book.db.execute("INSERT INTO broker_closed_trades VALUES (?,?,?,?,?,?,?)", (cid,
                        lot["instrument"], lot["strategy"], lot["opened"], lot["closed"],
                        str(lot["realized_pnl"]), int(lot["fees_provisional"])))
                    old = old_closed.get(cid)
                    if old is None or D(old["realized_pnl"]) != lot["realized_pnl"] or bool(old["fees_provisional"]) != lot["fees_provisional"]:
                        book.event(now, "trade.adjusted" if old else "trade.closed", {"lot_id": cid,
                            "instrument": lot["instrument"], "strategy": lot["strategy"],
                            "opened": lot["opened"], "realized_pnl": str(lot["realized_pnl"]),
                            "fees_provisional": lot["fees_provisional"]})
                for cid in set(old_closed)-set(closed):
                    book.event(now, "trade.reopened_by_fee_adjustment", {"lot_id": cid})
                book.set("cash", str(cash))
                book.set("dust", dust)
                book.set("realized_pnl", str(realized))
                book.set("broker_projection_hash", digest)
                book.event(now, "accounting.projected", {"hash": digest, "fills": len(fills), "fee_activities": len(fees)})
            if fills and policy.mode == "live" and policy.study_start_event == "first_live_fill" and book.get("study_started_at") is None:
                book.set("study_started_at", fills[0]["stamp"])
                book.event(now, "study.started", {"mode": "live", "first_fill_timestamp": fills[0]["stamp"]})
            book.set("accounting", accounting)
        return {"cash": actual_cash, "positions": dict(actual_qty), "accounting": accounting}


def normalize_symbol(value, policy):
    symbol = value.replace("/", "-")
    if "-" not in symbol and symbol.endswith("USD"):
        symbol = symbol[:-3] + "-USD"
    if symbol not in policy.allowed_instruments:
        raise AccountingError("unapproved asset in account activity")
    return symbol
