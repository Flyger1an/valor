"""Three fictional cash books. Append-only inputs, atomic state, no execution clients.

Orders are counterfactual IOC orders filled only by a later observed quote. No model
approval or broker receipt is manufactured. All dollar balances belong to this file.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import sqlite3
from decimal import ROUND_DOWN, ROUND_UP
from dataclasses import replace
from pathlib import Path

from .contracts import Policy, Quote, decimal as D, encode
from .shadow_sizing import KELLY_RULES, KELLY_RULES_V2, KELLY_RULES_V3, evidence_rules, recommend, usable_blocks
from .strategies import BY_VERSION, entry_signal, exit_signal, position_budget


BOOKS = ("baseline", "kelly", "henry")
RULES = {"version": "three-cash-books-v1", "starting_cash": "500", "maximum_days": 90,
         "execution": "next-observed-quote IOC; adverse slippage; supplied modeled liquidity",
         "approval": "hypothetical deterministic signal/data gate; not an operational AI approval",
         "fee_reserve_bps": "100", "settlement": "modeled at next UTC day; never actual broker fees",
         "henry": "henry-all-cash-v1: one position, up to all cash less fee reserve, no leverage or refills",
         "selection": "BTC-USD before ETH-USD; shared immutable opportunity IDs",
         "kelly": KELLY_RULES}


class IntegrityError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def day(stamp):
    return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).date().isoformat()


def day_start(stamp):
    return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def floor(value, step):
    return (D(value)/D(step)).to_integral_value(rounding=ROUND_DOWN)*D(step)


def fee(value, bps):
    return (D(value)*D(bps)/10000).quantize(D(".01"), rounding=ROUND_UP)


def new_book(name):
    return {"name": name, "policy_version": {"baseline": "current-caps-v1", "kelly": KELLY_RULES["version"],
            "henry": "henry-all-cash-v1"}[name], "cash": "500", "positions": {}, "lots": {}, "fills": {},
            "pending": {}, "seen": {}, "skips": {}, "safety_events": [], "liquidity_used": {}, "halt": "", "day": None,
            "day_start_equity": "500", "daily_halt": False, "attempts": 0, "peak": "500",
            "max_drawdown": "0", "sizing": {}}


def metrics(book, quotes):
    escrow = sum((D(f["reserve"])-D(f["fee"]) for f in book["fills"].values() if not f["settled"]), D(0))
    exposure = realized = basis = D(0)
    asset_pnl = {}
    for lot in book["lots"].values():
        buy = book["fills"][lot["buy"]]
        remaining, original = D(lot["remaining"]), D(buy["quantity"])
        cost = D(buy["value"])+D(buy["fee"])
        sold = original-remaining
        proceeds = sum((D(book["fills"][i]["value"])-D(book["fills"][i]["fee"]) for i in lot["sells"]), D(0))
        earned = proceeds-cost*sold/original
        holding_basis = cost*remaining/original
        mark = remaining*D(quotes.get(lot["instrument"], {}).get("bid", str(D(buy["price"]))))
        realized += earned
        exposure += mark
        basis += holding_basis
        asset_pnl[lot["instrument"]] = asset_pnl.get(lot["instrument"], D(0))+earned+mark-holding_basis
    equity = D(book["cash"])+escrow+exposure
    return {"cash": D(book["cash"]), "fee_escrow": escrow, "equity": equity, "exposure": exposure,
            "realized_pnl": realized, "unrealized_pnl": exposure-basis, "net_pnl": equity-500,
            "asset_pnl": asset_pnl,
            "fees": sum((D(f["fee"]) for f in book["fills"].values()), D(0)),
            "turnover": sum((D(f["value"]) for f in book["fills"].values()), D(0))}


class Experiment:
    def __init__(self, path, policy: Policy, *, epoch=None, strategy=None):
        self.path, self.policy = Path(path), policy
        if policy.mode == "live":
            raise ValueError("virtual experiment accepts paper/demo source policies only")
        if self.path.name != "experiment.sqlite":
            raise ValueError("experiment must use its own experiment.sqlite file")
        if not {"BTC-USD", "ETH-USD"} <= set(policy.allowed_instruments) or len(policy.allowed_instruments) > 64 or policy.starting_cash != D(500):
            raise ValueError("requires the $500 cash study and its original BTC/ETH instruments")
        if policy.fee_bps > D(RULES["fee_reserve_bps"]):
            raise ValueError("fee assumption exceeds fictional reserve")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS experiment_meta (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS experiment_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                observed_at REAL NOT NULL, hash TEXT NOT NULL, payload TEXT NOT NULL);
        """)
        row = self.db.execute("SELECT payload FROM experiment_meta WHERE key='identity'").fetchone()
        if row:
            self.identity = json.loads(row[0])
            if (self.state().get("universe", {}).get("policy_hash", self.identity["policy_hash"]) != policy.fingerprint
                    or replace(policy, allowed_instruments=tuple(self.identity["symbols"])).fingerprint != self.identity["policy_hash"]
                    or self.identity["rules"] != RULES
                    or (epoch is not None and epoch != self.identity["epoch"])
                    or (strategy is not None and strategy != self.identity["strategy"])):
                self.db.close()
                raise ValueError("existing experiment is immutable; no reset or policy substitution")
        else:
            if set(policy.allowed_instruments) != {"BTC-USD", "ETH-USD"}:
                self.db.close()
                raise ValueError("initialize the original study, then apply an explicit universe boundary")
            if epoch is None or not math.isfinite(epoch) or epoch <= 0:
                self.db.close()
                raise ValueError("initialization needs a shared forward epoch")
            strategy = strategy or policy.approved_strategies[0]
            if strategy not in policy.approved_strategies or strategy not in BY_VERSION:
                raise ValueError("unknown strategy")
            self.identity = {"policy_hash": policy.fingerprint, "rules": RULES, "epoch": epoch,
                             "strategy": strategy, "symbols": sorted(policy.allowed_instruments)}
            with self.db:
                self.db.execute("INSERT INTO experiment_meta VALUES ('identity',?)", (encode(self.identity),))
                self._save(self.initial_state())

    def close(self):
        self.db.close()

    def symbols(self, state=None):
        state = self.state() if state is None else state
        return state.get("universe", {}).get("symbols", self.identity["symbols"])

    def policy_hash(self, state):
        return state.get("universe", {}).get("policy_hash", self.identity["policy_hash"])

    def _required_marks(self, book, state, symbol):
        return set(book["positions"]) | {symbol} if state.get("universe") else self.symbols(state)

    def _stop_execution_price(self, state, symbol, stop):
        price = D(stop)*(1-self.policy.slippage_bps/10000)
        if state.get("universe"):
            step = D(state["quotes"][symbol]["price_increment"])
            price = (price/step).to_integral_value(rounding=ROUND_DOWN)*step
        return price

    def _activate_universe(self, state, event, now):
        from .universe import validate_expansion
        old = replace(self.policy, allowed_instruments=tuple(self.symbols(state)))
        new = Policy.from_dict(event["new_policy"])
        added = validate_expansion(old, new)
        if (event.get("from_policy") != self.policy_hash(state) or old.fingerprint != self.policy_hash(state)
                or event.get("rules_hash") != digest(KELLY_RULES_V3)
                or state.get("evidence_policy_version") not in {KELLY_RULES_V2["version"], KELLY_RULES_V3["version"]}):
            raise IntegrityError("invalid_universe_boundary")
        evidence = state["evidence"]
        boundary = {"id": event["id"], "at": now, "from_policy": old.fingerprint, "to_policy": new.fingerprint,
                    "from_version": state["evidence_policy_version"], "to_version": KELLY_RULES_V3["version"],
                    "rules_hash": event["rules_hash"], "added": added,
                    "prior_current_day": {k: copy.deepcopy(v) for k, v in evidence.items() if k != "blocks"},
                    "legacy_blocks_retained": len(evidence["blocks"]), "reclassified_blocks": 0}
        state.setdefault("universe_history", []).append(boundary)
        state["evidence_policy_history"].append(boundary)
        state["universe"] = {"version": "additive-cash-universe-v1", "symbols": list(new.allowed_instruments),
                             "policy_hash": new.fingerprint, "activated_at": now,
                             "cohort": digest({"id": event["id"], "at": now, "policy": new.fingerprint})}
        state["evidence_policy_version"] = KELLY_RULES_V3["version"]
        state["books"]["kelly"]["policy_version"] = KELLY_RULES_V3["version"]
        evidence.update(evidence_version=KELLY_RULES_V3["version"], complete=False,
                        invalid_reasons=["policy_boundary_partial_day"], coverage=self._new_coverage(state, now),
                        start_valuation=self._valuation_at(state, now))

    def _joint_recommendation(self, book, opportunities, frame, state, now):
        m = metrics(book, state["quotes"])
        held = {s: D(book["lots"][lid]["remaining"])*D(state["quotes"][s]["bid"])/m["equity"]
                for s, lid in book["positions"].items()} if m["equity"] > 0 else {}
        for s, order in book["pending"].items():
            if order["side"] == "buy" and m["equity"] > 0:
                held[s] = D(order["quantity"])*D(order["limit"])/m["equity"]
        eligible = set()
        for op in opportunities:
            symbol, q = op["instrument"], state["quotes"].get(op["instrument"])
            if (q and 0 <= now-q["timestamp"] <= 30 and symbol not in book["positions"]
                    and symbol not in book["pending"] and (D(q["ask"])-D(q["bid"]))/D(q["ask"])*10000 <= self.policy.max_spread_bps):
                eligible.add(symbol)
        return recommend(state["evidence"]["blocks"], self.symbols(state), eligible, held, now,
                         KELLY_RULES_V3["version"], state["universe"]["cohort"])

    def initial_state(self):
        return {"books": {n: new_book(n) for n in BOOKS}, "last_at": self.identity["epoch"], "frames": 0,
                "quotes": {}, "histories": {}, "halt": "", "opportunities": [], "evidence": {"blocks": [],
                "day": None, "active": {}, "start_pnl": {}, "complete": False},
                "source_cost_start": None, "shared_operating_estimate": None, "actual_shared_cost": None}

    def state(self):
        return json.loads(self.db.execute("SELECT payload FROM experiment_meta WHERE key='state'").fetchone()[0])

    def _save(self, state):
        self.db.execute("INSERT OR REPLACE INTO experiment_meta VALUES ('state',?)", (encode(state),))

    def apply(self, event):
        """Exactly-once atomic projection. Conflicting duplicates/rewinds permanently stop the experiment."""
        payload = encode(event)
        if len(payload) > 1_000_000:
            raise ValueError("oversized event")
        event_hash = digest(event)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.state()
            old = self.db.execute("SELECT hash FROM experiment_events WHERE id=?", (event.get("id"),)).fetchone()
            if old and old[0] == event_hash:
                self.db.rollback()
                return self.report(state)
            if state["halt"]:
                raise IntegrityError("experiment already stopped: "+state["halt"])
            try:
                if old:
                    raise IntegrityError("conflicting_duplicate_event")
                stamp = float(event["observed_at"])
                if not math.isfinite(stamp) or stamp < state["last_at"]:
                    raise IntegrityError("chronological_boundary_violation")
                if not isinstance(event["id"], str) or not 1 <= len(event["id"]) <= 180:
                    raise IntegrityError("invalid_event_identity")
                projected = copy.deepcopy(state)
                self._reduce(projected, event)
                projected["last_at"] = stamp
                self._check_conservation(projected)
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                # Retain the last balanced state, plus durable failure evidence. Never reset cash.
                reason = str(exc) if isinstance(exc, IntegrityError) else "invalid_input_"+type(exc).__name__
                state["halt"] = reason
                for book in state["books"].values():
                    book["safety_events"].append({"at": state["last_at"], "reason": reason})
                failure = {"type": "integrity_failure", "reason": reason, "rejected_hash": event_hash}
                self.db.execute("INSERT INTO experiment_events(id,observed_at,hash,payload) VALUES (?,?,?,?)",
                                ("failure:"+event_hash, state["last_at"], digest(failure), encode(failure)))
                self._save(state)
                self.db.commit()
                raise IntegrityError(reason) from exc
            self.db.execute("INSERT INTO experiment_events(id,observed_at,hash,payload) VALUES (?,?,?,?)",
                            (event["id"], stamp, event_hash, payload))
            self._save(projected)
            self.db.commit()
            return self.report(projected)
        except Exception:
            self.db.rollback()
            raise

    def _reduce(self, state, event):
        kind, now = event["type"], float(event["observed_at"])
        if kind == "frame":
            self._frame(state, event, now)
        elif kind == "fee_settlement":
            self._settle(state, event["book"], event["fill_id"], D(event["total_fee"]), now, "explicit_virtual_settlement")
        elif kind == "shared_cost":
            value = D(event["cumulative_usd"])
            if value < D(state["actual_shared_cost"] or 0):
                raise IntegrityError("shared_cost_cannot_decrease")
            state["actual_shared_cost"] = str(value)
        elif kind == "evidence_policy_update":
            self._activate_evidence_v2(state, event, now)
        elif kind == "universe_policy_update":
            self._activate_universe(state, event, now)
        else:
            raise IntegrityError("unsupported_event_type")
        self._settlement_status(state, now)

    def _check_conservation(self, state):
        for book in state["books"].values():
            m = metrics(book, state["quotes"])
            if m["cash"] < 0 or m["fee_escrow"] < 0 or any(D(l["remaining"]) < 0 for l in book["lots"].values()):
                raise IntegrityError("cash_or_inventory_conservation_failure")
            if abs(m["equity"]-D(500)-m["realized_pnl"]-m["unrealized_pnl"]) > D("0.000000001"):
                raise IntegrityError("equity_conservation_failure")
            peak = max(D(book["peak"]), m["equity"])
            book["peak"] = str(peak)
            book["max_drawdown"] = str(max(D(book["max_drawdown"]), (peak-m["equity"])/peak))

    def _settle(self, state, name, fid, total, now, basis):
        book = state["books"][name]
        fill = book["fills"][fid]
        if total < 0 or total > D(fill["reserve"]):
            raise IntegrityError("fee_outside_reserved_bound")
        if fill["settled"]:
            if total != D(fill["fee"]):
                raise IntegrityError("conflicting_final_fee_settlement")
            return
        adjustment = D(fill["fee"])-total
        book["cash"] = str(D(book["cash"])+D(fill["reserve"])-total)
        fill.update(fee=str(total), settled=True, settled_at=now, settlement_basis=basis)
        if name == "baseline":
            symbol, original_day, evidence = fill["instrument"], day(fill["at"]), state["evidence"]
            for block in evidence["blocks"]:
                if block["day"] == original_day:
                    block["pnl"][symbol] = str(D(block["pnl"][symbol])+adjustment)
            if evidence["day"] and original_day < evidence["day"]:
                evidence["start_pnl"][symbol] = str(D(evidence["start_pnl"].get(symbol, 0))+adjustment)

    def _settlement_status(self, state, now):
        fills = state["books"]["baseline"]["fills"].values()
        for block in state["evidence"]["blocks"]:
            settled = all(f["settled"] for f in fills if f["at"] < block["end"])
            if settled and not block["settled"]:
                block.update(settled=True, available_at=now)

    def _roll_day(self, state, now):
        if state.get("evidence_policy_version") in {KELLY_RULES_V2["version"], KELLY_RULES_V3["version"]}:
            return self._roll_day_v2(state, now)
        evidence = state["evidence"]
        m = metrics(state["books"]["baseline"], state["quotes"])
        current = day(now)
        if evidence["day"] != current:
            if evidence["day"]:
                end = dt.datetime.fromisoformat(evidence["day"]).replace(tzinfo=dt.timezone.utc).timestamp()+86400
                pnl = {s: str(m["asset_pnl"].get(s, D(0))-D(evidence["start_pnl"].get(s, 0))) for s in self.symbols(state)}
                evidence["blocks"].append({"day": evidence["day"], "start": evidence["start"], "end": end,
                    "pnl": pnl, "reference_notional": evidence["reference_notional"], "active": evidence["active"],
                    "complete": evidence["complete"] and end-state["last_at"] <= 30 and now-state["last_at"] <= 60,
                    "settled": False, "available_at": now})
            evidence.update(day=current, start=day_start(now), start_pnl={s: str(m["asset_pnl"].get(s, D(0))) for s in self.symbols(state)},
                reference_notional=str(position_budget(self.policy, m["equity"])), active={},
                complete=now-day_start(now) <= 30 and state["frames"] > 0)
            for book in state["books"].values():
                book.update(day=current, day_start_equity=str(metrics(book, state["quotes"])["equity"]), daily_halt=False, attempts=0)
        if state["frames"] and now-state["last_at"] > 60:
            evidence["complete"] = False
            for book in state["books"].values():
                book["safety_events"].append({"at": now, "reason": "sampling_gap_no_invented_execution"})

    def _valuation_at(self, state, at):
        """Cash is known without a market mark. Only held baseline assets need one."""
        held = {}
        for symbol in state["books"]["baseline"]["positions"]:
            quote = state["quotes"].get(symbol)
            age = at-quote["timestamp"] if quote else None
            held[symbol] = {"quote_at": quote["timestamp"] if quote else None,
                            "age_seconds": age, "valid": age is not None and 0 <= age <= 30}
        return {"at": at, "held_assets": held, "valid": all(v["valid"] for v in held.values())}

    def _new_coverage(self, state, now):
        return {"since": now, "observations": 0, "first_observation_at": None, "last_observation_at": None,
                "fresh_pair_observations": 0, "stale_by_asset": dict.fromkeys(self.symbols(state), 0),
                "held_stale_by_asset": dict.fromkeys(self.symbols(state), 0),
                "maximum_quote_age_seconds": dict.fromkeys(self.symbols(state), 0),
                "news_blocked_observations": 0, "supervisor_blocked_observations": 0,
                "max_gap_seconds": 0, "gaps_over_60_seconds": 0}

    def _activate_evidence_v2(self, state, event, now):
        if (state.get("evidence_policy_version", KELLY_RULES["version"]) != KELLY_RULES["version"]
                or event.get("from_version") != KELLY_RULES["version"]
                or event.get("to_version") != KELLY_RULES_V2["version"]
                or event.get("rules_hash") != digest(KELLY_RULES_V2)):
            raise IntegrityError("invalid_evidence_policy_boundary")
        evidence = state["evidence"]
        state["evidence_policy_history"] = [{"id": event["id"], "at": now,
            "from_version": KELLY_RULES["version"], "to_version": KELLY_RULES_V2["version"],
            "rules_hash": event["rules_hash"], "prior_current_day": {k: copy.deepcopy(v) for k, v in evidence.items() if k != "blocks"},
            "legacy_blocks_retained": len(evidence["blocks"]), "reclassified_blocks": 0}]
        state["evidence_policy_version"] = KELLY_RULES_V2["version"]
        state["evidence_last_observation_at"] = state["last_at"] if state["frames"] else None
        state["books"]["kelly"]["policy_version"] = KELLY_RULES_V2["version"]
        evidence.update(evidence_version=KELLY_RULES_V2["version"], complete=False,
                        invalid_reasons=["policy_boundary_partial_day"], coverage=self._new_coverage(state, now),
                        start_valuation=self._valuation_at(state, now))

    def _roll_day_v2(self, state, now):
        version = state["evidence_policy_version"]
        evidence, current = state["evidence"], day(now)
        previous = state.get("evidence_last_observation_at")
        gap = now-previous if previous is not None else None
        if evidence["day"] != current:
            m = metrics(state["books"]["baseline"], state["quotes"])
            if evidence["day"]:
                end = dt.datetime.fromisoformat(evidence["day"]).replace(tzinfo=dt.timezone.utc).timestamp()+86400
                valuation = self._valuation_at(state, end)
                reasons = list(evidence["invalid_reasons"])
                if previous is None or not 0 <= end-previous <= 30:
                    reasons.append("missing_closing_observation")
                if gap is None or gap > 60:
                    reasons.append("observation_gap")
                if not valuation["valid"]:
                    reasons.append("unpriced_held_inventory_at_end")
                evidence["blocks"].append({"day": evidence["day"], "start": evidence["start"], "end": end,
                    "pnl": {s: str(m["asset_pnl"].get(s, D(0))-D(evidence["start_pnl"].get(s, 0))) for s in self.symbols(state)},
                    "reference_notional": evidence["reference_notional"], "active": evidence["active"],
                    "complete": evidence["complete"] and not reasons, "settled": False, "available_at": now,
                    "evidence_version": version, "invalid_reasons": sorted(set(reasons)),
                    "coverage": evidence["coverage"], "start_valuation": evidence["start_valuation"], "end_valuation": valuation})
                if state.get("universe"):
                    evidence["blocks"][-1]["cohort"] = state["universe"]["cohort"]
            valuation = self._valuation_at(state, day_start(now))
            reasons = []
            if day_start(now) <= day_start(state["evidence_policy_history"][-1]["at"]):
                reasons.append("policy_boundary_partial_day")
            if now-day_start(now) > 30 or not state["frames"]:
                reasons.append("partial_day")
            if not valuation["valid"]:
                reasons.append("unpriced_held_inventory_at_start")
            evidence.update(day=current, start=day_start(now), start_pnl={s: str(m["asset_pnl"].get(s, D(0))) for s in self.symbols(state)},
                reference_notional=str(position_budget(self.policy, m["equity"])), active={}, complete=not reasons,
                invalid_reasons=reasons, evidence_version=version,
                coverage=self._new_coverage(state, now), start_valuation=valuation)
            for book in state["books"].values():
                book.update(day=current, day_start_equity=str(metrics(book, state["quotes"])["equity"]), daily_halt=False, attempts=0)
        if gap is not None:
            coverage = evidence["coverage"]
            coverage["max_gap_seconds"] = max(coverage["max_gap_seconds"], gap)
            if gap > 60:
                coverage["gaps_over_60_seconds"] += 1
                evidence["complete"] = False
                if "observation_gap" not in evidence["invalid_reasons"]:
                    evidence["invalid_reasons"].append("observation_gap")
                for book in state["books"].values():
                    book["safety_events"].append({"at": now, "reason": "sampling_gap_no_invented_execution"})

    def _record_coverage(self, state, frame, now):
        coverage = state["evidence"]["coverage"]
        coverage["observations"] += 1
        coverage["first_observation_at"] = coverage["first_observation_at"] or now
        coverage["last_observation_at"] = now
        fresh = []
        for symbol in self.symbols(state):
            q = state["quotes"].get(symbol)
            age = now-q["timestamp"] if q else None
            usable = age is not None and 0 <= age <= 30
            fresh.append(usable)
            if age is not None:
                coverage["maximum_quote_age_seconds"][symbol] = max(coverage["maximum_quote_age_seconds"][symbol], age)
            if not usable:
                coverage["stale_by_asset"][symbol] += 1
                if symbol in state["books"]["baseline"]["positions"]:
                    coverage["held_stale_by_asset"][symbol] += 1
        coverage["fresh_pair_observations"] += int(all(fresh))
        coverage["news_blocked_observations"] += int(not frame["context"]["data_entry_allowed"])
        coverage["supervisor_blocked_observations"] += int(not frame["context"]["supervisor_entry_allowed"])
        state["evidence_last_observation_at"] = now
        state["latest_input_provenance"] = frame.get("input_provenance", {})

    def _frame(self, state, frame, now):
        if any(type(frame["context"][key]) is not bool for key in ("data_entry_allowed", "supervisor_entry_allowed")):
            raise IntegrityError("invalid_boolean_entry_gate")
        if frame["policy_hash"] != self.policy_hash(state) or frame["strategy"] != self.identity["strategy"]:
            raise IntegrityError("source_policy_or_strategy_changed")
        if frame["source"] not in {"alpaca", "coinbase_public", "test_fixture"}:
            raise IntegrityError("unrecognized_market_source")
        if state.get("source") and state["source"] != frame["source"]:
            raise IntegrityError("market_source_changed")
        if state.get("universe") and frame["source"] == "alpaca" and frame.get("venue") != "us":
            raise IntegrityError("universe_requires_same_venue_provenance")
        if state.get("universe"):
            missing = frame.get("history_unavailable_symbols", [])
            if not isinstance(missing, list) or any(s not in self.symbols(state) for s in missing):
                raise IntegrityError("invalid_history_availability")
            state["history_unavailable_symbols"] = missing
        state["source"] = frame["source"]
        self._roll_day(state, now)
        for symbol, raw in frame["quotes"].items():
            if symbol not in self.symbols(state):
                raise IntegrityError("unexpected_instrument")
            q = Quote(symbol, raw["bid"], raw["ask"], raw["timestamp"])
            previous = state["quotes"].get(symbol)
            if previous and q.timestamp == previous["timestamp"] and any(D(raw[k]) != D(previous[k]) for k in ("bid", "ask")):
                raise IntegrityError("same_timestamp_quote_revision")
            if q.timestamp > now or q.timestamp < state["quotes"].get(symbol, {}).get("timestamp", 0):
                raise IntegrityError("future_or_rewound_quote")
            for key in ("buy_capacity", "sell_capacity", "increment"):
                if D(raw[key]) < 0 or key == "increment" and D(raw[key]) == 0:
                    raise IntegrityError("invalid_execution_capacity")
            if state.get("universe") and (D(raw.get("minimum_quantity", 0)) <= 0 or D(raw.get("price_increment", 0)) <= 0):
                raise IntegrityError("missing_catalog_order_rules")
            state["quotes"][symbol] = raw
        for symbol, incoming in frame.get("bars", {}).items():
            if symbol not in self.symbols(state):
                raise IntegrityError("unexpected_bar_instrument")
            bars = {b["timestamp"]: b for b in state["histories"].get(symbol, [])}
            for bar in incoming:
                t = float(bar["timestamp"])
                lo, hi, op, close = (D(bar[k]) for k in ("low", "high", "open", "close"))
                if t % 300 or t+300 > now or not 0 < lo <= min(op, close) <= max(op, close) <= hi or D(bar["volume"]) < 0:
                    raise IntegrityError("invalid_or_future_closed_bar")
                if t in bars and bars[t] != bar:
                    raise IntegrityError("closed_bar_revision")
                bars[t] = bar
            state["histories"][symbol] = [bars[t] for t in sorted(bars)[-400:]]
        fresh = all(s in state["quotes"] and 0 <= now-state["quotes"][s]["timestamp"] <= 30 for s in self.symbols(state))
        if state.get("evidence_policy_version") in {KELLY_RULES_V2["version"], KELLY_RULES_V3["version"]}:
            self._record_coverage(state, frame, now)
        elif not fresh:
            state["evidence"]["complete"] = False
        # Modeled fees settle once. External broker accounts/activities never enter these books.
        for name, book in state["books"].items():
            for fid, f in list(book["fills"].items()):
                if not f["settled"] and day(f["at"]) < day(now):
                    self._settle(state, name, fid, D(f["fee"]), now, "modeled_not_broker_actual")
            self._execute_pending(book, frame, state, now)
        self._settlement_status(state, now)
        opportunities = []
        spec = BY_VERSION[self.identity["strategy"]]
        for symbol in self.symbols(state):
            if symbol in state.get("history_unavailable_symbols", []):
                continue
            bars = state["histories"].get(symbol, [])
            if bars and 0 <= now-bars[-1]["timestamp"]-300 <= 600 and entry_signal(spec, bars):
                opportunities.append({"id": f"{spec.version}:{symbol}:{int(bars[-1]['timestamp'])}",
                    "instrument": symbol, "bar_timestamp": bars[-1]["timestamp"], "observed_at": now,
                    "strategy": spec.version, "virtual_approval": RULES["approval"], "decisions": {}})
        for name, book in state["books"].items():
            self._exits(book, state, now)
            if state.get("universe") and name == "kelly":
                book["joint_sizing"] = self._joint_recommendation(book, opportunities, frame, state, now)
            for op in opportunities:
                if op["id"] in book["seen"]:
                    op["decisions"][name] = "already_observed"
                    continue
                book["seen"][op["id"]] = now
                reason = self._entry(book, op, opportunities, frame, state, now)
                op["decisions"][name] = reason
                if reason != "queued_virtual_order":
                    book["skips"][reason] = book["skips"].get(reason, 0)+1
            if name == "henry" and not book["positions"] and not book["pending"] and not book["halt"]:
                spendable = D(book["cash"])+sum((D(f["reserve"])-D(f["fee"]) for f in book["fills"].values() if not f["settled"]), D(0))
                if spendable < D(10)*(1+D(RULES["fee_reserve_bps"])/10000)+D(".01"):
                    book["halt"] = "bankroll_no_longer_executable"
                    book["safety_events"].append({"at": now, "reason": book["halt"]})
        new_opportunities = [op for op in opportunities if any(v != "already_observed" for v in op["decisions"].values())]
        state["opportunities"] = (state["opportunities"]+new_opportunities)[-100:]
        cost = frame.get("shared_operating_estimate")
        if cost is not None:
            cost = D(cost)
            if state["source_cost_start"] is None:
                state["source_cost_start"] = str(cost)
            difference = cost-D(state["source_cost_start"])
            if difference < D(state["shared_operating_estimate"] or 0):
                raise IntegrityError("shared_cost_source_reset")
            state["shared_operating_estimate"] = str(difference)
        state["frames"] += 1

    def _execute_pending(self, book, frame, state, now):
        for symbol, order in list(book["pending"].items()):
            raw = state["quotes"].get(symbol)
            if not raw or raw["timestamp"] <= order["created"]:
                if now-order["created"] > 30:
                    del book["pending"][symbol]
                    book["skips"]["unfilled_expired"] = book["skips"].get("unfilled_expired", 0)+1
                continue
            del book["pending"][symbol]  # IOC remainder never silently becomes a new order
            if now-raw["timestamp"] > 30 or now-order["created"] > 30:
                book["skips"]["unfilled_expired"] = book["skips"].get("unfilled_expired", 0)+1
                continue
            buy = order["side"] == "buy"
            if buy:
                reason = self._pending_gate(book, order, state, frame, now, symbol)
                if reason:
                    book["skips"][reason] = book["skips"].get(reason, 0)+1
                    continue
            price = D(raw["ask"] if buy else raw["bid"])*(1+(1 if buy else -1)*self.policy.slippage_bps/10000)
            if state.get("universe"):
                step = D(raw["price_increment"])
                price = (price/step).to_integral_value(rounding=ROUND_UP if buy else ROUND_DOWN)*step
            bucket = str(raw.get("capacity_bucket", raw["timestamp"]))
            used = book["liquidity_used"].get(symbol, {})
            consumed = D(used["quantity"]) if used.get("bucket") == bucket else D(0)
            available = max(D(0), D(raw["buy_capacity" if buy else "sell_capacity"])-consumed)
            quantity = floor(min(D(order["quantity"]), available), raw["increment"])
            if quantity <= 0 or buy and price > D(order["limit"]):
                book["skips"]["unfilled_price_or_liquidity"] = book["skips"].get("unfilled_price_or_liquidity", 0)+1
                continue
            value, fid = quantity*price, order["id"]+":fill"
            reserve = fee(value, RULES["fee_reserve_bps"])
            if buy and value+reserve > D(book["cash"]):
                raise IntegrityError("unfunded_virtual_order")
            if not buy and value < reserve:
                book["safety_events"].append({"at": now, "reason": "non_executable_dust"})
                if book["name"] == "henry":
                    book["halt"] = "non_executable_dust_position"
                continue
            f = {"id": fid, "instrument": symbol, "side": order["side"], "quantity": str(quantity),
                 "price": str(price), "value": str(value), "fee": str(fee(value, self.policy.fee_bps)),
                 "reserve": str(reserve), "settled": False, "at": now, "decision_at": order["created"],
                 "reason": order["reason"], "liquidity_basis": frame["liquidity_basis"],
                 "partial": quantity < D(order["quantity"])}
            book["fills"][fid] = f
            book["liquidity_used"][symbol] = {"bucket": bucket, "quantity": str(consumed+quantity)}
            if buy:
                book["cash"] = str(D(book["cash"])-value-reserve)
                lot = {"id": fid, "buy": fid, "instrument": symbol, "remaining": str(quantity),
                       "stop": order["stop"], "opened": now, "sells": []}
                book["lots"][fid] = lot
                book["positions"][symbol] = fid
                if book["name"] == "baseline":
                    state["evidence"]["active"][symbol] = True
            else:
                lot = book["lots"][book["positions"][symbol]]
                lot["remaining"] = str(D(lot["remaining"])-quantity)
                lot["sells"].append(fid)
                book["cash"] = str(D(book["cash"])+value-reserve)
                if D(lot["remaining"]) == 0:
                    del book["positions"][symbol]

    def _pending_gate(self, book, order, state, frame, now, symbol):
        """Repeat deterministic limits at the later executable quote; never resize an old decision."""
        p, utc = self.policy, dt.datetime.fromtimestamp(now, dt.timezone.utc)
        if (not frame["context"]["data_entry_allowed"] or utc.hour not in p.trading_hours_utc
                or utc.weekday() not in p.trading_weekdays_utc or now >= self.identity["epoch"]+90*86400):
            return "entry_gate_changed_before_fill"
        if book["halt"] or book["daily_halt"]:
            return "book_halted_before_fill"
        if symbol in state.get("history_unavailable_symbols", []):
            return "history_unavailable_before_fill"
        if any(s not in state["quotes"] or not 0 <= now-state["quotes"][s]["timestamp"] <= 30 for s in self._required_marks(book, state, symbol)):
            return "stale_portfolio_mark_before_fill"
        q = state["quotes"][symbol]
        if (D(q["ask"])-D(q["bid"]))/D(q["ask"])*10000 > p.max_spread_bps:
            return "spread_changed_before_fill"
        if book["name"] == "henry":
            return None
        if not frame["context"]["supervisor_entry_allowed"]:
            return "supervision_changed_before_fill"
        m, scale = metrics(book, state["quotes"]), D(frame["context"]["supervisor_scale"])
        if not 0 < scale <= 1:
            raise IntegrityError("invalid_supervisor_scale")
        notional = D(order["quantity"])*D(order["limit"])
        total = position_budget(p, m["equity"])*p.max_total_notional/p.max_position_notional
        if notional > position_budget(p, m["equity"], scale) or m["exposure"]+notional > total:
            return "exposure_changed_before_fill"
        risk = D(order["planned_loss"])
        remaining = p.daily_loss_limit*D(book["day_start_equity"])/500-max(D(0), D(book["day_start_equity"])-m["equity"])
        held = sum((max(D(0), D(state["quotes"][s]["bid"])-self._stop_execution_price(state, s, book["lots"][lid]["stop"])*(1-p.fee_bps/10000))*D(book["lots"][lid]["remaining"]) for s, lid in book["positions"].items()), D(0))
        if risk > p.max_loss_per_trade*m["equity"]/500*scale or risk+held > remaining:
            return "loss_budget_changed_before_fill"
        return None

    def _exits(self, book, state, now):
        m = metrics(book, state["quotes"])
        if book["name"] != "henry" and D(book["day_start_equity"])-m["equity"] >= self.policy.daily_loss_limit*D(book["day_start_equity"])/500:
            if not book["daily_halt"]:
                book["safety_events"].append({"at": now, "reason": "daily_loss_limit"})
            book["daily_halt"] = True
        if book["name"] != "henry" and D(book["peak"])-m["equity"] >= self.policy.max_drawdown*D(book["peak"])/500:
            book["halt"] = "drawdown_limit"
        spec = BY_VERSION[self.identity["strategy"]]
        for symbol, lid in list(book["positions"].items()):
            if symbol in book["pending"] or book["name"] == "henry" and book["halt"]:
                continue
            q = state["quotes"].get(symbol)
            if not q or not 0 <= now-q["timestamp"] <= 30:
                continue
            lot, bars = book["lots"][lid], state["histories"].get(symbol, [])
            buy = book["fills"][lot["buy"]]
            cost = (D(buy["value"])+D(buy["fee"]))/D(buy["quantity"])
            stop = D(q["bid"]) <= D(lot["stop"])
            expiry = now >= self.identity["epoch"]+90*86400
            stale_bars = (symbol in state.get("history_unavailable_symbols", []) or
                          not bars or not 0 <= now-bars[-1]["timestamp"]-300 <= 600)
            ordinary = (D(q["bid"]) >= cost*(1+D(spec.target_fraction)) or now-lot["opened"] >= spec.max_hold_bars*300
                        or not stale_bars and exit_signal(spec, bars))
            if stop or book["daily_halt"] or book["halt"] or expiry or ordinary:
                reason = "protective_exit" if stop or book["daily_halt"] or book["halt"] else "study_complete" if expiry else "strategy_exit"
                book["pending"][symbol] = {"id": f"{book['name']}:{lid}:exit:{now}", "side": "sell", "created": now,
                    "quantity": lot["remaining"], "reason": reason}

    def _entry(self, book, op, opportunities, frame, state, now):
        p, symbol = self.policy, op["instrument"]
        if book["halt"] or book["daily_halt"]:
            return "book_halted"
        if now >= self.identity["epoch"]+90*86400:
            return "evaluation_complete"
        if symbol in book["positions"] or symbol in book["pending"]:
            return "position_or_order_already_open"
        if book["name"] == "henry" and (book["positions"] or book["pending"]):
            return "henry_one_concentrated_position"
        utc = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        if utc.hour not in p.trading_hours_utc or utc.weekday() not in p.trading_weekdays_utc:
            return "outside_shared_entry_window"
        if not frame["context"]["data_entry_allowed"]:
            return "shared_data_or_news_gate"
        if symbol in state.get("history_unavailable_symbols", []):
            return "history_unavailable"
        q = state["quotes"].get(symbol)
        if not q or any(s not in state["quotes"] or not 0 <= now-state["quotes"][s]["timestamp"] <= 30 for s in self._required_marks(book, state, symbol)):
            return "stale_quote"
        bid, ask = D(q["bid"]), D(q["ask"])
        if (ask-bid)/ask*10000 > p.max_spread_bps:
            return "spread_limit"
        scale = D(frame["context"]["supervisor_scale"])
        if not 0 < scale <= 1:
            raise IntegrityError("invalid_supervisor_scale")
        if book["name"] != "henry" and not frame["context"]["supervisor_entry_allowed"]:
            return "supervisor_paused_or_expired"
        m = metrics(book, state["quotes"])
        reserve_bps = D(RULES["fee_reserve_bps"])
        committed = sum((D(o["quantity"])*D(o["limit"])*(1+reserve_bps/10000)+D(".01")
                         for o in book["pending"].values() if o["side"] == "buy"), D(0))
        free_cash = max(D(0), D(book["cash"])-committed-D(".01"))
        budget = free_cash/(1+reserve_bps/10000)
        price = ask*(1+p.slippage_bps/10000)
        if state.get("universe"):
            step = D(q["price_increment"])
            price = (price/step).to_integral_value(rounding=ROUND_DOWN)*step
            if price < ask:
                return "price_increment_exceeds_slippage_limit"
        stop = bid*(1-D(BY_VERSION[self.identity["strategy"]].stop_fraction))
        stop_price = self._stop_execution_price(state, symbol, stop)
        sizing = {"raw_notional": str(budget), "fractional_notional": str(budget), "reason": "all_cash"}
        if book["name"] != "henry":
            if book["attempts"] >= p.max_trades_per_day:
                return "daily_order_limit"
            cap = position_budget(p, m["equity"], scale)
            total_cap = position_budget(p, m["equity"])*p.max_total_notional/p.max_position_notional
            pending_value = sum((D(o["quantity"])*D(o["limit"]) for o in book["pending"].values() if o["side"] == "buy"), D(0))
            budget = min(budget, cap, max(D(0), total_cap-m["exposure"]-pending_value))
            if book["name"] == "kelly":
                held = {s: D(book["lots"][lid]["remaining"])*D(state["quotes"][s]["bid"])/m["equity"] for s, lid in book["positions"].items()}
                # Pending allocations consume cash/exposure and are held fixed for the joint recommendation.
                for s, order in book["pending"].items():
                    if order["side"] == "buy":
                        held[s] = D(order["quantity"])*D(order["limit"])/m["equity"]
                sizing = copy.deepcopy(book["joint_sizing"]) if state.get("universe") else recommend(state["evidence"]["blocks"], self.symbols(state),
                                   {o["instrument"] for o in opportunities}, held, now,
                                   state.get("evidence_policy_version", KELLY_RULES["version"]))
                sizing.update(raw_notional=str(D(str(sizing["raw_fraction"][symbol]))*m["equity"]),
                              fractional_notional=str(D(str(sizing["fractional_fraction"][symbol]))*m["equity"]))
                budget = min(budget, D(sizing["fractional_notional"]))
            else:
                sizing = {"raw_notional": str(cap), "fractional_notional": str(cap), "reason": "current_policy_caps"}
            loss_rate = (price-stop_price)/price + p.fee_bps/10000*(1+stop_price/price)
            loss_cap = p.max_loss_per_trade*max(D(0), m["equity"])/500*scale
            remaining = p.daily_loss_limit*D(book["day_start_equity"])/500-max(D(0), D(book["day_start_equity"])-m["equity"])
            held_risk = sum((max(D(0), D(state["quotes"][s]["bid"])-self._stop_execution_price(state, s, book["lots"][lid]["stop"])*(1-p.fee_bps/10000))*D(book["lots"][lid]["remaining"]) for s, lid in book["positions"].items()), D(0))
            pending_risk = sum((D(o.get("planned_loss", "0")) for o in book["pending"].values() if o["side"] == "buy"), D(0))
            budget = min(budget, loss_cap/loss_rate, max(D(0), remaining-held_risk-pending_risk)/loss_rate)
        quantity = floor(max(D(0), budget)/price, q["increment"])
        notional = quantity*price
        sizing.update(capped_notional=str(notional), supervisor_scale=str(scale), at=now, opportunity_id=op["id"])
        book["sizing"][symbol] = sizing
        if notional < 10 or quantity < D(q.get("minimum_quantity", 0)):
            return sizing["reason"] if book["name"] == "kelly" and budget == 0 else "below_minimum_no_round_up"
        book["attempts"] += 1
        planned = ((price-stop_price)*quantity
                   +fee(notional, p.fee_bps)+fee(stop_price*quantity, p.fee_bps))
        if book["name"] != "henry" and (planned > loss_cap or planned+held_risk+pending_risk > remaining):
            return "rounded_fees_exceed_loss_budget"
        book["pending"][symbol] = {"id": book["name"]+":"+op["id"], "side": "buy", "created": now,
            "quantity": str(quantity), "limit": str(price), "stop": str(stop), "planned_loss": str(planned),
            "reason": "shared_strategy_signal"}
        return "queued_virtual_order"

    def report(self, state=None):
        state = self.state() if state is None else state
        version = state.get("evidence_policy_version", KELLY_RULES["version"])
        evidence, coverage = state["evidence"], state["evidence"].get("coverage")
        cohort_id = state.get("universe", {}).get("cohort")
        cohort = [b for b in evidence["blocks"] if b.get("evidence_version", KELLY_RULES["version"]) == version
                  and (cohort_id is None or b.get("cohort") == cohort_id)]
        history = state.get("evidence_policy_history", [])
        first_full_day = day_start(history[-1]["at"])+86400 if history else None
        expected_days = max(0, int((day_start(state["last_at"])-first_full_day)/86400)) if first_full_day else None
        observed_days = sum(b["start"] >= first_full_day and b["end"] <= state["last_at"] for b in cohort) if first_full_day else None
        result = {"schema_version": 1, "mode": "virtual_only", "identity_hash": digest(self.identity),
                  "policy_hash": self.policy_hash(state), "epoch": self.identity["epoch"],
                  "evaluation_end": self.identity["epoch"]+90*86400, "timestamp": state["last_at"],
                  "strategy": self.identity["strategy"], "rules": RULES, "frames": state["frames"],
                  "market_source": state.get("source", "not_started"),
                  "halt": state["halt"], "approval_basis": RULES["approval"],
                  "marks_fresh": bool(state["frames"]) and all(s in state["quotes"] and
                    0 <= state["last_at"]-state["quotes"][s]["timestamp"] <= 30 for s in self.symbols(state)),
                  "shared_operating_estimate": state["shared_operating_estimate"],
                  "actual_shared_operating_cost": state["actual_shared_cost"],
                  "incremental_model_api_calls": 0, "operating_cost_allocation": "one third for comparison only; never deducted from trading cash",
                  "evidence_blocks": len(state["evidence"]["blocks"]),
                  "usable_evidence_blocks": len(usable_blocks(evidence["blocks"], state["last_at"], version, cohort_id)),
                  "active_evidence_policy": evidence_rules(version),
                  "evidence_policy_history": history,
                  "evidence_quality": {"day": evidence["day"], "complete_so_far": evidence["complete"],
                      "invalid_reasons": evidence.get("invalid_reasons", []), "coverage": coverage,
                      "fresh_pair_observation_fraction": coverage["fresh_pair_observations"]/coverage["observations"] if coverage and coverage["observations"] else None,
                      "baseline_valuation_now": self._valuation_at(state, state["last_at"]),
                      "latest_input_provenance": state.get("latest_input_provenance", {}),
                      "first_full_utc_day_start": first_full_day, "expected_completed_full_days": expected_days,
                      "unobserved_full_days": max(0, expected_days-observed_days) if expected_days is not None else None,
                      "current_version_day_records": len(cohort), "incomplete_day_records": sum(not b["complete"] for b in cohort),
                      "unsettled_day_records": sum(b["complete"] and not b["settled"] for b in cohort),
                      "legacy_blocks_retained": len(evidence["blocks"])-len(cohort)},
                  "books": [], "recent_opportunities": state["opportunities"][-12:]}
        if state.get("universe"):
            result.update(symbols=self.symbols(state), universe=state["universe"], universe_history=state["universe_history"],
                          selection="lexical currently eligible symbols; no future ranking")
            required = {s for b in state["books"].values() for s in b["positions"]}
            result["marks_fresh"] = bool(state["frames"]) and all(s in state["quotes"] and
                0 <= state["last_at"]-state["quotes"][s]["timestamp"] <= 30 for s in required)
            result["quote_status"] = {s: {"history_available": bool(state["histories"].get(s)) and s not in state.get("history_unavailable_symbols", []),
                "age_seconds": state["last_at"]-state["quotes"][s]["timestamp"] if s in state["quotes"] else None,
                "fresh": s in state["quotes"] and 0 <= state["last_at"]-state["quotes"][s]["timestamp"] <= 30}
                for s in self.symbols(state)}
        for name in BOOKS:
            book = state["books"][name]
            m = metrics(book, state["quotes"])
            shared = D(state["shared_operating_estimate"])/3 if state["shared_operating_estimate"] is not None else None
            result["books"].append({"name": name, "policy_version": book["policy_version"], "starting_cash": "500",
                **{k: str(v) for k, v in m.items() if k != "asset_pnl"}, "drawdown": book["max_drawdown"],
                "after_allocated_operating_estimate": str(m["net_pnl"]-shared) if shared is not None else None,
                "halt": book["halt"], "daily_halt": book["daily_halt"], "positions": len(book["positions"]),
                "pending_orders": len(book["pending"]), "fills": len(book["fills"]),
                "closed_trades": sum(D(l["remaining"]) == 0 for l in book["lots"].values()),
                "provisional_fees": any(not f["settled"] for f in book["fills"].values()),
                "skips": book["skips"], "safety_events": book["safety_events"][-20:], "sizing": book["sizing"]})
        return result

    def verify_replay(self):
        """Replay into memory only; an existing experiment can never be reset by this command."""
        state = self.initial_state()
        for row in self.db.execute("SELECT * FROM experiment_events ORDER BY seq"):
            event = json.loads(row["payload"])
            if digest(event) != row["hash"]:
                raise IntegrityError("journal_hash_mismatch")
            if event["type"] == "integrity_failure":
                state["halt"] = event["reason"]
                for book in state["books"].values():
                    book["safety_events"].append({"at": state["last_at"], "reason": event["reason"]})
                continue
            self._reduce(state, event)
            state["last_at"] = float(event["observed_at"])
            self._check_conservation(state)
        if digest(state) != digest(self.state()):
            raise IntegrityError("replay_state_mismatch")
        return {"verified": True, "events": self.db.execute("SELECT count(*) FROM experiment_events").fetchone()[0],
                "state_hash": digest(state), "external_actions": 0}
