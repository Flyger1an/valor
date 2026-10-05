"""Small, separately runnable VPS processes. Standard library only; no legacy research imports."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .contracts import Policy, encode
from .engine import write_snapshot
from .ipc import Mailbox, read_object


def paths(root):
    root = Path(root)
    return {name: Path(os.environ.get("VALOR_" + name.upper() + "_DIR", str(root / name)))
            for name in ("state", "outbox", "inbox", "market", "research", "agents", "news")}


def load_policy(path):
    return Policy.from_dict(read_object(path, limit=20_000))


def build_broker(policy, state):
    from .ledger import Ledger
    from .broker import PaperBroker
    if policy.mode == "paper":
        book = Ledger(state / "book.sqlite", policy, PaperBroker.identity)
        return book, PaperBroker(book, policy)
    from .alpaca import AlpacaBroker, AlpacaHTTP
    prefix = "ALPACA_PAPER_" if policy.mode == "demo" else "ALPACA_LIVE_"
    http = AlpacaHTTP(policy.mode, os.getenv(prefix + "KEY", ""), os.getenv(prefix + "SECRET", ""))
    broker = AlpacaBroker(policy, http, os.getenv(prefix + "ACCOUNT_ID", ""))
    book = Ledger(state / "book.sqlite", policy, broker.identity)
    broker.bind(book)
    return book, broker


class Feed:
    def __init__(self, policy, target, *, background=False):
        from .market import CoinbaseData, AlpacaData
        self.policy, self.target = policy, target
        source = os.getenv("VALOR_DATA_SOURCE", "coinbase_public" if policy.mode == "paper" else "alpaca")
        if source == "coinbase_public" and policy.mode == "paper":
            self.provider = CoinbaseData()
        elif source == "alpaca":
            from .alpaca import AlpacaHTTP
            self.provider = AlpacaData(AlpacaHTTP("demo", os.getenv("ALPACA_DATA_KEY", ""), os.getenv("ALPACA_DATA_SECRET", "")))
        else:
            raise ValueError("remote execution requires matching Alpaca market data")
        self.next_bars = 0
        self.increments = None
        self.instrument_rules = {}
        self.background, self.bar_thread = background, None

    def tick(self, now):
        quotes = self.provider.quotes(self.policy.allowed_instruments)
        if self.increments is None:
            if hasattr(self.provider, "instrument_rules"):
                self.instrument_rules = self.provider.instrument_rules(self.policy.allowed_instruments)
                self.increments = {s: r["increment"] for s, r in self.instrument_rules.items()}
            else:
                self.increments = self.provider.increments(self.policy.allowed_instruments) if hasattr(self.provider, "increments") else {}
        header = {"source": self.provider.source, "policy_hash": self.policy.fingerprint, "timestamp": time.time(),
                  "venue": getattr(self.provider, "venue", self.provider.source),
                  "increments": self.increments, "instrument_rules": self.instrument_rules,
                  "symbols": list(self.policy.allowed_instruments)}
        write_snapshot(self.target / "quotes.json", {**header, "quotes": {s: asdict(q) for s, q in quotes.items()}})
        if now < self.next_bars or self.bar_thread is not None and self.bar_thread.is_alive():
            return
        self.next_bars = now + 60
        if self.background:
            self.bar_thread = threading.Thread(target=self._background_bars, args=(now, header), daemon=True)
            self.bar_thread.start()
        else:
            self._refresh_bars(now, header)

    def _background_bars(self, now, header):
        try:
            self._refresh_bars(now, header)
        except Exception as exc:
            print(encode({"role": "feed", "event": "bars_unavailable", "error_type": type(exc).__name__}), flush=True)

    def _refresh_bars(self, now, header):
        from .market import merge_bars
        saved = read_object(self.target / "history.json", {"histories": {}})
        if (saved.get("source", self.provider.source) != self.provider.source
                or saved.get("venue", header["venue"]) != header["venue"]):
            raise ValueError("cannot mix market data venues in a study")
        incoming = {}
        symbols = self.policy.allowed_instruments
        for offset in range(0, len(symbols), 4):
            group = symbols[offset:offset+4]
            ready = [s for s in group if saved["histories"].get(s)]
            missing = [s for s in group if s not in ready]
            if missing:
                incoming.update(self.provider.bars(missing, now))
            if ready:
                if hasattr(self.provider, "bars_since"):
                    start = min(saved["histories"][s][-1]["timestamp"] for s in ready)-600
                    incoming.update(self.provider.bars_since(ready, now, start))
                else:
                    incoming.update(self.provider.bars(ready, now))
        histories, errors = {}, {}
        for symbol in self.policy.allowed_instruments:
            try:
                histories[symbol] = merge_bars(saved["histories"].get(symbol, []), incoming.get(symbol, []), now)
            except ValueError:
                # Retain the frozen observations; quarantine this symbol instead of
                # replacing history or preventing unrelated symbols from refreshing.
                histories[symbol] = saved["histories"].get(symbol, [])
                errors[symbol] = "closed_bar_revision_or_invalid_bar"
        # An execution-sized view avoids decoding the full research history every five seconds.
        write_snapshot(self.target / "signals.json", {**header, "history_errors": errors,
            "histories": {s: [] if s in errors else b[-400:] for s, b in histories.items()}})
        write_snapshot(self.target / "history.json", {**header, "history_errors": errors, "histories": histories})


def research_tick(policy, p, now):
    from .learning import evaluate
    data = read_object(p["market"] / "history.json", {})
    if data.get("policy_hash") != policy.fingerprint or not 0 <= now-data.get("timestamp", 0) <= 600:
        raise ValueError("research needs fresh, matching market history")
    if data.get("history_errors"):
        raise ValueError("research requires resolution of quarantined historical bars")
    saved = read_object(p["research"] / "assessment.json", {})
    if saved.get("policy_hash") and saved["policy_hash"] != policy.fingerprint:
        # Preserve the old classification, then start a prospective selection cohort.
        archive = "assessment-"+hashlib.sha256(encode(saved).encode()).hexdigest()+".json"
        write_snapshot(p["research"] / archive, saved)
        saved = {"cutoff": now+300, "universe_boundary_at": now, "prior_policy_hash": saved["policy_hash"]}
    snapshot = read_object(p["outbox"] / "snapshot.json", {})
    learning = snapshot.get("learning", {})
    if saved.get("status") == "review_required" and learning.get("finalized_evidence_hash") != saved.get("evidence_hash"):
        # Freeze evidence awaiting review; never silently swap the sample under the reviewers.
        return
    if saved.get("status") == "rejected" or learning.get("finalized_evidence_hash") == saved.get("evidence_hash") and saved.get("evidence_hash"):
        saved = {"cutoff": saved["end"] + 300}
    incumbent = snapshot.get("active_strategy") or policy.approved_strategies[0]
    assessed = evaluate(policy, data["histories"], saved, incumbent)
    write_snapshot(p["research"] / "assessment.json", {**assessed, "policy_hash": policy.fingerprint,
                  "universe_boundary_at": saved.get("universe_boundary_at"),
                  "prior_policy_hash": saved.get("prior_policy_hash"),
                  "source": data["source"], "evaluated_at": now})


def status(outbox, now=None):
    now = time.time() if now is None else now
    snapshot = read_object(Path(outbox) / "snapshot.json", {})
    if not snapshot:
        return {"healthy": False, "reason": "worker_has_not_started"}
    from .contracts import Quote
    quotes = snapshot.get("quotes", {})
    positions = snapshot.get("positions", [])
    if "market_feed_received_at" in snapshot:
        feed_fresh = 0 <= now-snapshot["market_feed_received_at"] <= 30
        held_fresh = all(p["instrument"] in quotes and Quote(**quotes[p["instrument"]]).fresh(now, 30) for p in positions)
        usable_quotes = bool(quotes) and (not snapshot.get("entry_session_open", True)
                                         or any(Quote(**q).fresh(now, 30) for q in quotes.values()))
        market_healthy = feed_fresh and held_fresh and usable_quotes
    else:
        market_healthy = bool(quotes) and all(Quote(**q).fresh(now, 30) for q in quotes.values())
    healthy = (0 <= now - snapshot.get("timestamp", 0) <= 30 and bool(quotes)
               and market_healthy
               and 0 <= now - (snapshot.get("broker_reconciled_at") or 0) <= 30)
    if snapshot.get("news", {}).get("required") and snapshot["news"].get("entry_blocked", True):
        healthy = False
    return {"healthy": healthy, "worker_age_seconds": round(now-snapshot["timestamp"], 1), **snapshot}


def serve(outbox, bind, port):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in {"/", "/status", "/healthz"}:
                self.send_error(404)
                return
            try:
                value = status(outbox)
                payload = encode(value if self.path != "/healthz" else {"healthy": value["healthy"], "mode": value.get("mode")}).encode()
                code = 503 if self.path == "/healthz" and not value["healthy"] else 200
            except Exception:
                code, payload = 503, b'{"healthy":false,"reason":"snapshot_unavailable"}'
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *_):
            pass
    ThreadingHTTPServer((bind, port), Handler).serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("validate", "worker", "feed", "agents", "research", "monitor", "status", "smoke", "watchdog", "news"))
    parser.add_argument("--policy", default="infra/trading/policy.paper.json")
    parser.add_argument("--root", default=".valor/trading-paper")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--require-fresh", action="store_true")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    args = parser.parse_args(argv)
    policy, p = load_policy(args.policy), paths(args.root)
    if args.role == "validate":
        from .strategies import BY_VERSION
        if any(v not in BY_VERSION for v in policy.approved_strategies):
            raise ValueError("unknown strategy version")
        print(encode({"valid": True, "mode": policy.mode, "policy_hash": policy.fingerprint,
                      "study_end_utc": policy.study_end_utc, "strategies": len(policy.approved_strategies)}))
        return 0
    if args.role == "status":
        value = status(p["outbox"])
        print(encode(value))
        return int(args.require_fresh and not value["healthy"])
    if args.role == "monitor":
        serve(p["outbox"], args.bind, args.port)
        return 0
    if args.role == "smoke":
        from .smoke import run
        print(encode(run(policy, Path(args.root))))
        return 0
    if args.role == "watchdog":
        from .watchdog import ping
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        while not stop.is_set():
            try:
                ping(os.environ.get("VALOR_HEARTBEAT_URL"), status(p["outbox"]), time.time())
            except Exception as exc:
                print(encode({"role": "watchdog", "event": "ping_failed", "error_type": type(exc).__name__}), flush=True)
                if args.once:
                    return 1
            if args.once:
                return 0
            stop.wait(30)
        return 0
    owned = {"worker": ("state", "outbox"), "feed": ("market",), "agents": ("agents", "inbox"), "research": ("research",), "news": ("news",)}[args.role]
    for name in owned:
        p[name].mkdir(parents=True, exist_ok=True)
    lock = (p[owned[0]] / (args.role + ".lock")).open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.role == "worker":
        from .worker import Worker
        book, broker = build_broker(policy, p["state"])
        worker = Worker(book, policy, broker, p["state"], p["outbox"], p["inbox"], p["market"], p["research"], p["news"])
        tick, interval = worker.tick, 5
    elif args.role == "feed":
        tick, interval = Feed(policy, p["market"], background=True).tick, 5
    elif args.role == "research":
        tick, interval = lambda now: research_tick(policy, p, now), 3600
    elif args.role == "news":
        from .news import NewsFeed
        from .alpaca import AlpacaHTTP
        tick, interval = NewsFeed(AlpacaHTTP("demo", os.getenv("ALPACA_DATA_KEY", ""), os.getenv("ALPACA_DATA_SECRET", "")), p["news"]).tick, 60
    else:
        from .agent_worker import BudgetedModel, agent_tick
        mailbox = Mailbox(p["outbox"] / "requests", p["inbox"] / "responses")
        model = os.getenv("VALOR_AGENT_MODEL", "gpt-6-luna")
        if model != "gpt-6-luna":
            raise ValueError("model change requires a code/pricing/latency review for this study")
        analyst = BudgetedModel(p["agents"] / "usage.sqlite", policy, model, os.getenv("OPENAI_API_KEY", ""))
        reviewer = BudgetedModel(p["agents"] / "usage.sqlite", policy, model, os.getenv("OPENAI_API_KEY", ""))
        tick, interval = lambda now: agent_tick(mailbox, policy, analyst, reviewer, analyst.usage, now), 1
    stopped = threading.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stopped.set())
    try:
        while not stopped.is_set():
            delay = interval
            try:
                tick(time.time())
            except Exception as exc:
                # Data, request bodies, credentials and provider error bodies never enter stdout.
                print(encode({"role": args.role, "event": "tick_failed", "error_type": type(exc).__name__}), flush=True)
                if args.once:
                    return 1
                delay = min(interval, 30)
            if args.once:
                return 0
            stopped.wait(delay)
    finally:
        if args.role == "worker":
            book.close()
        lock.close()
    return 0
