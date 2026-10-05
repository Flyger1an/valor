"""Broker contract and an explicitly labelled, persistent local paper exchange."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Protocol

from .contracts import Intent, OrderReport, Policy, Quote, decimal, encode
from .ledger import Ledger


class Broker(Protocol):
    mode: str
    identity: str

    def submit(self, intent: Intent, quote: Quote) -> OrderReport: ...
    def lookup(self, client_id: str) -> OrderReport | None: ...
    def cancel(self, client_id: str) -> OrderReport: ...
    def account(self) -> dict: ...


class PaperBroker:
    mode = "paper"
    identity = "local-paper-v1"

    def __init__(self, ledger: Ledger, policy: Policy):
        if policy.mode != "paper":
            raise ValueError("paper broker cannot represent demo or live execution")
        self.ledger, self.policy = ledger, policy
        self.ledger.db.execute("""CREATE TABLE IF NOT EXISTS paper_exchange(
            client_id TEXT PRIMARY KEY, intent TEXT NOT NULL, report TEXT NOT NULL)""")
        self.ledger.db.commit()

    def lookup(self, client_id: str):
        row = self.ledger.db.execute("SELECT report FROM paper_exchange WHERE client_id=?", (client_id,)).fetchone()
        return OrderReport(**json.loads(row[0])) if row else None

    def submit(self, intent: Intent, quote: Quote):
        existing = self.lookup(intent.client_id)
        if existing:
            return existing
        slip = self.policy.slippage_bps / 10000
        price = quote.ask * (1 + slip) if intent.side == "buy" else quote.bid * (1 - slip)
        value = price * intent.quantity
        if (intent.side == "buy" and price > intent.limit_price or
                intent.side == "sell" and intent.limit_price and price < intent.limit_price):
            report = OrderReport(intent.client_id, "cancelled", decimal(0), decimal(0), decimal(0))
        else:
            report = OrderReport(intent.client_id, "filled", intent.quantity, value,
                                 value * self.policy.fee_bps / 10000)
        # Commit the exchange receipt before applying it to the book. A restart finds the receipt.
        with self.ledger.db:
            self.ledger.db.execute("INSERT INTO paper_exchange VALUES (?,?,?)",
                                   (intent.client_id, encode(asdict(intent)), encode(asdict(report))))
        return report

    def cancel(self, client_id: str):
        report = self.lookup(client_id)
        if report is None:
            raise ValueError("cannot cancel an unknown order")
        return report  # local paper orders are immediate fills

    def account(self):
        cash, positions = self.policy.starting_cash, {}
        for row in self.ledger.db.execute("SELECT intent,report FROM paper_exchange"):
            intent, report = json.loads(row[0]), OrderReport(**json.loads(row[1]))
            sign = 1 if intent["side"] == "buy" else -1
            symbol = intent["instrument"]
            positions[symbol] = positions.get(symbol, decimal(0)) + sign * report.filled_quantity
            cash -= sign * report.filled_value + report.fees
        return {"cash": cash, "positions": {s: q for s, q in positions.items() if q}, "open_order_ids": []}
