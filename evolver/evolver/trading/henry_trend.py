"""Henry, live (paper): the frozen daily 50-day trend rule that passed the lab and the battery.

Once a day, at the UTC daily close, the rule decides for each coin, using henry_lab.simulate,
the exact code that was tested, run on the coin's daily bars:
  - hold while price is above a rising 50-day average; exit when that breaks
  - alts only enter while BTC is above its own 100-day average
  - each coin is one equal sleeve of the book, sized to a 2.5% daily-volatility target, never levered
  - a 3 ATR protective stop trails each position and is watched on live quotes all day
Daily bars are built from the feed's closed 5-minute bars, after an initial seed of daily history.

Paper fills at the next observed quote with adverse slippage and 25 bps fees. Nothing here can place
a real order.

Kill switches, declared before the first trade:
  - equity 35% below its peak: liquidate and halt
  - live decisions disagreeing with the Binance shadow replay for 3 days running: flagged for review
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sqlite3
from pathlib import Path

from . import henry_lab as L
from .contracts import encode

RULE = {"family": "ma_trend", "tf": "1d", "ma": 50, "side": "long", "btc_filter": True}
RULE["id"] = L.config_id(RULE)
TREND_RULES = {
    "version": "henry-trend-v1", "rule": RULE["id"], "starting_cash": 500.0, "fee_bps": 25.0, "slippage_bps": 5.0,
    "decision": "UTC daily close, built from the feed's closed 5-minute bars",
    "sizing": "equal sleeves (equity / coins), each at the lab's vol-targeted fraction, no leverage",
    "kill_switches": {"max_drawdown_pct": 35.0, "shadow_divergence_days": 3},
    "evidence": "lab one-shot PASS on 2017-21; robustness battery PASS (Bitstamp 2011-17, 30 unseen coins, stress)",
}
DAY = 86400
QUOTE_MAX_AGE = 60
MIN_DAILY = RULE["ma"]+7


class IntegrityError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def day_of(t):
    return int(t-t % DAY)


class TrendBook:
    def __init__(self, path, *, symbols=None, epoch=None):
        self.path = Path(path)
        if self.path.name != "henry_trend.sqlite":
            raise ValueError("the trend book uses its own henry_trend.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.executescript("""PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                observed_at REAL NOT NULL, hash TEXT NOT NULL, payload TEXT NOT NULL);""")
        row = self.db.execute("SELECT payload FROM meta WHERE key='identity'").fetchone()
        if row:
            self.identity = json.loads(row[0])
            if self.identity["rules"] != TREND_RULES:
                self.db.close()
                raise ValueError("existing trend journal is immutable; new rules need a new root")
            return
        if not symbols or epoch is None:
            self.db.close()
            raise ValueError("initialization needs the symbols and an epoch")
        self.identity = {"rules": TREND_RULES, "epoch": epoch, "symbols": sorted(symbols)}
        with self.db:
            self.db.execute("INSERT INTO meta VALUES ('identity',?)", (encode(self.identity),))
            self._save(self.initial_state())

    def close(self):
        self.db.close()

    def initial_state(self):
        cash = TREND_RULES["starting_cash"]
        return {"cash": cash, "positions": {}, "pending": [], "trades": [], "fees": 0.0, "fills": 0,
                "daily": {}, "daily_sources": {}, "intraday": {}, "quotes": {}, "decisions": [], "last_decided_day": None,
                "equity_curve": [], "peak": cash, "max_drawdown_pct": 0.0, "halt": "", "started_day": None,
                "start_closes": {}, "frames": 0, "last_at": self.identity["epoch"], "skips": {}}

    def state(self):
        return json.loads(self.db.execute("SELECT payload FROM meta WHERE key='state'").fetchone()[0])

    def _save(self, state):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('state',?)", (encode(state),))

    def apply(self, event):
        h = digest(event)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.state()
            old = self.db.execute("SELECT hash FROM events WHERE id=?", (event.get("id"),)).fetchone()
            if old and old[0] == h:
                self.db.rollback()
                return self.report(state)
            if old:
                raise IntegrityError("conflicting_duplicate_event")
            stamp = float(event["observed_at"])
            if not math.isfinite(stamp) or stamp < state["last_at"]:
                raise IntegrityError("chronological_boundary_violation")
            projected = copy.deepcopy(state)
            self._reduce(projected, event, stamp)
            projected["last_at"] = stamp
            if projected["cash"] < -1e-6:
                raise IntegrityError("negative_cash")
            self.db.execute("INSERT INTO events(id,observed_at,hash,payload) VALUES (?,?,?,?)",
                            (event["id"], stamp, h, encode(event)))
            self._save(projected)
            self.db.commit()
            return self.report(projected)
        except Exception:
            self.db.rollback()
            raise

    def verify_replay(self):
        state = self.initial_state()
        for (payload,) in self.db.execute("SELECT payload FROM events ORDER BY seq"):
            ev = json.loads(payload)
            self._reduce(state, ev, float(ev["observed_at"]))
            state["last_at"] = float(ev["observed_at"])
        if digest(state) != digest(self.state()):
            raise IntegrityError("replay_mismatch")
        return {"replay": "ok", "frames": state["frames"]}

    # ------------------------------------------------------------------ reducer
    def _reduce(self, state, event, now):
        kind = event.get("type")
        if kind == "seed":
            self._seed(state, event)
            return
        if kind != "frame":
            raise IntegrityError("unsupported_event_type")
        self._merge(state, event, now)
        self._execute(state, now)
        self._close_days(state, now)
        self._watch_stops(state, now)
        self._mark(state)
        state["frames"] += 1

    def _seed(self, state, event):
        """First seed fills the history. Later seeds only repair synthetic flat days (written when the
        book was down and had no 5m bars), never overwrite real ones, and never add future days."""
        limit = day_of(float(event["observed_at"]))
        for sym, bars in event["daily"].items():
            if sym not in self.identity["symbols"]:
                continue
            clean = {int(b[0]): [float(x) for x in b[:6]] for b in bars if b[4] > 0 and int(b[0]) < limit}
            have = state["daily"].get(sym)
            if not have:
                state["daily"][sym] = [clean[d] for d in sorted(clean)][-400:]
                state["daily_sources"][sym] = event.get("source", "seed")
                continue
            for k, b in enumerate(have):
                synthetic = b[5] == 0 and b[1] == b[2] == b[3] == b[4]
                if synthetic and int(b[0]) in clean:
                    have[k] = clean[int(b[0])]
                    state["skips"]["repaired_days"] = state["skips"].get("repaired_days", 0)+1

    def _merge(self, state, frame, now):
        for sym, q in frame.get("quotes", {}).items():
            if sym in self.identity["symbols"] and 0 < float(q["bid"]) <= float(q["ask"]):
                state["quotes"][sym] = {"bid": float(q["bid"]), "ask": float(q["ask"]), "timestamp": float(q["timestamp"])}
        for sym, bars in frame.get("bars", {}).items():
            if sym not in self.identity["symbols"]:
                continue
            store = state["intraday"].setdefault(sym, {})
            for b in bars:
                t = int(b["timestamp"])
                if t+300 <= now and str(t) not in store:  # closed bars only; first version wins
                    store[str(t)] = [float(b[k]) for k in ("open", "high", "low", "close", "volume")]
            cutoff = day_of(now)-3*DAY
            for k in [k for k in store if int(k) < cutoff]:
                del store[k]

    def _fresh(self, state, sym, now):
        q = state["quotes"].get(sym)
        return q if q and 0 <= now-q["timestamp"] <= QUOTE_MAX_AGE else None

    def _close_days(self, state, now):
        """Build every completed UTC day from 5m bars; decide once all coins have the day (or an hour late)."""
        target = day_of(now)-DAY  # the most recent fully closed day
        if now < day_of(now)+300:
            return
        built_any = False
        for sym in self.identity["symbols"]:
            daily = state["daily"].setdefault(sym, [])
            last = int(daily[-1][0]) if daily else None
            d = (last+DAY) if last is not None else target
            while d <= target:
                bars = [v for k, v in sorted(state["intraday"].get(sym, {}).items(), key=lambda kv: int(kv[0]))
                        if d <= int(k) < d+DAY]
                if bars:
                    daily.append([float(d), bars[0][0], max(b[1] for b in bars), min(b[2] for b in bars), bars[-1][3],
                                  sum(b[4] for b in bars)])
                elif daily:
                    c = daily[-1][4]
                    daily.append([float(d), c, c, c, c, 0.0])  # no trades that day: flat bar
                    state["skips"]["flat_day_bars"] = state["skips"].get("flat_day_bars", 0)+1
                else:
                    break
                built_any = True
                d += DAY
            state["daily"][sym] = daily[-400:]
        have = all(state["daily"].get(s) and int(state["daily"][s][-1][0]) == target for s in self.identity["symbols"])
        if state["last_decided_day"] != target and (have or now >= target+DAY+3600):
            self._decide(state, target, now)

    def _leader(self, state):
        btc = state["daily"].get(L.LEADER)
        return L.Leader([b[:6] for b in btc]) if btc and len(btc) > 100 else None

    def _decide(self, state, day, now):
        leader = self._leader(state)
        targets = {}
        for sym in self.identity["symbols"]:
            bars = [b[:6] for b in state["daily"].get(sym, [])]
            if len(bars) < MIN_DAILY or int(bars[-1][0]) != day:
                targets[sym] = {"pos": 0.0, "stop": None, "why": "not enough daily history" if len(bars) < MIN_DAILY else "no bar today"}
                continue
            trace = []
            rows, _ = L.simulate(RULE, sym, bars, [], leader, trace=trace)
            pos, stop = rows[-1][2], trace[-1]
            closes = [b[4] for b in bars]
            ma = sum(closes[-50:])/50
            ma_prev = sum(closes[-55:-5])/50
            why = f"close {closes[-1]:.6g} {'above' if closes[-1] > ma else 'below'} 50d avg {ma:.6g} ({'rising' if ma > ma_prev else 'falling'})"
            if sym != L.LEADER and leader is not None:
                why += f"; BTC filter {'on' if leader.side_ok(day+DAY, 1) else 'blocks new entries'}"
            targets[sym] = {"pos": round(pos, 6), "stop": stop, "why": why}
        state["decisions"] = (state["decisions"]+[{"day": day, "targets": targets}])[-120:]
        state["last_decided_day"] = day
        if state["started_day"] is None:
            state["started_day"] = day
            state["start_closes"] = {s: state["daily"][s][-1][4] for s in self.identity["symbols"] if state["daily"].get(s)}
        equity = self._equity(state)
        state["equity_curve"] = (state["equity_curve"]+[[day, round(equity, 4)]])[-2000:]
        sleeve = equity/len(self.identity["symbols"])
        for sym, tg in targets.items():
            held = state["positions"].get(sym)
            pending = any(o["symbol"] == sym for o in state["pending"])
            if state["halt"] or pending:
                continue
            if tg["pos"] > 0 and not held:
                state["pending"].append({"side": "buy", "symbol": sym, "notional": tg["pos"]*sleeve, "stop": tg["stop"],
                                         "created": now, "reason": "rule_entry", "day": day})
            elif tg["pos"] <= 0 and held:
                state["pending"].append({"side": "sell", "symbol": sym, "created": now, "reason": "rule_exit", "day": day})
            elif held and tg["stop"] is not None:
                held["stop"] = max(held["stop"] or 0.0, tg["stop"])

    def _watch_stops(self, state, now):
        for sym, pos in state["positions"].items():
            q = self._fresh(state, sym, now)
            if q and pos["stop"] and q["bid"] <= pos["stop"] and not any(o["symbol"] == sym for o in state["pending"]):
                state["pending"].append({"side": "sell", "symbol": sym, "created": now, "reason": "stop"})

    def _execute(self, state, now):
        fee, slip = TREND_RULES["fee_bps"]/10000, TREND_RULES["slippage_bps"]/10000
        keep = []
        for o in state["pending"]:
            q = state["quotes"].get(o["symbol"])
            if not q or q["timestamp"] <= o["created"]:
                keep.append(o)
                continue
            if now-q["timestamp"] > QUOTE_MAX_AGE:
                keep.append(o)  # wait for a fresh quote; a daily strategy can afford to
                continue
            if o["side"] == "buy":
                price = q["ask"]*(1+slip)
                notional = min(o["notional"], state["cash"]/(1+fee))
                if notional < 10:
                    state["skips"]["below_minimum"] = state["skips"].get("below_minimum", 0)+1
                    continue
                qty = notional/price
                cost = notional*fee
                state["cash"] -= notional+cost
                state["fees"] += cost
                state["positions"][o["symbol"]] = {"qty": qty, "entry": price, "cost": notional+cost, "stop": o["stop"],
                                                   "opened": now}
            else:
                pos = state["positions"].pop(o["symbol"], None)
                if not pos:
                    continue
                price = q["bid"]*(1-slip)
                gross = pos["qty"]*price
                cost = gross*fee
                state["cash"] += gross-cost
                state["fees"] += cost
                state["trades"] = (state["trades"]+[{"symbol": o["symbol"], "opened": pos["opened"], "closed": now,
                                   "entry": pos["entry"], "exit": price, "pnl_usd": round(gross-cost-pos["cost"], 4),
                                   "return_pct": round((gross-cost)/pos["cost"]*100-100, 2), "reason": o["reason"]}])[-500:]
            state["fills"] += 1
        state["pending"] = keep

    def _equity(self, state):
        eq = state["cash"]
        for sym, p in state["positions"].items():
            q = state["quotes"].get(sym)
            eq += p["qty"]*(q["bid"] if q else p["entry"])
        return eq

    def _mark(self, state):
        eq = self._equity(state)
        state["peak"] = max(state["peak"], eq)
        dd = (state["peak"]-eq)/state["peak"]*100 if state["peak"] else 0.0
        state["max_drawdown_pct"] = round(max(state["max_drawdown_pct"], dd), 2)
        if not state["halt"] and dd >= TREND_RULES["kill_switches"]["max_drawdown_pct"]:
            state["halt"] = "max_drawdown_kill_switch"
            for sym in list(state["positions"]):
                if not any(o["symbol"] == sym for o in state["pending"]):
                    state["pending"].append({"side": "sell", "symbol": sym, "created": state["last_at"], "reason": "kill_switch"})

    # ------------------------------------------------------------------ report
    def report(self, state=None, shadow=None):
        state = self.state() if state is None else state
        eq = self._equity(state)
        start = TREND_RULES["starting_cash"]
        holds = {}
        if state["start_closes"]:
            rel = [state["daily"][s][-1][4]/c-1 for s, c in state["start_closes"].items() if state["daily"].get(s) and c]
            holds["equal_weight_hold_pct"] = round(sum(rel)/len(rel)*100, 2) if rel else None
            if L.LEADER in state["start_closes"]:
                holds["btc_hold_pct"] = round((state["daily"][L.LEADER][-1][4]/state["start_closes"][L.LEADER]-1)*100, 2)
        latest = state["decisions"][-1] if state["decisions"] else None
        return {"book": "henry_trend", "mode": "virtual_only", "rules_version": TREND_RULES["version"], "rule": RULE["id"],
                "identity_hash": digest(self.identity), "timestamp": state["last_at"], "frames": state["frames"],
                "halt": state["halt"], "equity_usd": round(eq, 2), "return_pct": round((eq/start-1)*100, 2),
                "max_drawdown_pct": state["max_drawdown_pct"], "fees_usd": round(state["fees"], 2),
                "started_day": state["started_day"], "days_live": len(state["equity_curve"]), "vs": holds,
                "positions": {s: {"qty": round(p["qty"], 8), "entry": p["entry"], "stop": p["stop"]} for s, p in state["positions"].items()},
                "pending": state["pending"], "latest_decision": latest, "recent_trades": state["trades"][-10:],
                "closed_trades": len(state["trades"]),
                "daily_history_days": {s: len(v) for s, v in state["daily"].items()},
                "shadow": shadow_check(state, shadow), "skips": state["skips"]}


def shadow_check(state, shadow):
    """Compare live daily decisions (Alpaca-built bars) with the Binance shadow replay."""
    if not shadow or not shadow.get("days"):
        return {"status": "no shadow file yet"}
    rows = []
    for dec in state["decisions"]:
        sd = shadow["days"].get(str(int(dec["day"])))
        if not sd:
            continue
        diff = [s for s, tg in dec["targets"].items() if s in sd and (tg["pos"] > 0) != (sd[s] > 0)]
        rows.append((dec["day"], diff))
    streak = 0
    for _, diff in reversed(rows):
        if not diff:
            break
        streak += 1
    limit = TREND_RULES["kill_switches"]["shadow_divergence_days"]
    return {"days_compared": len(rows), "days_in_agreement": sum(1 for _, d in rows if not d),
            "latest_disagreements": rows[-1][1] if rows else [], "divergence_streak": streak,
            "flag": streak >= limit, "status": "REVIEW: live and shadow disagree" if streak >= limit else "ok"}
