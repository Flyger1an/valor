"""Synthetic broker journals and mocked Telegram only; never place or send real orders."""
import importlib.util
import io
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
import urllib.error

MODULE = Path(__file__).parents[1] / "evolver/trading/telegram_alerts.py"
spec = importlib.util.spec_from_file_location("telegram_alerts", MODULE)
alerts_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alerts_module)
AlertError, PaperOrderAlerts, Telegram = alerts_module.AlertError, alerts_module.PaperOrderAlerts, alerts_module.Telegram


class Sender:
    def __init__(self, error=None):
        self.error, self.messages = error, []

    def send(self, text):
        self.messages.append(text)
        if self.error:
            raise self.error
        return 42


class OrderAlertsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "book.sqlite"
        self.config = {"schema_version": 1, "policy_hash": "a" * 64,
                       "broker_identity": "alpaca:demo:" + "b" * 16,
                       "recipient_hash": alerts_module.digest("12345"),
                       "bot_username": "TestValorBot", "instruments": ["BTC-USD", "ETH-USD"]}
        self.writer = sqlite3.connect(self.source)
        self.writer.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,timestamp REAL,event TEXT,payload TEXT);
            CREATE TABLE orders(client_id TEXT PRIMARY KEY,intent TEXT,purpose TEXT);
            CREATE TABLE alpaca_receipts(client_id TEXT PRIMARY KEY,remote_id TEXT,report TEXT);
        """)
        with self.writer:
            self.writer.execute("INSERT INTO meta VALUES ('identity',?)", (json.dumps({
                "policy": self.config["policy_hash"], "broker": self.config["broker_identity"]}),))
        self.state = self.root / "notifications/alerts.sqlite"
        self.reader = PaperOrderAlerts(self.source, self.state, self.config)

    def tearDown(self):
        self.reader.close()
        self.writer.close()
        self.tmp.cleanup()

    def event(self, cid="example", status="open", when=110, name="order.status"):
        with self.writer:
            self.writer.execute("INSERT INTO events(timestamp,event,payload) VALUES (?,?,?)",
                                (when, name, json.dumps({"client_id": cid, "status": status})))

    def order(self, cid="example", status="open", when=110, remote=True, purpose="discretionary", side="buy"):
        intent = {"client_id": cid, "instrument": "BTC-USD", "side": side,
                  "reason": "secret prose must not reach Telegram", "quantity": "123.987", "cash": "999999"}
        with self.writer:
            self.writer.execute("INSERT OR REPLACE INTO orders VALUES (?,?,?)", (cid, json.dumps(intent), purpose))
            self.writer.execute("INSERT OR REPLACE INTO alpaca_receipts VALUES (?,?,?)",
                                (cid, "private-broker-order-id" if remote else None,
                                 json.dumps({"client_id": cid, "status": status})))
        self.event(cid, status, when)

    def start(self):
        self.reader.collect(100)

    def pending(self):
        return self.reader.db.execute("SELECT * FROM outbox ORDER BY source_seq").fetchall()

    def test_existing_history_and_later_fill_are_not_replayed(self):
        self.order(when=90)
        self.start()
        self.event(status="filled", when=110)
        self.assertEqual(self.reader.collect(111), 0)
        self.assertEqual(self.pending(), [])

    def test_intent_uncertain_rejection_and_unconfirmed_receipt_do_not_notify(self):
        self.start()
        self.event(name="order.intent")
        self.event(name="order.uncertain")
        self.order("rejected", status="rejected")
        self.order("unconfirmed", remote=False)
        self.reader.collect(111)
        self.assertEqual(self.pending(), [])

    def test_acceptance_is_once_per_order_across_fills_and_restart(self):
        self.start()
        self.order()
        self.assertEqual(self.reader.collect(111), 1)
        sender = Sender()
        self.assertEqual(self.reader.deliver_one(sender, 111), "delivered")
        self.event(status="partial", when=120)
        self.event(status="filled", when=130)
        self.reader.close()
        self.reader = PaperOrderAlerts(self.source, self.state, self.config)
        self.assertEqual(self.reader.collect(131), 0)
        self.assertIsNone(self.reader.deliver_one(sender, 131))
        self.assertEqual(len(sender.messages), 1)
        self.assertIn("PAPER/DEMO", sender.messages[0])
        self.assertIn("BUY BTC-USD", sender.messages[0])
        self.assertIn("fill not confirmed", sender.messages[0])
        for private in ["123.987", "999999", "private-broker-order-id", "secret prose", "example"]:
            self.assertNotIn(private, sender.messages[0])

    def test_filled_receipt_and_protective_orders_are_labeled(self):
        self.start()
        self.order(status="filled", purpose="protection", side="sell")
        self.reader.collect(111)
        body = self.pending()[0]["body"]
        self.assertIn("Protective order accepted", body)
        self.assertIn("Recorded broker status: filled", body)
        self.assertIn("SELL BTC-USD", body)

    def test_preexisting_unacknowledged_intent_can_notify_after_new_receipt(self):
        self.order(remote=False, status="submitting", when=90)
        self.start()
        self.order(when=110)
        self.assertEqual(self.reader.collect(111), 1)

    def test_wrong_source_identity_and_history_rewrite_fail_closed(self):
        self.order(when=90)
        self.start()
        with self.writer:
            self.writer.execute("UPDATE events SET payload='{}'")
        with self.assertRaisesRegex(AlertError, "source_journal_rewritten"):
            self.reader.collect(111)
        with self.writer:
            self.writer.execute("UPDATE meta SET value=? WHERE key='identity'", (json.dumps({"policy": "a"*64, "broker": "alpaca:live:"+"b"*16}),))
        with self.assertRaisesRegex(AlertError, "source_identity_mismatch"):
            self.reader.collect(112)

    def test_source_connection_cannot_write_and_reads_active_wal(self):
        self.start()
        self.order()
        db = self.reader.source_connection()
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM orders")
        finally:
            db.close()
        self.assertEqual(self.reader.collect(111), 1)

    def test_rate_limit_retry_is_durable_and_preserves_other_orders(self):
        self.start()
        self.order("first")
        self.order("second", when=112)
        self.reader.collect(113)
        sender = Sender(AlertError("telegram_rate_limited", "retry", 120))
        self.assertEqual(self.reader.deliver_one(sender, 113), "pending")
        self.reader.close()
        self.reader = PaperOrderAlerts(self.source, self.state, self.config)
        sender.error = None
        self.assertIsNone(self.reader.deliver_one(sender, 200))
        self.assertEqual(self.reader.deliver_one(sender, 233), "delivered")
        self.assertIsNone(self.reader.deliver_one(sender, 240))
        self.assertEqual(self.reader.deliver_one(sender, 248), "delivered")

    def test_uncertain_response_and_interrupted_send_are_not_replayed(self):
        self.start()
        self.order("lost")
        self.order("interrupted")
        self.reader.collect(111)
        sender = Sender(AlertError("telegram_delivery_uncertain", "uncertain"))
        self.assertEqual(self.reader.deliver_one(sender, 111), "uncertain")
        with self.reader.db:
            self.reader.db.execute("UPDATE outbox SET status='sending' WHERE status='pending'")
        self.reader.close()
        self.reader = PaperOrderAlerts(self.source, self.state, self.config)
        self.assertIsNone(self.reader.deliver_one(Sender(), 1000))
        self.assertEqual([r["status"] for r in self.pending()], ["uncertain", "uncertain"])
        self.assertFalse(self.reader.status(1000, True, True)["healthy"])

    def test_old_events_expired_queue_and_capacity_do_not_flood(self):
        self.start()
        self.order("too-old", when=110)
        self.reader.collect(4000)
        self.assertEqual(self.pending(), [])
        self.reader.max_pending = 1
        self.order("one", when=4001)
        self.order("two", when=4002)
        self.assertEqual(self.reader.collect(4003), 1)
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(self.reader.deliver_one(Sender(), 8000), "expired")
        self.reader.collect(8001)
        self.assertEqual(len(self.pending()), 1)

    def test_changed_recipient_binding_cannot_reuse_outbox(self):
        self.start()
        other = {**self.config, "recipient_hash": alerts_module.digest("99999")}
        with self.assertRaisesRegex(AlertError, "notification_identity_changed"):
            PaperOrderAlerts(self.source, self.state, other)


class Response:
    def __init__(self, result):
        self.result = result
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return json.dumps({"ok": True, "result": self.result}).encode()


class Opener:
    def __init__(self, results):
        self.results, self.requests = list(results), []
    def open(self, request, timeout):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception): raise result
        return Response(result)


class TelegramTests(unittest.TestCase):
    token = "10000:" + "x" * 30
    config = {"recipient_hash": alerts_module.digest("12345"), "bot_username": "TestValorBot"}
    def test_exact_bot_and_private_chat_verified_before_send(self):
        opener = Opener([{"is_bot": True, "username": "TestValorBot", "id": 10000},
                         {"id": 12345, "type": "private"},
                         {"chat": {"id": 12345}, "from": {"id": 10000}, "message_id": 42}])
        client = Telegram(self.token, "12345", self.config, opener)
        with self.assertRaisesRegex(AlertError, "not_verified"):
            client.send("test")
        client.verify()
        self.assertEqual(client.send("PAPER/DEMO test"), 42)
        self.assertEqual(len(opener.requests), 3)
        payload = json.loads(opener.requests[-1].data)
        self.assertTrue(payload["protect_content"])
        self.assertNotIn("parse_mode", payload)

    def test_group_changed_recipient_or_wrong_bot_is_rejected(self):
        with self.assertRaises(AlertError):
            Telegram(self.token, "-10012345", self.config)
        with self.assertRaises(AlertError):
            Telegram(self.token, "99999", self.config)
        for chat in [{"id": 12345, "type": "group"}, {"id": 777, "type": "private"}]:
            c = Telegram(self.token, "12345", self.config, Opener([
                {"is_bot": True, "username": "TestValorBot", "id": 10000}, chat]))
            with self.assertRaisesRegex(AlertError, "identity_mismatch"):
                c.verify()
        wrong_bot = Telegram(self.token, "12345", self.config, Opener([
            {"is_bot": True, "username": "OtherBot", "id": 20000}, {"id": 12345, "type": "private"}]))
        with self.assertRaisesRegex(AlertError, "identity_mismatch"):
            wrong_bot.verify()

    def test_rate_limit_backoff_and_errors_never_expose_token(self):
        url = "https://api.telegram.org/bot"+self.token+"/sendMessage"
        limited = urllib.error.HTTPError(url, 429, "private", {}, io.BytesIO(b'{"parameters":{"retry_after":123}}'))
        cases = [(limited, "retry"), (urllib.error.HTTPError(url, 500, self.token, {}, None), "uncertain"),
                 (urllib.error.HTTPError(url, 302, self.token, {}, None), "blocked"),
                 (urllib.error.URLError(socket.gaierror("dns")), "retry"),
                 (TimeoutError(self.token), "uncertain")]
        for error, disposition in cases:
            c = Telegram(self.token, "12345", self.config, Opener([error]))
            with self.assertRaises(AlertError) as raised:
                c.call("sendMessage", {})
            self.assertEqual(raised.exception.disposition, disposition)
            self.assertNotIn(self.token, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
