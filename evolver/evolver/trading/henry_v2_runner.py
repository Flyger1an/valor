"""Local-only Henry v2 runner. Reads Valor's existing market files read-only; no network clients.

Commands: init (explicit, once), run (loop), tick (one frame), report, replay.
Henry v2 never shares a journal, bankroll or rules with the three-book experiment.
"""
from __future__ import annotations

import argparse
import fcntl
import signal
import threading
import time
from pathlib import Path

from .contracts import Policy, decimal as D, encode
from .engine import write_snapshot
from .henry_v2 import HenryV2, IntegrityError, digest
from .ipc import read_object


def capture(book: HenryV2, source_root, now):
    source = Path(source_root)
    prices = read_object(source/"market"/"quotes.json", {}, limit=100_000)
    signals = read_object(source/"market"/"signals.json", {}, limit=1_000_000)
    if (prices.get("source") != signals.get("source") or not 0 <= now-prices.get("timestamp", 0) <= 30
            or not 0 <= now-signals.get("timestamp", 0) <= 600):
        raise ValueError("fresh, matching market files are required")
    state = book.state()
    quotes, bars = {}, {}
    for symbol in book.identity["symbols"]:
        history = signals.get("histories", {}).get(symbol, [])
        history = [b for b in history if b["timestamp"]+300 <= now]
        retained = {b["timestamp"]: b for b in state["histories"].get(symbol, [])}
        if any(b["timestamp"] in retained and retained[b["timestamp"]] != b for b in history):
            bars[symbol] = history  # let the reducer record the revision as an auditable halt
        else:
            bars[symbol] = [b for b in history if b["timestamp"] not in retained]
        raw = prices.get("quotes", {}).get(symbol)
        if not raw or not history:
            continue
        rules = signals.get("instrument_rules", {}).get(symbol, {})
        q = {k: raw[k] for k in ("bid", "ask", "timestamp")}
        q.update(capacity=str(D(history[-1]["volume"])*D(".01")), capacity_bucket=history[-1]["timestamp"],
                 increment=rules.get("increment", signals.get("increments", {}).get(symbol, "0.00000001")))
        for key in ("minimum_quantity", "price_increment"):
            if key in rules:
                q[key] = rules[key]
        quotes[symbol] = q
    return {"type": "frame", "id": f"henry-v2:{now}:{digest(quotes)[:16]}", "observed_at": now,
            "source": prices.get("source"), "quotes": quotes, "bars": bars,
            "liquidity_basis": "modeled: 1% of preceding closed five-minute bar volume"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "tick", "run", "report", "replay"))
    parser.add_argument("--policy", default="infra/trading/policy.demo.json",
                        help="source policy; only its symbols and fee/slippage assumptions are used")
    parser.add_argument("--root", required=True, help="Henry v2's own isolated directory")
    parser.add_argument("--source-root", help="read-only runtime root containing market/")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.source_root:
        source = Path(args.source_root).resolve()
        if root == source or root in source.parents or source in root.parents:
            parser.error("Henry v2 and source directories must be separate, non-nested paths")
    if args.command in {"tick", "run"} and not args.source_root:
        parser.error("--source-root is required")
    path = root/"henry_v2.sqlite"
    if args.command != "init" and not path.exists():
        parser.error("initialize explicitly first; no implicit fresh bankroll")
    if args.command == "init" and path.exists():
        parser.error("Henry v2 already exists; use report, never reset")
    policy = Policy.from_dict(read_object(args.policy, limit=20_000))
    if policy.mode == "live":
        parser.error("Henry v2 is virtual only")
    root.mkdir(parents=True, exist_ok=True)
    lock = (root/"henry_v2.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    book = HenryV2(path, symbols=policy.allowed_instruments, fee_bps=policy.fee_bps,
                   slippage_bps=policy.slippage_bps, epoch=time.time() if args.command == "init" else None)
    try:
        if args.command == "replay":
            print(encode(book.verify_replay()))
            return 0
        if args.command in {"init", "report"}:
            report = book.report()
            write_snapshot(root/"snapshot.json", report)
            print(encode(report))
            return 0
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        report = book.report()
        while not stop.is_set():
            if report["halt"] and not report["position"] and not report["pending"]:
                print(encode({"event": "henry_v2_halted", "reason": report["halt"], "equity": report["equity_usd"]}), flush=True)
                while not stop.wait(30):
                    pass
                return 0
            try:
                report = book.apply(capture(book, args.source_root, time.time()))
                write_snapshot(root/"snapshot.json", report)
            except IntegrityError:
                report = book.report()
                write_snapshot(root/"snapshot.json", report)
                if args.command == "tick":
                    raise
            except (ValueError, OSError, KeyError):
                if args.command == "tick":
                    raise
                print(encode({"event": "input_unavailable", "mode": "virtual_only"}), flush=True)
            if args.command == "tick":
                print(encode(report))
                return 0
            stop.wait(5)
        return 0
    finally:
        book.close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
