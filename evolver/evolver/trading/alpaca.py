"""Alpaca cash crypto adapter. Fixed endpoints, durable client IDs, broker-confirmed net quantities.

Requires a dedicated account: outside trades/transfers cause reconciliation to stop entries.
Paper and live keys are separate; live construction is blocked pending fee and broker-demo validation.
This module is never imported by the legacy research or simulation loops.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_DOWN, ROUND_UP

from .contracts import Intent, OrderReport, Policy, Quote, decimal, encode, utc_timestamp


class BrokerError(RuntimeError):
    pass


class BrokerHTTPError(BrokerError):
    def __init__(self, method, status, provider_code=None, reason=None):
        super().__init__(f"Alpaca {method} failed with HTTP {status}")
        self.status = status
        self.provider_code, self.reason = provider_code, reason


class AlpacaHTTP:
    def __init__(self, mode: str, key: str, secret: str):
        if mode not in {"demo", "live"} or not key or not secret:
            raise BrokerError("Alpaca needs separate paper or live credentials")
        self.base = "https://paper-api.alpaca.markets" if mode == "demo" else "https://api.alpaca.markets"
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Content-Type": "application/json"}

    def request(self, method, path, body=None, params=None, *, data=False):
        base = "https://data.alpaca.markets" if data else self.base
        url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, data=encode(body).encode() if body is not None else None,
                                     headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return None
            # Keep only a bounded, redacted rejection reason, never headers or a raw body.
            try:
                error = json.loads(exc.read(4096))
                code = str(error.get("code", ""))[:24]
                reason = re.sub(r"[A-Za-z0-9_+/=-]{16,}", "[redacted]", str(error.get("message", "")))[:300]
            except Exception:
                code, reason = None, None
            raise BrokerHTTPError(method, exc.code, code, reason) from None


class AlpacaBroker:
    """Paper-broker execution with durable activities and broker-held protection.

    Live remains a separate, unconditional activation gate. Neither a successful
    demo nor an AI verdict can remove it.
    """
    def __init__(self, policy: Policy, http, account_id: str, *, clock=time.time):
        if policy.mode not in {"demo", "live"} or policy.quote_currency != "USD":
            raise BrokerError("Alpaca adapter requires demo/live mode and USD")
        if not account_id:
            raise BrokerError("an explicit account ID is required")
        if policy.mode == "live":
            raise BrokerError("live execution unavailable: authenticated demo, fee settlement, and outage evidence required")
        if getattr(http, "base", "https://paper-api.alpaca.markets") != "https://paper-api.alpaca.markets":
            raise BrokerError("demo must use the paper endpoint")
        self.mode, self.policy, self.http, self.account_id = policy.mode, policy, http, account_id
        self.identity = "alpaca:" + self.mode + ":" + hashlib.sha256(account_id.encode()).hexdigest()[:16]
        self.book, self.assets = None, {}
        self.clock = clock

    def bind(self, book):
        from .accounting import ActivityJournal
        self.book = book
        with book.db:
            book.db.execute("""CREATE TABLE IF NOT EXISTS alpaca_receipts(
                client_id TEXT PRIMARY KEY, baseline_cash TEXT NOT NULL,
                baseline_quantity TEXT NOT NULL, remote_id TEXT, report TEXT)""")
        self.journal = ActivityJournal(book)

    def _account_info(self):
        a = self.http.request("GET", "/v2/account")
        if (not a or a.get("id") != self.account_id or a.get("status") != "ACTIVE" or
                a.get("crypto_status") != "ACTIVE" or a.get("currency") != "USD" or
                any(a.get(k) for k in ("trading_blocked", "account_blocked", "trade_suspended_by_user"))):
            raise BrokerError("account identity, crypto eligibility, or trading status failed")
        return a

    def account(self):
        a = self._account_info()
        positions = self.http.request("GET", "/v2/positions")
        orders = self.http.request("GET", "/v2/orders", params={"status": "open", "limit": 500})
        if not isinstance(positions, list) or not isinstance(orders, list) or len(orders) >= 500:
            raise BrokerError("incomplete broker account snapshot")
        return {"cash": decimal(a["cash"]),
                "positions": {self._symbol(p["symbol"]): decimal(p["qty"]) for p in positions},
                "available": {self._symbol(p["symbol"]): decimal(p.get("qty_available", p["qty"])) for p in positions},
                "open_order_ids": [o["client_order_id"] for o in orders]}

    def _symbol(self, value):
        from .accounting import normalize_symbol
        return normalize_symbol(value, self.policy)

    def activities(self, now=None):
        # Re-scan from account admission, not just the last cursor: late-created fees may
        # carry an earlier trade date. Ninety days at six entries/day is bounded.
        params = {"direction": "asc", "page_size": 100}
        if self.book.get("activity_since"):
            params["after"] = self.book.get("activity_since")
            if now is not None and now-self.book.get("last_full_activity_scan", 0) < 3600:
                recent = dt.datetime.fromtimestamp(now-3*86400, dt.timezone.utc).date().isoformat()
                params["after"] = max(params["after"], recent)
        result, seen = [], set()
        for _ in range(100):
            page = self.http.request("GET", "/v2/account/activities", params=params)
            if not isinstance(page, list) or len(page) > 100:
                raise BrokerError("incomplete activity response")
            result.extend(page)
            if len(page) < 100:
                if now is not None and params.get("after") == self.book.get("activity_since"):
                    with self.book.db:
                        self.book.set("last_full_activity_scan", now)
                return result
            token = page[-1].get("id")
            if not token or token in seen:
                raise BrokerError("activity pagination did not advance")
            seen.add(token)
            params = {**params, "page_token": token}
        raise BrokerError("activity response exceeds validated study bounds")

    def initialize(self, now):
        if self.book.get("broker_admitted_at"):
            return
        if self.book.orders(include_protection=True):
            raise BrokerError("existing demo book requires an explicit accounting migration")
        account = self.account()
        if (account["positions"] or account["open_order_ids"]
                or abs(account["cash"]-self.policy.starting_cash) > decimal("0.00000001")):
            raise BrokerError("demo requires a fresh, flat account with exactly the configured starting cash")
        prior = self.activities()
        if any(a.get("activity_type") not in {"CSD"} for a in prior):
            raise BrokerError("dedicated demo account contains pre-existing trading activity")
        self.journal.ingest(prior, now)
        with self.book.db:
            self.book.set("opening_activity_ids", [a["id"] for a in prior])
            self.book.set("broker_admitted_at", now)
            self.book.set("activity_since", dt.datetime.fromtimestamp(now, dt.timezone.utc).date().isoformat())
            self.book.event(now, "broker.admitted", {"mode": self.mode, "starting_cash": str(account["cash"])})

    def _report(self, order):
        cid = order["client_order_id"]
        local = self.book.order(cid)
        receipt = self.book.db.execute("SELECT * FROM alpaca_receipts WHERE client_id=?", (cid,)).fetchone()
        if local is None or receipt is None:
            raise BrokerError("unknown order receipt")
        intent = Intent(**json.loads(local["intent"]))
        if (self._symbol(order["symbol"]) != intent.instrument or order["side"] != intent.side
                or decimal(order["qty"]) != intent.quantity
                or receipt["remote_id"] not in {None, order["id"]}):
            raise BrokerError("broker order identity mismatch")
        if local["purpose"] == "protection" and (order.get("type") != "stop_limit" or order.get("time_in_force") != "gtc"
                or decimal(order.get("stop_price", 0)) != intent.stop_price
                or decimal(order.get("limit_price", 0)) != intent.limit_price):
            raise BrokerError("broker protection differs from the reserved stop")
        status = order["status"]
        if status in {"canceled", "expired"}:
            state = "cancelled"
        elif status in {"new", "accepted", "pending_new", "pending_cancel", "accepted_for_bidding", "stopped"}:
            state = "open"
        elif status == "partially_filled":
            state = "partial"
        elif status in {"filled", "rejected"}:
            state = status
        else:
            raise BrokerError("unsupported order state; reconcile before proceeding")
        qty = decimal(order["filled_qty"])
        if qty > intent.quantity:
            raise BrokerError("broker filled more than the reserved quantity")
        report = OrderReport(cid, state, qty, qty*decimal(order.get("filled_avg_price") or 0), decimal(0))
        with self.book.db:
            self.book.db.execute("UPDATE alpaca_receipts SET remote_id=?,report=? WHERE client_id=?",
                                 (order["id"], encode(report.__dict__), cid))
        return report

    def lookup(self, client_id):
        saved = self.book.db.execute("SELECT remote_id,report FROM alpaca_receipts WHERE client_id=?", (client_id,)).fetchone()
        if saved and saved["remote_id"] is None and saved["report"] and json.loads(saved["report"])["status"] == "rejected":
            return OrderReport(**json.loads(saved["report"]))
        order = self.http.request("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_id})
        return self._report(order) if order else None

    def asset(self, instrument):
        if instrument not in self.assets:
            asset = self.http.request("GET", "/v2/assets/" + urllib.parse.quote(instrument.replace("-", "/"), safe=""))
            if not asset or not asset.get("tradable") or asset.get("class") != "crypto":
                raise BrokerError("instrument is not tradable crypto")
            if any(decimal(asset[k]) <= 0 for k in ("min_trade_increment", "min_order_size", "price_increment")):
                raise BrokerError("invalid broker increments")
            self.assets[instrument] = asset
            if self.book:
                with self.book.db:
                    self.book.set("asset_increments", {**self.book.get("asset_increments", {}),
                                                       instrument: asset["min_trade_increment"]})
        return self.assets[instrument]

    def round_quantity(self, symbol, quantity):
        step = decimal(self.asset(symbol)["min_trade_increment"])
        return (decimal(quantity)/step).to_integral_value(rounding=ROUND_DOWN)*step

    def _post(self, intent, body):
        # The durable reservation and receipt both precede POST. A failed POST is never retried.
        with self.book.db:
            self.book.db.execute("INSERT INTO alpaca_receipts VALUES (?,?,?,?,?)", (intent.client_id, "0", "0", None, None))
        try:
            return self._report(self.http.request("POST", "/v2/orders", body=body))
        except BrokerHTTPError as exc:
            if exc.status not in {400, 401, 403, 422}:
                raise  # rate limits/server failures remain uncertain; do not resend
            report = OrderReport(intent.client_id, "rejected", 0, 0, 0)
            with self.book.db:
                self.book.db.execute("UPDATE alpaca_receipts SET report=? WHERE client_id=?", (encode(report.__dict__), intent.client_id))
                self.book.event(self.clock(), "order.broker_rejected", {"client_id": intent.client_id,
                               "http_status": exc.status, "broker_code": exc.provider_code, "reason": exc.reason})
            return report

    def submit(self, intent: Intent, quote: Quote):
        asset = self.asset(intent.instrument)
        step, minimum, price_step = (decimal(asset[k]) for k in ("min_trade_increment", "min_order_size", "price_increment"))
        if intent.quantity < minimum or intent.quantity % step:
            raise BrokerError("intent violates broker lot size or minimum")
        reason = self.entry_minimum_reason(intent)
        if reason:
            raise BrokerError(reason)
        # Other positions may have protective orders, but no ordinary order may be in flight.
        allowed = {o["client_id"] for o in self.book.orders(pending_only=True, include_protection=True)
                   if o["purpose"] == "protection"}
        if set(self.account()["open_order_ids"]) - allowed:
            raise BrokerError("another order is pending")
        body = {"symbol": intent.instrument.replace("-", "/"), "qty": str(intent.quantity),
                "side": intent.side, "time_in_force": "ioc", "client_order_id": intent.client_id}
        if intent.side == "buy" or intent.limit_price:
            price = (intent.limit_price / price_step).to_integral_value(rounding=ROUND_DOWN) * price_step
            body.update(type="limit", limit_price=str(price))
        else:
            body.update(type="market")
        return self._post(intent, body)

    def entry_minimum_reason(self, intent):
        # Authenticated paper API returned 40310000 on 2026-10-04: cost basis >= $10.
        # min_order_size in /assets alone does not enforce this USD floor.
        return "broker_minimum_entry_notional" if intent.side == "buy" and intent.quantity*intent.limit_price < 10 else None

    def cancel(self, client_id):
        order = self.http.request("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_id})
        if not order:
            raise BrokerError("unknown cancellation target")
        report = self._report(order)
        if report.status in {"filled", "cancelled", "rejected"}:
            return report
        self.http.request("DELETE", "/v2/orders/" + urllib.parse.quote(order["id"], safe=""))
        return self.lookup(client_id)

    def apply_report(self, report, now):
        # The POST/GET response only establishes order state. Activity IDs establish cash
        # movements; no immediate balance inference or double application through Ledger.
        return self.reconcile(now)

    def reconcile(self, now, protect=True):
        from .accounting import AccountingError
        self.initialize(now)
        for order in self.book.orders(pending_only=True, include_protection=True):
            report = self.lookup(order["client_id"])
            if report is None:
                self.book.unknown(order["client_id"], now)
                with self.book.db:
                    self.book.set("entry_pause", "unresolved_order")
                if now-order["created"] > 30:
                    self.book.halt("unresolved_order", now)
                return False
        self.journal.ingest(self.activities(now), now)
        for symbol in self.policy.allowed_instruments:
            self.asset(symbol)
        now = max(now, self.clock())
        # Stage first: a failed balance check must not publish a guessed cash/lot
        # projection. Immutable raw activities are still retained for review.
        projection = self.journal.rebuild(now, persist=False)
        if projection["accounting"]["order_activity_lag"]:
            with self.book.db:
                self.book.set("entry_pause", "activity_lag")
            return False
        a = self.account()
        expected_open = set()
        for row in self.book.db.execute("SELECT client_id,report FROM alpaca_receipts WHERE report IS NOT NULL"):
            if json.loads(row["report"])["status"] in {"open", "partial"}:
                expected_open.add(row["client_id"])
        live_open = set(a["open_order_ids"])
        if live_open - expected_open:
            raise AccountingError("unknown or missing open orders", code="unknown_open_orders")
        if expected_open - live_open:
            # Race: an order looked up as open filled/cancelled before this account snapshot. Refresh
            # each by its durable client ID; only a still-open-yet-unlisted order is a real mismatch.
            for cid in expected_open - live_open:
                fresh = self.lookup(cid)
                if fresh is None or fresh.status in {"open", "partial"}:
                    raise AccountingError("unknown or missing open orders", code="unknown_open_orders")
            with self.book.db:
                self.book.set("entry_pause", "order_state_refresh")
                self.book.event(now, "broker.order_state_refreshed",
                                {"client_ids": sorted(expected_open - live_open)})
            return False  # re-ingest the new fills on the next cycle; never infer them here
        gross = {s: q for s, q in projection["positions"].items() if q}
        effective = projection["effective_positions"]
        actual = {s: q for s, q in a["positions"].items() if q}
        tolerance = decimal("0.00000001")
        cash_confirmed = abs(a["cash"]-projection["cash"]) <= tolerance
        cash_estimated = abs(a["cash"]-projection["effective_cash"]) <= tolerance
        cash_settlement_confirmed = False
        # The authenticated account endpoint rounds USD cash to cents while FILL carries
        # sub-cent execution values. Require an exact rounded match, not a loose $0.01 band.
        cents = decimal("0.01")
        if a["cash"] == a["cash"].quantize(cents):
            cash_confirmed |= a["cash"] == projection["cash"].quantize(cents)
            cash_estimated |= a["cash"] == projection["effective_cash"].quantize(cents)
            # Posted fees can expose per-fill USD cent rounding that differs from
            # rounding the final high-precision balance. Require this exact model
            # to match; never use it to excuse unposted fees or an arbitrary delta.
            cash_settlement_confirmed = (not projection["accounting"]["fees_provisional"]
                and a["cash"] == projection["settlement_cash"].quantize(cents))
        if self.book.get("cash_settlement_model") == "per_fill_cent":
            cash_confirmed = False  # an established model cannot switch to excuse a later discrepancy
        cash_estimated &= projection["accounting"]["fees_provisional"]
        # Owner decision 2026-10-08: the account endpoint shows cash in whole cents, and unposted
        # fees are reserved rounded UP per fill while the broker withholds the exact rate, so an
        # exact match is often impossible. Until fees post, accept cent-rounded broker cash between
        # "every reserved fee charged" and "no fee charged". Full-precision cash keeps the exact
        # rule above; a gap larger than the declared reserves still fails.
        if projection["accounting"].get("fee_reserves") and a["cash"] == a["cash"].quantize(cents):
            low, high = sorted((projection["effective_cash"], projection["cash"]))
            cash_estimated |= (low.quantize(cents, rounding=ROUND_DOWN) <= a["cash"]
                               <= high.quantize(cents, rounding=ROUND_UP))
        # A documented fee accrual can bridge an exactly matching withheld fee, but the
        # readiness report still marks it provisional. Arbitrary in-range deltas do not pass.
        if any(q < 0 for q in actual.values()):
            raise AccountingError("broker reports borrowed inventory")
        def quantities_match(expected):
            return all(abs(actual.get(s, decimal(0))-expected.get(s, decimal(0))) <=
                       decimal(self.asset(s)["min_trade_increment"]) for s in self.policy.allowed_instruments)
        qty_confirmed = quantities_match(gross)
        qty_estimated = quantities_match(effective)
        if not ((cash_confirmed or cash_estimated or cash_settlement_confirmed) and (qty_confirmed or qty_estimated)):
            raise AccountingError("broker balance differs from journal and explicit fee accruals", code="balance_mismatch",
                                  details={"broker_cash": str(a["cash"]), "journal_cash": str(projection["cash"]),
                                           "fee_accrual_cash": str(projection["effective_cash"]),
                                           "settlement_cash": str(projection["settlement_cash"]),
                                           "cash_matches": cash_confirmed or cash_estimated or cash_settlement_confirmed,
                                           "quantity_matches": qty_confirmed or qty_estimated})
        self.journal.rebuild(now)
        accounting = projection["accounting"]
        reason = "activity_lag" if accounting["order_activity_lag"] else ""
        if accounting["cash_flow_count"]:
            reason = "external_cash_flow_review_required"
        with self.book.db:
            self.book.set("entry_pause", reason)
            if cash_settlement_confirmed and not cash_confirmed:
                self.book.set("cash_settlement_model", "per_fill_cent")
            self.book.set("accounting", {**accounting, "balance_match": "confirmed" if (cash_confirmed or cash_settlement_confirmed) and qty_confirmed else "fee_accrual_bridge",
                           "cash_match_basis": "full_precision_or_final_cent_rounding" if cash_confirmed else
                                "per_fill_cent_settlement" if cash_settlement_confirmed else "provisional_fee_accrual",
                           "broker_cash": str(a["cash"]), "cent_settlement_cash": str(projection["settlement_cash"]),
                           "cash_precision_difference": str(a["cash"]-projection["cash"])})
        if reason:
            return False
        if protect and not self.ensure_protection(now):
            return False
        return True

    def active_protection(self, symbol=None):
        return [o for o in self.book.orders(pending_only=True, include_protection=True)
                if o["purpose"] == "protection" and (symbol is None or json.loads(o["intent"])["instrument"] == symbol)]

    def ensure_protection(self, now):
        coverage = {}
        for symbol, pos in self.book.positions().items():
            asset = self.asset(symbol)
            step, price_step = decimal(asset["min_trade_increment"]), decimal(asset["price_increment"])
            qty = (decimal(pos["quantity"])/step).to_integral_value(rounding=ROUND_DOWN)*step
            stop = (decimal(pos["stop_price"])/price_step).to_integral_value(rounding=ROUND_DOWN)*price_step
            limit = (stop*(1-self.policy.slippage_bps/10000)/price_step).to_integral_value(rounding=ROUND_DOWN)*price_step
            active = self.active_protection(symbol)
            earlier = [o for o in self.book.orders(include_protection=True) if o["purpose"] == "protection"
                       and json.loads(o["intent"])["instrument"] == symbol and o["created"] >= pos["opened"]]
            if not active and earlier and earlier[-1]["status"] == "rejected":
                self._coverage_pause("broker_rejected_protective_order", now)
                return False
            matching = [o for o in active if (decimal(json.loads(o["intent"])["quantity"])-decimal(o["quantity"]) == qty
                        and decimal(json.loads(o["intent"])["stop_price"]) == stop and o["status"] in {"open", "partial"})]
            if len(matching) == 1 and len(active) == 1:
                coverage[symbol] = {"status": "broker_held", "client_id": matching[0]["client_id"], "quantity": str(qty),
                                    "residual_quantity": str(decimal(pos["quantity"])-qty)}
                continue
            if active:
                for order in active:
                    report = self.cancel(order["client_id"])
                    if report is None or report.status not in {"filled", "cancelled", "rejected"}:
                        self._coverage_pause("protection_cancel_unconfirmed", now)
                        return False
                # A stop may fill during cancellation. Rebuild before deciding replacement size.
                self.reconcile(now, protect=False)
                self._coverage_pause("protection_refresh_pending", now)
                return False
            if qty < decimal(asset["min_order_size"]) or not 0 < limit <= stop:
                coverage[symbol] = {"status": "below_broker_minimum", "quantity": str(qty)}
                self._coverage_pause("unprotected_residual_position", now, coverage)
                return False
            generation = len([o for o in self.book.orders(include_protection=True) if o["purpose"] == "protection"])
            intent = Intent(f"stop:{pos['opened']}:{generation}", pos["strategy"], symbol, "sell", qty, stop, now,
                            "broker_protective_stop", limit)
            self.book.reserve(intent, now, purpose="protection")
            body = {"symbol": symbol.replace("-", "/"), "qty": str(qty), "side": "sell", "type": "stop_limit",
                    "time_in_force": "gtc", "stop_price": str(stop), "limit_price": str(limit), "client_order_id": intent.client_id}
            try:
                report = self._post(intent, body)
                if report.status not in {"open", "partial"}:
                    self._coverage_pause("protection_not_resting", now)
                    return False
                self.journal.rebuild(now)
                coverage[symbol] = {"status": "broker_held", "client_id": intent.client_id, "quantity": str(qty),
                                    "residual_quantity": str(decimal(pos["quantity"])-qty)}
            except Exception:
                self.book.unknown(intent.client_id, now)
                self._coverage_pause("protection_submission_uncertain", now)
                return False
        # An orphan stop must not remain able to sell a later position accidentally.
        for order in self.active_protection():
            symbol = json.loads(order["intent"])["instrument"]
            if symbol not in self.book.positions():
                self.cancel(order["client_id"])
                self._coverage_pause("orphan_protection_reconciliation", now)
                return False
        with self.book.db:
            self.book.set("protection", {"complete": True, "checked_at": now, "positions": coverage})
        return True

    def _coverage_pause(self, reason, now, coverage=None):
        with self.book.db:
            self.book.set("entry_pause", reason)
            self.book.set("protection", {"complete": False, "checked_at": now, "reason": reason, "positions": coverage or {}})

    def prepare(self, intent, now):
        if intent.side != "sell":
            return True
        for order in self.active_protection(intent.instrument):
            report = self.cancel(order["client_id"])
            if report is None or report.status not in {"filled", "cancelled", "rejected"}:
                self._coverage_pause("exit_waiting_for_stop_cancellation", now)
                return False
        if not self.reconcile(now, protect=False):
            return False
        pos = self.book.positions().get(intent.instrument)
        # Never increase or silently alter an AI-reviewed quantity after a cancellation race.
        return bool(pos and intent.quantity <= decimal(pos["quantity"]))


def latest_quotes(http, instruments):
    data = http.request("GET", "/v1beta3/crypto/us/latest/quotes", data=True,
                        params={"symbols": ",".join(s.replace("-", "/") for s in instruments)})
    result = {}
    for symbol, q in data["quotes"].items():
        stamp = utc_timestamp(q["t"])
        normalized = symbol.replace("/", "-")
        result[normalized] = Quote(normalized, decimal(q["bp"]), decimal(q["ap"]), stamp)
    return result


def recent_bars(http, instruments, now=None, start_at=None):
    now = now or time.time()
    start = dt.datetime.fromtimestamp(now - 86400 * 7 if start_at is None else start_at, dt.timezone.utc).isoformat()
    params = {"symbols": ",".join(s.replace("-", "/") for s in instruments), "timeframe": "5Min",
              "start": start, "end": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(), "limit": 10000}
    collected, seen = {}, set()
    for _ in range(20):
        data = http.request("GET", "/v1beta3/crypto/us/bars", data=True, params=params)
        if not isinstance(data, dict) or not isinstance(data.get("bars"), dict):
            raise BrokerError("invalid historical data response")
        for symbol, bars in data["bars"].items():
            if symbol.replace("/", "-") not in instruments or not isinstance(bars, list):
                raise BrokerError("unexpected historical data instrument")
            collected.setdefault(symbol, []).extend(bars)
        token = data.get("next_page_token")
        if not token:
            break
        if not isinstance(token, str) or token in seen:
            raise BrokerError("historical pagination did not advance")
        seen.add(token)
        params = {**params, "page_token": token}
    else:
        raise BrokerError("historical data exceeds bounded pagination")
    return {symbol.replace("/", "-"): sorted([
        {"timestamp": utc_timestamp(b["t"]),
         "open": str(b["o"]), "high": str(b["h"]), "low": str(b["l"]), "close": str(b["c"]),
         "volume": str(b["v"])} for b in bars
        if utc_timestamp(b["t"]) + 300 <= now
    ], key=lambda b: b["timestamp"]) for symbol, bars in collected.items()}
