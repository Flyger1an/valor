"""Outbound-only paper-order notifications. Never imports or calls an execution broker."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class AlertError(Exception):
    """Only fixed, credential-free codes may cross the reporting boundary."""
    def __init__(self, code, disposition="blocked", retry_after=0):
        super().__init__(code)
        self.code, self.disposition, self.retry_after = code, disposition, retry_after


def load_config(path):
    raw = Path(path).read_bytes()
    if len(raw) > 8192:
        raise AlertError("invalid_config")
    c = json.loads(raw)
    return validate_config(c)


def validate_config(c):
    if (c.get("schema_version") != 1 or
            not re.fullmatch(r"[a-f0-9]{64}", c.get("policy_hash", "")) or
            not re.fullmatch(r"alpaca:demo:[a-f0-9]{16}", c.get("broker_identity", "")) or
            not re.fullmatch(r"[a-f0-9]{64}", c.get("recipient_hash", "")) or
            not re.fullmatch(r"[A-Za-z0-9_]{5,64}", c.get("bot_username", "")) or
            not isinstance(c.get("instruments"), list) or not 1 <= len(c["instruments"]) <= 64 or
            len(set(c["instruments"])) != len(c["instruments"]) or
            any(not isinstance(s, str) or not re.fullmatch(r"[A-Z0-9]+-USD", s) for s in c["instruments"]) or
            not re.fullmatch(r"[a-f0-9]{64}", c.get("acceptance_identity_policy_hash", c["policy_hash"]))):
        raise AlertError("invalid_config")
    return c


def migrate_config(state, old, new, now):
    """Offline source-pin expansion; recipient, cursor and delivery identities stay fixed."""
    validate_config(old)
    validate_config(new)
    origin = old.get("acceptance_identity_policy_hash", old["policy_hash"])
    strip = lambda c: {k: v for k, v in c.items() if k not in {"policy_hash", "instruments", "acceptance_identity_policy_hash"}}
    # Universe expansions add instruments; session expansions keep them identical.
    if (strip(old) != strip(new) or not set(old["instruments"]) <= set(new["instruments"])
            or new.get("acceptance_identity_policy_hash") != origin or old["policy_hash"] == new["policy_hash"]):
        raise AlertError("invalid_notification_universe_migration")
    if not Path(state).is_file():
        raise AlertError("notification_state_required")
    db = sqlite3.connect(state, timeout=10)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT value FROM meta WHERE key='config_identity'").fetchone()
        if not row or json.loads(row[0]) != digest(json.dumps(old, sort_keys=True)):
            raise AlertError("notification_identity_changed")
        db.execute("CREATE TABLE IF NOT EXISTS config_migrations (policy_hash TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        db.execute("INSERT INTO config_migrations VALUES (?,?)", (new["policy_hash"], json.dumps(
            {"at": now, "old": old, "new": new, "cursor_preserved": True, "delivery_ids_preserved": True}, sort_keys=True)))
        db.execute("UPDATE meta SET value=? WHERE key='config_identity'",
                   (json.dumps(digest(json.dumps(new, sort_keys=True))),))
        db.commit()
    finally:
        db.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Telegram:
    def __init__(self, token, chat_id, config, opener=None):
        if (not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token) or
                not re.fullmatch(r"[0-9]{1,20}", chat_id) or
                digest(chat_id) != config["recipient_hash"]):
            raise AlertError("credential_or_recipient_mismatch")
        self.token, self.chat_id, self.config = token, chat_id, config
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.bot_id = None

    def call(self, method, payload=None):
        if method not in {"getMe", "getChat", "sendMessage"}:
            raise AlertError("unsupported_telegram_operation")
        req = urllib.request.Request(
            "https://api.telegram.org/bot" + self.token + "/" + method,
            data=json.dumps(payload or {}).encode(), headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=8) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized")
            data = json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code == 429:
                try:
                    delay = json.loads(error.read(4096)).get("parameters", {}).get("retry_after", 60)
                    delay = float(delay)
                    if not math.isfinite(delay) or delay < 1:
                        delay = 60
                except Exception:
                    delay = 60
                raise AlertError("telegram_rate_limited", "retry", max(60, delay)) from None
            kind = "uncertain" if error.code >= 500 else "blocked"
            raise AlertError("telegram_http_" + str(error.code), kind) from None
        except urllib.error.URLError as error:
            # These failures precede submission. A timeout/connection reset may occur
            # after Telegram accepted the message and cannot safely be replayed.
            safe = isinstance(error.reason, (socket.gaierror, ConnectionRefusedError))
            raise AlertError("telegram_connect_failed" if safe else "telegram_delivery_uncertain",
                             "retry" if safe else "uncertain", 60) from None
        except Exception:
            raise AlertError("telegram_response_uncertain", "uncertain") from None
        if not isinstance(data, dict) or data.get("ok") is not True or "result" not in data:
            raise AlertError("telegram_response_uncertain", "uncertain")
        return data["result"]

    def verify(self):
        me = self.call("getMe")
        chat = self.call("getChat", {"chat_id": self.chat_id})
        if (me.get("is_bot") is not True or me.get("username") != self.config["bot_username"] or
                chat.get("type") != "private" or str(chat.get("id")) != self.chat_id):
            raise AlertError("telegram_identity_mismatch")
        self.bot_id = me["id"]

    def send(self, text):
        if self.bot_id is None:
            raise AlertError("telegram_not_verified")
        result = self.call("sendMessage", {"chat_id": self.chat_id, "text": text,
                                           "protect_content": True})
        if (str(result.get("chat", {}).get("id")) != self.chat_id or
                result.get("from", {}).get("id") != self.bot_id or
                type(result.get("message_id")) is not int):
            raise AlertError("telegram_receipt_uncertain", "uncertain")
        return result["message_id"]


class PaperOrderAlerts:
    """One independent SQLite outbox; source is a short-lived, read-only WAL reader."""
    max_age = 3600
    max_pending = 100
    min_send_interval = 15

    def __init__(self, source, state, config):
        self.source, self.state, self.config = Path(source), Path(state), config
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS historical_orders(id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS outbox(
                id TEXT PRIMARY KEY, source_seq INTEGER NOT NULL, created REAL NOT NULL,
                body TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, error TEXT, message_id INTEGER);
        """)
        identity = digest(json.dumps(config, sort_keys=True))
        prior = self.get("config_identity")
        if prior is not None and prior != identity:
            self.db.close()
            raise AlertError("notification_identity_changed")
        with self.db:
            self.set("config_identity", identity)
            self.db.execute("UPDATE outbox SET status='uncertain',error='interrupted_delivery' WHERE status='sending'")

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def source_connection(self):
        db = sqlite3.connect(self.source.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN")
            row = db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
            identity = json.loads(row[0]) if row else None
            if identity != {"policy": self.config["policy_hash"], "broker": self.config["broker_identity"]}:
                raise AlertError("source_identity_mismatch")
            return db
        except Exception:
            db.close()
            raise

    @staticmethod
    def event_hash(row):
        return digest(json.dumps(dict(row), sort_keys=True)) if row else "empty"

    def acceptance(self, source, event, now):
        if event["event"] != "order.status":
            return None
        stamp = event["timestamp"]
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or not 0 <= now-stamp <= self.max_age:
            return None
        payload = json.loads(event["payload"])
        status, cid = payload.get("status"), payload.get("client_id")
        if status not in {"open", "partial", "filled"} or not isinstance(cid, str) or not 1 <= len(cid) <= 256:
            return None
        row = source.execute("""SELECT o.intent,o.purpose,r.remote_id,r.report
            FROM orders o JOIN alpaca_receipts r ON o.client_id=r.client_id
            WHERE o.client_id=?""", (cid,)).fetchone()
        if row is None or not row["remote_id"] or not row["report"]:
            return None  # An intent/reservation or uncertain submission is never a placement.
        receipt, intent = json.loads(row["report"]), json.loads(row["intent"])
        if (receipt.get("client_id") != cid or intent.get("client_id") != cid or
                intent.get("instrument") not in self.config["instruments"] or intent.get("side") not in {"buy", "sell"} or
                row["purpose"] not in {"discretionary", "protection"}):
            return None
        # No quantities, balances, account IDs, strategy prose, raw receipts or broker
        # IDs enter Telegram. A short opaque reference makes duplicate review possible.
        identity = digest(self.config.get("acceptance_identity_policy_hash", self.config["policy_hash"]) + ":accepted:" + cid)
        purpose = "Protective order" if row["purpose"] == "protection" else "Order"
        label = {"open": "open (fill not confirmed)", "partial": "partially filled", "filled": "filled"}[status]
        body = (f"VALOR — PAPER/DEMO\n{purpose} accepted by Alpaca\n"
                f"{intent['side'].upper()} {intent['instrument']}\nRecorded broker status: {label}\n"
                f"UTC: {dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat(timespec='seconds')}\n"
                f"Ref: {identity[:12]}\nSimulation only.")
        return identity, body

    def collect(self, now):
        source = self.source_connection()
        try:
            newest = source.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            maximum = newest["seq"] if newest else 0
            cursor = self.get("cursor")
            if cursor is None:
                # On first activation, existing history is the baseline, never a backlog.
                with self.db:
                    for order in source.execute("SELECT client_id FROM alpaca_receipts WHERE remote_id IS NOT NULL AND remote_id!=''"):
                        self.db.execute("INSERT OR IGNORE INTO historical_orders VALUES (?)",
                                        (digest(self.config.get("acceptance_identity_policy_hash", self.config["policy_hash"]) + ":accepted:" + order[0]),))
                    self.set("cursor", maximum)
                    self.set("cursor_hash", self.event_hash(newest))
                    self.set("activated_at", now)
                return 0
            old = source.execute("SELECT * FROM events WHERE seq=?", (cursor,)).fetchone() if cursor else None
            if maximum < cursor or self.event_hash(old) != self.get("cursor_hash"):
                raise AlertError("source_journal_rewritten")
            rows = source.execute("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 250", (cursor,)).fetchall()
            added = 0
            with self.db:
                self.db.execute("UPDATE outbox SET status='expired',error='age_limit' WHERE status='pending' AND created<?",
                                (now-self.max_age,))
                pending = self.db.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0]
                for event in rows:
                    item = self.acceptance(source, event, now)
                    known = item and (self.db.execute("SELECT 1 FROM outbox WHERE id=?", (item[0],)).fetchone() or
                                      self.db.execute("SELECT 1 FROM historical_orders WHERE id=?", (item[0],)).fetchone())
                    if item and not known:
                        if pending >= self.max_pending:
                            break  # Backpressure affects this reader only, never the trading writer.
                        self.db.execute("INSERT INTO outbox(id,source_seq,created,body,status) VALUES (?,?,?,?,'pending')",
                                        (item[0], event["seq"], event["timestamp"], item[1]))
                        pending += 1
                        added += 1
                    self.set("cursor", event["seq"])
                    self.set("cursor_hash", self.event_hash(event))
            return added
        finally:
            source.close()

    def deliver_one(self, telegram, now):
        if now < self.get("next_send", 0):
            return None
        row = self.db.execute("SELECT * FROM outbox WHERE status='pending' AND next_attempt<=? ORDER BY source_seq LIMIT 1", (now,)).fetchone()
        if row is None:
            return None
        if now-row["created"] > self.max_age:
            with self.db:
                self.db.execute("UPDATE outbox SET status='expired',error='age_limit' WHERE id=?", (row["id"],))
            return "expired"
        # Commit the attempt before HTTP. After a crash, 'sending' becomes 'uncertain'
        # rather than replaying a possibly delivered message. Telegram has no send key.
        with self.db:
            self.db.execute("UPDATE outbox SET status='sending',attempts=attempts+1 WHERE id=?", (row["id"],))
            self.set("next_send", now+self.min_send_interval)
        try:
            message_id = telegram.send(row["body"])
        except AlertError as error:
            status = "pending" if error.disposition == "retry" else error.disposition
            backoff = max(error.retry_after, min(900, 30 * 2**min(row["attempts"], 5)))
            with self.db:
                self.db.execute("UPDATE outbox SET status=?,error=?,next_attempt=? WHERE id=?",
                                (status, error.code, now+backoff, row["id"]))
                self.set("next_send", now+backoff)
            return status
        except Exception:
            with self.db:
                self.db.execute("UPDATE outbox SET status='uncertain',error='delivery_interrupted' WHERE id=?", (row["id"],))
            return "uncertain"
        with self.db:
            self.db.execute("UPDATE outbox SET status='delivered',message_id=?,error=NULL WHERE id=?", (message_id, row["id"]))
        return "delivered"

    def status(self, now, source_ok, connection_ok, error=None):
        counts = dict(self.db.execute("SELECT status,COUNT(*) FROM outbox GROUP BY status").fetchall())
        return {"schema_version": 1, "timestamp": now, "mode": "demo", "source_ok": source_ok,
                "connection_verified": connection_ok, "cursor": self.get("cursor"), "counts": counts,
                "healthy": source_ok and connection_ok and not counts.get("uncertain") and not counts.get("blocked"),
                "error": error}


