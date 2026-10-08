"""One durable execution book. Cumulative fills are applied exactly once in a transaction."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .contracts import Intent, OrderReport, Policy, TERMINAL, decimal, encode


class Ledger:
    def __init__(self, path: str | Path, policy: Policy, broker_identity: str):
        self.policy = policy
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(
                client_id TEXT PRIMARY KEY, intent TEXT NOT NULL, status TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '0', value TEXT NOT NULL DEFAULT '0',
                fees TEXT NOT NULL DEFAULT '0', created REAL NOT NULL, base_fees TEXT NOT NULL DEFAULT '0');
            CREATE TABLE IF NOT EXISTS positions(
                instrument TEXT PRIMARY KEY, strategy TEXT NOT NULL, quantity TEXT NOT NULL,
                cost_basis TEXT NOT NULL, stop_price TEXT NOT NULL, opened REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                event TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quotes(instrument TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS equity_days(day TEXT PRIMARY KEY, timestamp REAL NOT NULL, equity TEXT NOT NULL);
        """)
        if "purpose" not in {r[1] for r in self.db.execute("PRAGMA table_info(orders)")}:
            self.db.execute("ALTER TABLE orders ADD COLUMN purpose TEXT NOT NULL DEFAULT 'discretionary'")
            self.db.commit()
        identity = {"policy": policy.fingerprint, "broker": broker_identity}
        existing = self.get("identity")
        if existing is not None and existing != identity:
            self.db.close()
            raise ValueError("book belongs to a different policy or broker; explicit migration required")
        with self.db:
            if existing is None:
                for key, value in {"identity": identity, "cash": str(policy.starting_cash),
                                   "realized_pnl": "0", "peak_equity": str(policy.starting_cash),
                                   "halt": "", "supervisor": {"action": "pause_entries", "scale": "1",
                                                              "expires_at": 0, "issued_at": 0}}.items():
                    self.set(key, value)

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, encode(value)))

    def event(self, now: float, name: str, payload: dict):
        self.db.execute("INSERT INTO events(timestamp,event,payload) VALUES (?,?,?)",
                        (now, name, encode(payload)))

    def halt(self, reason: str, now: float):
        with self.db:
            if not self.get("halt"):
                self.set("halt", reason)
                self.event(now, "risk.halted", {"reason": reason})

    def order(self, client_id):
        row = self.db.execute("SELECT * FROM orders WHERE client_id=?", (client_id,)).fetchone()
        return dict(row) if row else None

    def orders(self, pending_only=False, include_protection=False):
        rows = self.db.execute("SELECT * FROM orders ORDER BY created, client_id").fetchall()
        return [dict(r) for r in rows if (include_protection or r["purpose"] != "protection")
                and (not pending_only or r["status"] not in TERMINAL)]

    def positions(self):
        return {r["instrument"]: dict(r) for r in self.db.execute("SELECT * FROM positions")}

    def reserve(self, intent: Intent, now: float, purpose="discretionary"):
        if purpose not in {"discretionary", "protection"}:
            raise ValueError("invalid order purpose")
        with self.db:
            self.db.execute("INSERT INTO orders(client_id,intent,status,created,purpose) VALUES (?,?,?,?,?)",
                            (intent.client_id, encode(asdict(intent)), "submitting", now, purpose))
            self.event(now, "order.intent", {"client_id": intent.client_id, "purpose": purpose, **asdict(intent)})

    def unknown(self, client_id: str, now: float):
        with self.db:
            previous = self.order(client_id)
            if previous and previous["status"] == "unknown":
                return
            self.db.execute("UPDATE orders SET status='unknown' WHERE client_id=?", (client_id,))
            self.event(now, "order.uncertain", {"client_id": client_id})

    def apply_report(self, report: OrderReport, now: float):
        with self.db:
            order = self.order(report.client_id)
            if order is None:
                raise ValueError("broker reported an unknown client order")
            intent = Intent(**json.loads(order["intent"]))
            old_qty, old_value, old_fees = (decimal(order[k]) for k in ("quantity", "value", "fees"))
            old_base_fees = decimal(order["base_fees"])
            qty, value, fees = report.filled_quantity, report.filled_value, report.fees
            if qty < old_qty or value < old_value or fees < old_fees or report.base_fees < old_base_fees or qty > intent.quantity:
                raise ValueError("non-monotonic or excessive cumulative fill")
            if order["status"] in TERMINAL and report.status != order["status"]:
                raise ValueError("terminal order cannot return to another state")
            if report.status == "filled" and qty != intent.quantity:
                raise ValueError("filled state requires the complete quantity")
            if report.status == "rejected" and qty:
                raise ValueError("rejected order cannot contain fills")
            dq, dv, df = qty - old_qty, value - old_value, fees - old_fees
            if (dq and self.policy.mode == "live" and self.policy.study_start_event == "first_live_fill"
                    and self.get("study_started_at") is None):
                stamp = report.first_fill_timestamp
                if stamp is None or not order["created"] - 5 <= stamp <= now:
                    raise ValueError("first live fill requires a verified broker execution timestamp")
                self.set("study_started_at", stamp)
                self.event(now, "study.started", {"mode": "live", "first_fill_timestamp": stamp})
            dbf = report.base_fees - old_base_fees
            if not dq and dv:
                raise ValueError("value changed without an incremental fill")
            if df and not qty:
                raise ValueError("fees without a fill are unsupported")
            if intent.side == "sell" and report.base_fees:
                raise ValueError("base-denominated sell fees are unsupported")
            if dq or df or dbf:
                pos = self.positions().get(intent.instrument)
                cash = decimal(self.get("cash"))
                if intent.side == "buy":
                    if pos and pos["strategy"] != intent.strategy:
                        raise ValueError("position already belongs to another strategy")
                    old_pos_qty = decimal(pos["quantity"]) if pos else decimal(0)
                    old_basis = decimal(pos["cost_basis"]) if pos else decimal(0)
                    if old_pos_qty + dq - dbf <= 0:
                        raise ValueError("late base fee cannot be attributed to a closed position")
                    cash -= dv + df
                    self.db.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?)",
                                    (intent.instrument, intent.strategy, str(old_pos_qty + dq - dbf),
                                     str(old_basis + dv + df), str(intent.stop_price),
                                     pos["opened"] if pos else now))
                else:
                    if not pos or dq > decimal(pos["quantity"]):
                        raise ValueError("exit exceeds the owned position")
                    position_qty = decimal(pos["quantity"])
                    basis = decimal(pos["cost_basis"])
                    removed_basis = basis * dq / position_qty
                    cash += dv - df
                    increment = dv - df - removed_basis
                    realized = decimal(self.get("realized_pnl")) + increment
                    self.set("realized_pnl", str(realized))
                    outcome_key = f"outcome:{intent.instrument}:{pos['opened']}"
                    trade_pnl = decimal(self.get(outcome_key, "0")) + increment
                    self.set(outcome_key, str(trade_pnl))
                    if dq == position_qty:
                        self.db.execute("DELETE FROM positions WHERE instrument=?", (intent.instrument,))
                        self.event(now, "trade.closed", {"instrument": intent.instrument, "strategy": intent.strategy,
                                                        "realized_pnl": str(trade_pnl), "opened": pos["opened"]})
                    else:
                        self.db.execute("UPDATE positions SET quantity=?,cost_basis=? WHERE instrument=?",
                                        (str(position_qty - dq), str(basis - removed_basis), intent.instrument))
                self.set("cash", str(cash))
                self.event(now, "order.fill", {"client_id": report.client_id, "instrument": intent.instrument,
                           "side": intent.side, "quantity": str(dq), "value": str(dv), "fees": str(df),
                           "base_fees": str(dbf)})
            changed = order["status"] != report.status or bool(dq or df or dbf)
            self.db.execute("UPDATE orders SET status=?,quantity=?,value=?,fees=?,base_fees=? WHERE client_id=?",
                            (report.status, str(qty), str(value), str(fees), str(report.base_fees), report.client_id))
            if changed:
                self.event(now, "order.status", {"client_id": report.client_id, "status": report.status})

    def recent_events(self, limit=50):
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in self.db.execute(
            "SELECT * FROM events ORDER BY seq DESC LIMIT ?", (limit,))]

    def performance(self):
        outcomes = [decimal(r["realized_pnl"]) for r in self.closed_trades()]
        gains = sum((max(decimal(0), p) for p in outcomes), decimal(0))
        losses = -sum((min(decimal(0), p) for p in outcomes), decimal(0))
        return {"closed_trades": len(outcomes), "wins": sum(p > 0 for p in outcomes),
                "losses": sum(p < 0 for p in outcomes), "gross_gains_after_fees": str(gains),
                "gross_losses_after_fees": str(losses),
                "profit_factor": str(gains/losses) if losses else None,
                "max_observed_drawdown_pct": self.get("max_observed_drawdown_pct", "0")}

    def closed_trades(self):
        if self.get("activity_accounting"):
            return [dict(r) for r in self.db.execute("SELECT * FROM broker_closed_trades ORDER BY closed,lot_id")]
        return [{**json.loads(r["payload"]), "closed": r["timestamp"]} for r in self.db.execute(
            "SELECT timestamp,payload FROM events WHERE event='trade.closed' ORDER BY seq")]

    @property
    def revision(self):
        return self.db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
