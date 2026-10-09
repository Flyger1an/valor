"""Run the live (paper) trend book. Offline: reads the feed's market files read-only.

  init    --seed /henry/daily_seed.json        explicit, once
  run     --source-root /runtime               loop; applies a refreshed seed file to repair gaps
  report  [--shadow /henry/shadow.json]
  replay
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import signal
import threading
import time
from pathlib import Path

from .contracts import Policy, encode
from .engine import write_snapshot
from .henry_trend import TrendBook, IntegrityError, digest
from .ipc import read_object


def capture(book, source_root, now):
    source = Path(source_root)
    prices = read_object(source/"market"/"quotes.json", {}, limit=100_000)
    signals = read_object(source/"market"/"signals.json", {}, limit=2_000_000)
    if not 0 <= now-prices.get("timestamp", 0) <= 60:
        raise ValueError("stale market files")
    state = book.state()
    quotes, bars = {}, {}
    for sym in book.identity["symbols"]:
        raw = prices.get("quotes", {}).get(sym)
        if raw:
            quotes[sym] = {k: raw[k] for k in ("bid", "ask", "timestamp")}
        seen = state["intraday"].get(sym, {})
        newest = max((int(k) for k in seen), default=0)
        fresh = [b for b in signals.get("histories", {}).get(sym, []) if b["timestamp"] > newest and b["timestamp"]+300 <= now]
        if fresh:
            bars[sym] = fresh
    return {"type": "frame", "id": f"trend:{now}:{digest(quotes)[:12]}", "observed_at": now, "quotes": quotes, "bars": bars}


def seed_event(path, now):
    data = json.loads(Path(path).read_text())
    return {"type": "seed", "id": "seed:"+hashlib.sha256(Path(path).read_bytes()).hexdigest()[:24], "observed_at": now,
            "source": data.get("source", "unknown"), "daily": data["daily"]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("init", "run", "tick", "report", "replay"))
    p.add_argument("--policy", default="/config/henry-trend-policy.json")
    p.add_argument("--root", default="/henry")
    p.add_argument("--source-root")
    p.add_argument("--seed")
    p.add_argument("--shadow")
    args = p.parse_args(argv)
    root = Path(args.root)
    path = root/"henry_trend.sqlite"
    if args.command == "init" and path.exists():
        p.error("trend book already exists; never reset")
    if args.command != "init" and not path.exists():
        p.error("initialize explicitly first")
    if args.command in ("run", "tick") and not args.source_root:
        p.error("--source-root is required")
    policy = Policy.from_dict(read_object(args.policy, limit=20_000))
    if policy.mode == "live":
        p.error("the trend book is virtual only")
    root.mkdir(parents=True, exist_ok=True)
    lock = (root/"henry_trend.lock").open("a")
    if args.command in ("init", "run", "tick"):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    book = TrendBook(path, symbols=policy.allowed_instruments, epoch=time.time() if args.command == "init" else None)
    shadow_path = Path(args.shadow) if args.shadow else root/"shadow.json"
    seed_path = Path(args.seed) if args.seed else root/"daily_seed.json"

    def shadow():
        try:
            return json.loads(shadow_path.read_text())
        except (OSError, ValueError):
            return None

    try:
        if args.command == "replay":
            print(encode(book.verify_replay()))
            return 0
        if args.command == "init":
            if not seed_path.exists():
                p.error("a daily seed file is required: run henry_trend_feed seed first")
            report = book.apply(seed_event(seed_path, time.time()))
            write_snapshot(root/"snapshot.json", report)
            print(encode(report))
            return 0
        if args.command == "report":
            report = book.report(shadow=shadow())
            print(encode(report))
            return 0
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        seed_mtime = seed_path.stat().st_mtime if seed_path.exists() else 0
        while not stop.is_set():
            now = time.time()
            try:
                if seed_path.exists() and seed_path.stat().st_mtime > seed_mtime:  # daily refresh repairs gaps
                    book.apply(seed_event(seed_path, now))
                    seed_mtime = seed_path.stat().st_mtime
                book.apply(capture(book, args.source_root, now))
                write_snapshot(root/"snapshot.json", book.report(shadow=shadow()))
            except IntegrityError as exc:
                print(encode({"event": "integrity", "reason": str(exc)}), flush=True)
            except (ValueError, OSError, KeyError):
                print(encode({"event": "input_unavailable"}), flush=True)
            if args.command == "tick":
                return 0
            stop.wait(30)
        return 0
    finally:
        book.close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
