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


def activity_digest(item):
    return hashlib.sha256(encode(item).encode()).hexdigest()


def cash_amount(item):
    """A USD cash fact must not also move inventory or represent a pending entry."""
    if (item.get("status", "executed") != "executed"
            or item.get("currency", "USD") != "USD"
            or D(item.get("qty", 0)) != 0
            or item.get("symbol") not in (None, "", "USD")):
        raise AccountingError("invalid USD cash activity")
    return D(item["net_amount"])


def fee_components(fee, policy):
    """Infer denomination from mutually exclusive cash/quantity facts, not CFEE alone.

    These are Trading API activity records, not the different Broker SSE schema.
    USD-pair sell fees debit net_amount; buy fees debit the received base asset.
    Conflicting denominations, rebates and unsupported shapes require review.
    """
    net = D(fee["net_amount"])
    qty = D(fee.get("qty", 0))
    raw_symbol = fee.get("symbol")
    symbol = normalize_symbol(raw_symbol, policy) if raw_symbol not in (None, "", "USD") else None
    base = fee["activity_type"] == "CFEE" and qty < 0 and net == 0
    if base:
        # Trading API may label the account currency USD even when qty debits
        # crypto. The approved USD symbol, negative qty and zero cash movement
        # establish denomination; foreign account/asset currencies still fail.
        if not symbol or fee.get("currency", symbol.split("-")[0]) not in {"USD", symbol.split("-")[0]}:
            raise AccountingError("invalid base-asset fee denomination")
        return True, -qty, symbol
    if net < 0 and qty == 0 and fee.get("currency", "USD") == "USD":
        return False, -net, symbol
    raise AccountingError("ambiguous fee denomination or rebate requires review")


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
            CREATE TABLE IF NOT EXISTS broker_cash_journal_reviews(
                activity_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS broker_fee_reviews(
                activity_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)
        with book.db:
            book.set("activity_accounting", True)

    def review_cash_journal(self, activity_id, classification, evidence_sha256, reviewed_by, now):
        """Append a verified operator decision; never infer funding from amount or text.

        Call only after inspecting private admission and broker evidence. This method
        does not fetch evidence, reconcile balances, alter raw facts or clear a halt.
        """
        if self.book.policy.mode != "demo":
            raise AccountingError("cash journal review is limited to demo accounting")
        row = self.book.db.execute("SELECT payload FROM broker_activities WHERE id=?", (activity_id,)).fetchone()
        if not row:
            raise AccountingError("review requires an ingested broker activity")
        item = json.loads(row[0])
        if item["activity_type"] != "JNLC" or classification not in {"opening_capital", "external_funding"}:
            raise AccountingError("unsupported cash journal classification")
        amount = cash_amount(item)
        admission = self.book.get("broker_admitted_at")
        if (not admission or not isinstance(reviewed_by, str) or not reviewed_by.strip()
                or not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64
                or any(c not in "0123456789abcdef" for c in evidence_sha256)
                or D(now) < D(admission)):
            raise AccountingError("verified admission evidence and reviewer required")
        if classification == "opening_capital" and (amount <= 0 or amount > self.book.policy.starting_cash):
            raise AccountingError("opening journal exceeds opening capital")
        decision = {"activity_sha256": activity_digest(item), "classification": classification,
                    "evidence_sha256": evidence_sha256, "reviewed_by": reviewed_by,
                    **self._admission_context()}
        payload = encode(decision)
        with self.book.db:
            prior = self.book.db.execute("SELECT payload FROM broker_cash_journal_reviews WHERE activity_id=?", (activity_id,)).fetchone()
            if prior and prior[0] != payload:
                raise AccountingError("cash journal review is immutable; conflicting decision")
            if not prior:
                self.book.db.execute("INSERT INTO broker_cash_journal_reviews VALUES (?,?)", (activity_id, payload))
                self.book.event(now, "accounting.cash_journal_reviewed", {"activity_id": activity_id, **decision})

    def review_fee_allocation(self, activity_id, fill_ids, evidence_sha256, reviewed_by, now):
        """Bind a verified settlement-date fee to exact historical fills, never a position.

        The operator must establish attribution from complete broker records. This
        method records that decision; its evidence digest is not automated proof.
        """
        if self.book.policy.mode != "demo":
            raise AccountingError("fee review is limited to demo accounting")
        row = self.book.db.execute("SELECT payload FROM broker_activities WHERE id=?", (activity_id,)).fetchone()
        if not row:
            raise AccountingError("fee review requires an ingested activity")
        fee = json.loads(row[0])
        if fee.get("activity_type") not in {"CFEE", "FEE"} or fee.get("status", "executed") != "executed":
            raise AccountingError("fee review requires an executed fee")
        if (not isinstance(fill_ids, list) or not fill_ids or any(not isinstance(i, str) for i in fill_ids)
                or len(set(fill_ids)) != len(fill_ids)):
            raise AccountingError("fee review requires unique fill IDs")
        admission = self.book.get("broker_admitted_at")
        if (not admission or not isinstance(reviewed_by, str) or not reviewed_by.strip()
                or not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64
                or any(c not in "0123456789abcdef" for c in evidence_sha256)
                or D(now) < D(admission)):
            raise AccountingError("verified admission evidence and reviewer required")
        base, _, symbol = fee_components(fee, self.book.policy)
        hashes = {}
        for fid in sorted(fill_ids):
            row = self.book.db.execute("SELECT payload FROM broker_activities WHERE id=?", (fid,)).fetchone()
            if not row:
                raise AccountingError("fee review references an unknown fill")
            fill = json.loads(row[0])
            stamp = utc_timestamp(fill["transaction_time"])
            owned = self.book.db.execute("SELECT 1 FROM alpaca_receipts r JOIN orders o ON o.client_id=r.client_id WHERE r.remote_id=?",
                                         (fill.get("order_id"),)).fetchone()
            if (fill.get("activity_type") != "FILL" or not owned or stamp > now
                    or fill.get("side") != ("buy" if base else "sell")
                    or (symbol and normalize_symbol(fill["symbol"], self.book.policy) != symbol)
                    or (fee.get("order_id") and fee["order_id"] != fill.get("order_id"))
                    or day_at(stamp) > dt.date.fromisoformat(fee["date"][:10]).isoformat()
                    or (fee.get("created_at") and utc_timestamp(fee["created_at"]) < stamp)):
                raise AccountingError("fee review contradicts fill identity or chronology")
            hashes[fid] = activity_digest(fill)
        decision = {"activity_sha256": activity_digest(fee), "fill_sha256": hashes,
                    "evidence_sha256": evidence_sha256, "reviewed_by": reviewed_by,
                    **self._admission_context()}
        payload = encode(decision)
        with self.book.db:
            prior = self.book.db.execute("SELECT payload FROM broker_fee_reviews WHERE activity_id=?", (activity_id,)).fetchone()
            if prior and prior[0] != payload:
                raise AccountingError("fee review is immutable; conflicting decision")
            if not prior:
                self.book.db.execute("INSERT INTO broker_fee_reviews VALUES (?,?)", (activity_id, payload))
                self.book.event(now, "accounting.fee_allocation_reviewed", {"activity_id": activity_id, **decision})

    def _admission_context(self):
        rows = self.book.db.execute("SELECT timestamp,payload FROM events WHERE event='broker.admitted'").fetchall()
        admission = self.book.get("broker_admitted_at")
        if (len(rows) != 1 or rows[0][0] != admission
                or D(json.loads(rows[0][1]).get("starting_cash")) != self.book.policy.starting_cash):
            raise AccountingError("cash journal review requires intact opening admission evidence")
        return {"admitted_at": admission, "starting_cash": str(self.book.policy.starting_cash),
                "identity": self.book.get("identity"),
                "admission_sha256": hashlib.sha256(rows[0][1].encode()).hexdigest()}

    def _validate_review(self, item, review, now, event="accounting.cash_journal_reviewed"):
        if not isinstance(review, dict):
            raise AccountingError("cash journal requires verified funding classification")
        evidence, reviewer = review.get("evidence_sha256"), review.get("reviewed_by")
        if (review.get("activity_sha256") != activity_digest(item)
                or any(review.get(k) != v for k, v in self._admission_context().items())
                or not isinstance(evidence, str) or len(evidence) != 64
                or any(c not in "0123456789abcdef" for c in evidence)
                or not isinstance(reviewer, str) or not reviewer.strip()):
            raise AccountingError("cash journal review evidence is missing or stale")
        decisions = [(r[0], json.loads(r[1])) for r in self.book.db.execute(
            "SELECT timestamp,payload FROM events WHERE event=?", (event,))]
        matching = [(stamp, payload) for stamp, payload in decisions if payload.get("activity_id") == item["id"]]
        if (len(matching) != 1 or matching[0][1] != {"activity_id": item["id"], **review}
                or not D(review["admitted_at"]) <= D(matching[0][0]) <= D(now)):
            raise AccountingError("cash journal review audit decision is missing or inconsistent")

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

    def rebuild(self, now, *, persist=True):
        try:
            return self._rebuild(now, persist=persist)
        except AccountingError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError) as exc:
            raise AccountingError("invalid broker activity schema; explicit review required") from exc

    def _rebuild(self, now, *, persist):
        book, policy = self.book, self.book.policy
        orders = {o["client_id"]: o for o in book.orders(include_protection=True)}
        receipts = {r["remote_id"]: dict(r) for r in book.db.execute(
            "SELECT * FROM alpaca_receipts WHERE remote_id IS NOT NULL")}
        ignored = set(book.get("opening_activity_ids", []))
        activities = [json.loads(r[0]) for r in book.db.execute("SELECT payload FROM broker_activities ORDER BY id")]
        reviews = {r[0]: json.loads(r[1]) for r in book.db.execute("SELECT activity_id,payload FROM broker_cash_journal_reviews")}
        fee_reviews = {r[0]: json.loads(r[1]) for r in book.db.execute("SELECT activity_id,payload FROM broker_fee_reviews")}
        raw_by_id = {a["id"]: a for a in activities}
        opening_total = D(0)
        opening_journals = []
        fills, fees, flows = [], [], []
        for item in activities:
            if item["id"] in ignored:
                if item["activity_type"] != "CSD" or cash_amount(item) < 0:
                    raise AccountingError("invalid legacy opening activity")
                opening_total += cash_amount(item)
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
                amount = cash_amount(item)
                if (kind == "CSD" and amount < 0) or (kind == "CSW" and amount > 0):
                    raise AccountingError("cash flow sign mismatch")
                flows.append(amount)
            elif kind == "JNLC":
                amount = cash_amount(item)
                review = reviews.get(item["id"])
                self._validate_review(item, review, now)
                if review["classification"] == "opening_capital" and amount > 0:
                    opening_total += amount
                    opening_journals.append(item["id"])
                elif review["classification"] == "external_funding":
                    flows.append(amount)
                else:
                    raise AccountingError("invalid cash journal classification")
            else:
                raise AccountingError("unsupported account activity requires operator review")
        if opening_total > policy.starting_cash:
            raise AccountingError("opening activities double count starting capital")
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
            base, amount, symbol = fee_components(fee, policy)
            fee_day = dt.date.fromisoformat(fee["date"][:10]).isoformat()
            eligible = [f for f in fills if (not symbol or f["symbol"] == symbol)
                          and (f["side"] == "buy" if base else f["side"] == "sell")
                          and (not fee.get("order_id") or f["order_id"] == fee["order_id"])]
            method = "order_id" if fee.get("order_id") else "daily_pro_rata"
            if fee["id"] in fee_reviews:
                review = fee_reviews[fee["id"]]
                self._validate_review(fee, review, now, "accounting.fee_allocation_reviewed")
                hashes = review.get("fill_sha256")
                if (not isinstance(hashes, dict) or not hashes
                        or any(fid not in raw_by_id or activity_digest(raw_by_id[fid]) != digest
                               for fid, digest in hashes.items())):
                    raise AccountingError("reviewed fee fill evidence is missing or stale")
                candidates = [f for f in eligible if f["id"] in hashes]
                if (len(candidates) != len(hashes) or any(f["day"] > fee_day or
                        (fee.get("created_at") and utc_timestamp(fee["created_at"]) < f["stamp"])
                        for f in candidates)):
                    raise AccountingError("reviewed fee contradicts fill identity or chronology")
                method = "reviewed_fill_set_pro_rata"
            else:
                candidates = [f for f in eligible if fee.get("order_id") or f["day"] == fee_day]
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
                                    method))

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
                      "opening_cash_journal_ids": opening_journals,
                      "reviewed_fee_activity_ids": sorted(fee_reviews),
                      "daily_fee_allocation": "proportional to filled quantity for base fees; proceeds for USD fees",
                      "last_ingested_at": now}
        digest = hashlib.sha256(encode({"positions": positions, "dust": dust, "cash": cash, "closed": closed,
                                        "orders": order_updates, "allocations": allocations}).encode()).hexdigest()
        projection = {"cash": actual_cash, "positions": dict(actual_qty), "accounting": accounting,
                      "effective_cash": cash,
                      "effective_positions": {s: p["quantity"] for s, p in {**positions, **dust}.items()}}
        if not persist:
            return projection
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
        return projection


def normalize_symbol(value, policy):
    symbol = value.replace("/", "-")
    if "-" not in symbol and symbol.endswith("USD"):
        symbol = symbol[:-3] + "-USD"
    if symbol not in policy.allowed_instruments:
        raise AccountingError("unapproved asset in account activity")
    return symbol