def write_status(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/config/notifications.json")
    parser.add_argument("--source", default="/source/book.sqlite")
    parser.add_argument("--state", default="/state/alerts.sqlite")
    parser.add_argument("--token-file", default="/credentials/bot-token")
    parser.add_argument("--chat-file", default="/credentials/chat-id")
    parser.add_argument("--check-source", action="store_true")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    status_path = Path(args.state).with_name("status.json")
    if args.health:
        try:
            value = json.loads(status_path.read_text())
            return 0 if value["healthy"] and 0 <= time.time()-value["timestamp"] < 45 else 1
        except Exception:
            return 1
    config = load_config(args.config)
    Path(args.state).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.state).with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        alerts = PaperOrderAlerts(args.source, args.state, config)
        try:
            if args.check_source:
                db = alerts.source_connection()
                db.close()
                print(json.dumps({"source_verified": True, "mode": "demo", "credentials_used": False}))
                return 0
            token = Path(args.token_file).read_text().strip()
            chat_id = Path(args.chat_file).read_text().strip()
            telegram = Telegram(token, chat_id, config)
            stop = threading.Event()
            for signum in (signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, lambda *_: stop.set())
            verified_until, next_verify, connection_error = 0, 0, None
            while not stop.is_set():
                now = time.time()
                source_ok, error = False, None
                try:
                    alerts.collect(now)
                    source_ok = True
                    if now >= verified_until and now >= next_verify:
                        telegram.verify()
                        verified_until = time.time()+300
                        connection_error = None
                    if now < verified_until:
                        result = alerts.deliver_one(telegram, now)
                        if result in {"blocked", "uncertain"}:
                            verified_until = 0
                            next_verify = now+300
                            connection_error = "delivery_" + result
                except AlertError as exc:
                    error = exc.code
                    next_verify = now+max(60, exc.retry_after)
                    if source_ok:
                        connection_error = error
                except Exception:
                    error = "source_or_state_unavailable"
                write_status(status_path, alerts.status(time.time(), source_ok, time.time() < verified_until,
                                                       error or connection_error))
                stop.wait(5)
        finally:
            alerts.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Never stringify urllib errors, environment values, source rows or paths.
        print(json.dumps({"healthy": False, "error": error.code if isinstance(error, AlertError) else "startup_failed"}), flush=True)
        raise SystemExit(1) from None
