"""Local-only three-book experiment, consuming existing files without network clients.

Nothing imports the broker, agent worker, credentials, deployment, or model runner.
Initialization is explicit; subsequent commands cannot replenish or reset a book.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import signal
import threading
import time
from pathlib import Path

from .contracts import Policy, decimal as D, encode
from .engine import write_snapshot
from .experiment import Experiment, IntegrityError, digest
from .ipc import read_object
from .news import assess as assess_news
from .shadow_sizing import KELLY_RULES, KELLY_RULES_V2, KELLY_RULES_V3


def capture(experiment, source_root, now):
    """Capture a point-in-time opportunity stream from files already produced by Valor.

    The last CLOSED bar's volume supplies a 1% participation proxy. It is a modeled
    capacity, not observed quote depth or a claim of executable exchange liquidity.
    """
    source = Path(source_root)
    prices = read_object(source/"market"/"quotes.json", {}, limit=100_000)
    signals = read_object(source/"market"/"signals.json", {}, limit=1_000_000)
    runtime = read_object(source/"outbox"/"snapshot.json", {}, limit=1_000_000)
    news = read_object(source/"news"/"snapshot.json", {}, limit=100_000)
    state, policy = experiment.state(), experiment.policy
    if (prices.get("policy_hash") != policy.fingerprint or signals.get("policy_hash") != policy.fingerprint
            or prices.get("source") != signals.get("source")
            or not 0 <= now-prices.get("timestamp", 0) <= 30
            or not 0 <= now-signals.get("timestamp", 0) <= 600):
        raise ValueError("fresh, matching pre-existing market files are required")
    bars, quotes = {}, {}
    dynamic = bool(state.get("universe"))
    if dynamic and (prices.get("venue") != "us" or signals.get("venue") != "us"):
        raise ValueError("expanded universe requires explicit matching Alpaca US provenance")
    unavailable, history_unavailable = [], []
    for symbol in experiment.symbols(state):
        history = signals.get("histories", {}).get(symbol, [])
        if dynamic and (not history or symbol in signals.get("history_errors", {})):
            history = []
            history_unavailable.append(symbol)
        if not history or history[-1]["timestamp"]+300 > now:
            if not dynamic or history:
                raise ValueError("closed-bar history is required")
            unavailable.append(symbol)
        retained = {b["timestamp"]: b for b in state["histories"].get(symbol, [])}
        if any(b["timestamp"] in retained and retained[b["timestamp"]] != b for b in history):
            # Route through the durable reducer so corruption becomes an auditable terminal halt.
            bars[symbol] = history
        else:
            bars[symbol] = [b for b in history if b["timestamp"] not in retained]
        raw = prices.get("quotes", {}).get(symbol)
        rules = signals.get("instrument_rules", {}).get(symbol)
        if dynamic and (raw is None or not rules):
            unavailable.append(symbol)
            continue
        capacity = str(D(history[-1]["volume"])*D(".01")) if history else "0"
        quotes[symbol] = {k: raw[k] for k in ("bid", "ask", "timestamp")}
        quotes[symbol].update(buy_capacity=capacity, sell_capacity=capacity, capacity_bucket=history[-1]["timestamp"] if history else 0,
                             increment=signals.get("increments", {}).get(symbol, "0.00000001"))
        if dynamic:
            quotes[symbol].update(rules)
    supervisor = runtime.get("supervisor", {})
    runtime_current = (runtime.get("policy_hash") == policy.fingerprint and
                       0 <= now-runtime.get("timestamp", 0) <= 60)
    # The news volume contains a raw bundle, not the worker's assessed view.
    # Validate that bundle directly when present so every book sees the same
    # current integrity/freshness gate, even if supervision is unavailable.
    news_facts = assess_news(news, now) if news else runtime.get("news", {}) if runtime_current else {}
    news_usable = (news_facts.get("entry_blocked") is False
                   and 0 <= now-news_facts.get("fetched_at", 0) <= 600)
    cost = runtime.get("costs", {}).get("total_operating_estimated_usd") if runtime_current else None
    frame = {"type": "frame", "id": f"capture:{now}:{digest(quotes)[:16]}", "observed_at": now,
             "source": prices["source"], "policy_hash": policy.fingerprint,
             "strategy": experiment.identity["strategy"], "quotes": quotes, "bars": bars,
             "input_provenance": {"market_received_at": prices["timestamp"], "signals_received_at": signals["timestamp"],
                                  "runtime_received_at": runtime.get("timestamp"), "news_fetched_at": news_facts.get("fetched_at"),
                                  "execution_clock": "provider quote timestamp; receipt time never refreshes a price"},
             "liquidity_basis": "modeled: 1% of preceding closed five-minute bar volume, shared by all fills in that bar bucket per book",
             "context": {"data_entry_allowed": news_usable,
                         "supervisor_entry_allowed": runtime_current and supervisor.get("action") != "pause_entries"
                             and supervisor.get("expires_at", 0) > now,
                         "supervisor_scale": supervisor.get("scale", "1"),
                         "news_hash": news_facts.get("evidence_hash"),
                         "operational_approval_reused": False},
             "shared_operating_estimate": cost}
    if dynamic:
        frame.update(venue="us", unavailable_symbols=unavailable, history_unavailable_symbols=history_unavailable)
        if signals.get("history_integrity"):
            frame["history_cutoffs"] = {s: v.get("cutoff", 0) for s, v in signals["history_integrity"].items()}
            frame["input_provenance"]["history_integrity"] = {s: {k: v.get(k) for k in ("cutoff", "generation")}
                                                            for s, v in signals["history_integrity"].items()}
    return frame


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "tick", "run", "report", "replay", "upgrade-evidence", "expand-universe", "recover-quotes",
                                                "expand-session"))
    parser.add_argument("--policy", default="infra/trading/policy.demo.json")
    parser.add_argument("--root", required=True, help="new isolated experiment directory")
    parser.add_argument("--source-root", help="existing read-only runtime root with market/outbox/news directories")
    parser.add_argument("--strategy", help="freeze an approved strategy at initialization")
    parser.add_argument("--new-policy", help="expand-universe/expand-session: the widened paper/demo policy")
    parser.add_argument("--recovery-review", help="recover-quotes only: explicit failure-bound operator review JSON")
    parser.add_argument("--stay-running-after-completion", action="store_true",
                        help="run only: idle after the settled end state, without further observations")
    args = parser.parse_args(argv)
    if args.stay_running_after_completion and args.command != "run":
        parser.error("--stay-running-after-completion applies only to run")
    root = Path(args.root).resolve()
    if args.source_root:
        source = Path(args.source_root).resolve()
        if root == source or root in source.parents or source in root.parents:
            parser.error("experiment and source directories must be separate, non-nested paths")
    if args.command in {"tick", "run", "recover-quotes"} and not args.source_root:
        parser.error("--source-root is required; this command never starts a market feed")
    path = root/"experiment.sqlite"
    if args.command != "init" and not path.exists():
        parser.error("initialize explicitly first; no implicit fresh bankroll")
    if args.command == "init" and path.exists():
        parser.error("experiment already exists; use report, never reset")
    policy = Policy.from_dict(read_object(args.policy, limit=20_000))
    root.mkdir(parents=True, exist_ok=True)
    lock = (root/"experiment.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    experiment = Experiment(path, policy, epoch=time.time() if args.command == "init" else None, strategy=args.strategy)
    try:
        if args.command == "recover-quotes":
            if not args.recovery_review:
                parser.error("--recovery-review is required; halts never recover automatically")
            review = read_object(args.recovery_review, limit=20_000)
            market = read_object(source/"market"/"quotes.json", limit=100_000)
            report = experiment.recover_quotes(review, market, time.time())
            write_snapshot(root/"snapshot.json", report)
            print(encode(report))
            return 0
        if args.command == "expand-universe":
            if not args.new_policy:
                parser.error("--new-policy is required for an explicit universe boundary")
            from dataclasses import asdict
            new = Policy.from_dict(read_object(args.new_policy, limit=20_000))
            report = experiment.apply({"type": "universe_policy_update", "id": "universe:"+new.fingerprint,
                "observed_at": time.time(), "from_policy": policy.fingerprint,
                "new_policy": asdict(new), "rules_hash": digest(KELLY_RULES_V3)})
            write_snapshot(root/"snapshot.json", report)
            print(encode(report))
            return 0
        if args.command == "expand-session":
            if not args.new_policy:
                parser.error("--new-policy is required for an explicit session boundary")
            import copy
            from dataclasses import asdict
            new = Policy.from_dict(read_object(args.new_policy, limit=20_000))
            event = {"type": "session_policy_update", "id": "session:"+new.fingerprint, "observed_at": time.time(),
                     "from_policy": policy.fingerprint, "to_policy": new.fingerprint, "new_policy": asdict(new)}
            # Dry projection first: a malformed boundary is refused, never journaled as a halt.
            experiment._activate_session(copy.deepcopy(experiment.state()), event, event["observed_at"])
            report = experiment.apply(event)
            write_snapshot(root/"snapshot.json", report)
            print(encode(report))
            return 0
        if args.command == "upgrade-evidence":
            if experiment.state().get("evidence_policy_version") != KELLY_RULES_V2["version"]:
                experiment.apply({"type": "evidence_policy_update", "id": "evidence-policy:"+KELLY_RULES_V2["version"],
                    "observed_at": time.time(), "from_version": KELLY_RULES["version"],
                    "to_version": KELLY_RULES_V2["version"], "rules_hash": digest(KELLY_RULES_V2)})
            report = experiment.report()
            write_snapshot(root/"snapshot.json", report)
            print(encode(report))
            return 0
        if args.command == "replay":
            print(encode(experiment.verify_replay()))
            return 0
        if args.command in {"init", "report"}:
            report = experiment.report()
            write_snapshot(root/"snapshot.json", report)
            print(encode(report))
            return 0
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        report = experiment.report()
        while not stop.is_set():
            if report["halt"]:
                if args.command == "tick":
                    raise IntegrityError("experiment already stopped: "+report["halt"])
                print(encode({"event": "experiment_halted", "reason": report["halt"],
                              "last_at": report["timestamp"], "mode": "virtual_only"}), flush=True)
                while not stop.wait(30):
                    pass
                return 0
            if (report["timestamp"] >= report["evaluation_end"] and all(
                    b["positions"] == 0 and b["pending_orders"] == 0 and not b["provisional_fees"] for b in report["books"])):
                print(encode({"event": "evaluation_complete", "mode": "virtual_only"}), flush=True)
                if args.stay_running_after_completion:
                    while not stop.wait(30):
                        pass
                return 0
            try:
                report = experiment.apply(capture(experiment, args.source_root, time.time()))
                write_snapshot(root/"snapshot.json", report)
            except IntegrityError:
                report = experiment.report()
                write_snapshot(root/"snapshot.json", report)
                if args.command == "tick":
                    raise
            except (ValueError, OSError, KeyError):
                # No fabricated observations during outages. Snapshot ages visibly; future gaps are recorded.
                if args.command == "tick":
                    raise
                print(encode({"event": "input_unavailable", "mode": "virtual_only"}), flush=True)
            if args.command == "tick":
                print(encode(report))
                return 0
            stop.wait(5)
        return 0
    finally:
        experiment.close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
