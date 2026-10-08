"""Henry v2: a standalone, aggressive, cash-only virtual book.

Henry v2 is deliberately independent of the three-book study (baseline, kelly, henry v1).
It has its own journal, identity, bankroll and rules, and reads the same market files
read-only. Nothing here can place a real order, touch the source ledger, or change the
three-book experiment.

Personality (versioned in HENRY_V2_RULES):
- Hunts every approved strategy on every allowed symbol, 24/7. No supervisor, news,
  session window or daily-loss gate.
- Probe then press: opens with half of spendable cash, adds the rest once the trade is
  working by at least one stop distance while the trend is intact.
- Lets winners run: no fixed take-profit and no hold clock. Exits only on a trailing
  stop or a confirmed trend break.
- No cooldown after an exit. The next qualifying signal is taken.
- Never borrows. Halts permanently (and liquidates) if equity falls below the floor,
  because a blown account is the only outcome that ends the data.

Realism that stays: next-observed-quote IOC fills, adverse slippage, modeled fees on
both sides, a liquidity cap of 1% of the last closed bar's volume, exchange increments
and minimums, and closed-bar-only signals.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sqlite3
from decimal import ROUND_DOWN, ROUND_UP
from pathlib import Path

from .contracts import decimal as D, encode
from .strategies import BY_VERSION, CATALOG, ema, entry_signal

HENRY_V2_RULES = {
    "version": "henry-raging-bull-v2",
    "starting_cash": "500",
    "equity_floor": "250",
    "entry_tranche": "0.5",
    "press_after_stop_multiples": "1",
    "trail_stop_multiples": "2",
    "trend_break_closes": 2,
    "max_symbols": 1,
    "min_notional": "10",
    "family_priority": ["breakout", "ema_trend", "mean_reversion"],
    "execution": "next-observed-quote IOC; adverse slippage; modeled fees",
    "entry_liquidity": "max(1% of last closed bar volume, liquidity_floor_usd) per bar",
    "liquidity_floor_usd": "2000",
    "exit_liquidity": "uncapped: exits always fill the whole position at bid less slippage",
    "trade_record": "one round trip per position, including presses and partial exits",
    "gates_removed": ["supervisor", "news", "session_hours", "daily_loss_limit", "drawdown_halt",
                      "max_trades_per_day", "max_spread", "per_trade_loss_cap", "fixed_target", "max_hold"],
    "leverage": "none",
}
QUOTE_MAX_AGE = 30
BAR_SECONDS = 300


class IntegrityError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def floor_to(value, step):
    return (D(value)/D(step)).to_integral_value(rounding=ROUND_DOWN)*D(step)


def fee(value, bps):
    return (D(value)*D(bps)/10000).quantize(D(".01"), rounding=ROUND_UP)


class HenryV2:
    def __init__(self, path, *, symbols=None, fee_bps=None, slippage_bps=None, epoch=None):
        self.path = Path(path)
        if self.path.name != "henry_v2.sqlite":
            raise ValueError("Henry v2 must use its own henry_v2.sqlite file")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.executescript("""PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                observed_at REAL NOT NULL, hash TEXT NOT NULL, payload TEXT NOT NULL);""")
        row = self.db.execute("SELECT payload FROM meta WHERE key='identity'").fetchone()
        if row:
            self.identity = json.loads(row[0])
            if self.identity["rules"] != HENRY_V2_RULES:
                self.db.close()
                raise ValueError("existing Henry v2 journal is immutable; new rules need a new root")
            return
        if not symbols or epoch is None or not math.isfinite(epoch) or fee_bps is None or slippage_bps is None:
            self.db.close()
            raise ValueError("initialization needs symbols, fee/slippage assumptions and an epoch")
        self.identity = {"rules": HENRY_V2_RULES, "epoch": epoch, "symbols": sorted(symbols),
                         "fee_bps": str(D(fee_bps)), "slippage_bps": str(D(slippage_bps)),
                         "strategies": [s.version for s in CATALOG]}
        with self.db:
            self.db.execute("INSERT INTO meta VALUES ('identity',?)", (encode(self.identity),))
            self._save(self.initial_state())

    # ---------- persistence ----------
    def close(self):
        self.db.close()

    def initial_state(self):
        cash = HENRY_V2_RULES["starting_cash"]
        return {"cash": cash, "position": None, "pending": None, "trades": [], "fills": 0,
                "histories": {}, "quotes": {}, "liquidity_used": {}, "peak": cash, "max_drawdown_pct": "0",
                "halt": "", "floor_breached": False, "frames": 0, "last_at": self.identity["epoch"],
                "fees_paid": "0", "skips": {}, "last_decision": None}

    def state(self):
        return json.loads(self.db.execute("SELECT payload FROM meta WHERE key='state'").fetchone()[0])

    def _save(self, state):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('state',?)", (encode(state),))

    def apply(self, event):
        """Exactly-once projection. Integrity failures halt permanently with evidence; cash never resets."""
        event_hash = digest(event)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.state()
            old = self.db.execute("SELECT hash FROM events WHERE id=?", (event.get("id"),)).fetchone()
            if old and old[0] == event_hash:
                self.db.rollback()
                return self.report(state)
            if state["halt"] and not state["floor_breached"]:
                raise IntegrityError("henry_v2 already stopped: "+state["halt"])
            try:
                if old:
                    raise IntegrityError("conflicting_duplicate_event")
                stamp = float(event["observed_at"])
                if not math.isfinite(stamp) or stamp < state["last_at"]:
                    raise IntegrityError("chronological_boundary_violation")
                projected = copy.deepcopy(state)
                self._reduce(projected, event, stamp)
                projected["last_at"] = stamp
                self._check(projected)
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                reason = str(exc) if isinstance(exc, IntegrityError) else "invalid_input_"+type(exc).__name__
                state["halt"] = reason
                failure = {"type": "integrity_failure", "reason": reason, "rejected_hash": event_hash}
                self.db.execute("INSERT INTO events(id,observed_at,hash,payload) VALUES (?,?,?,?)",
                                ("failure:"+event_hash, state["last_at"], digest(failure), encode(failure)))
                self._save(state)
                self.db.commit()
                raise IntegrityError(reason) from exc
            self.db.execute("INSERT INTO events(id,observed_at,hash,payload) VALUES (?,?,?,?)",
                            (event["id"], stamp, event_hash, encode(event)))
            self._save(projected)
            self.db.commit()
            return self.report(projected)
        except Exception:
            self.db.rollback()
            raise

    def verify_replay(self):
        state = self.initial_state()
        for (payload,) in self.db.execute("SELECT payload FROM events ORDER BY seq"):
            event = json.loads(payload)
            if event.get("type") == "integrity_failure":
                state["halt"] = event["reason"]
                continue
            self._reduce(state, event, float(event["observed_at"]))
            state["last_at"] = float(event["observed_at"])
        if digest(state) != digest(self.state()):
            raise IntegrityError("replay_mismatch")
        return {"replay": "ok", "frames": state["frames"], "state_hash": digest(state)}

    def _check(self, state):
        if D(state["cash"]) < 0:
            raise IntegrityError("negative_cash")
        pos = state["position"]
        if pos and D(pos["quantity"]) <= 0:
            raise IntegrityError("non_positive_position")

    # ---------- the book ----------
    def _reduce(self, state, event, now):
        if event.get("type") != "frame":
            raise IntegrityError("unsupported_event_type")
        self._merge(state, event, now)
        self._execute_pending(state, now)
        self._mark(state)
        if not state["pending"]:
            self._decide(state, now)
        state["frames"] += 1

    def _merge(self, state, frame, now):
        for symbol, incoming in frame.get("bars", {}).items():
            if symbol not in self.identity["symbols"]:
                raise IntegrityError("unexpected_bar_instrument")
            bars = {b["timestamp"]: b for b in state["histories"].get(symbol, [])}
            for bar in incoming:
                t = float(bar["timestamp"])
                lo, hi, op, close = (D(bar[k]) for k in ("low", "high", "open", "close"))
                if t % BAR_SECONDS or t+BAR_SECONDS > now or not 0 < lo <= min(op, close) <= max(op, close) <= hi:
                    raise IntegrityError("invalid_or_future_closed_bar")
                if t in bars and bars[t] != bar:
                    raise IntegrityError("closed_bar_revision")
                bars[t] = bar
            state["histories"][symbol] = [bars[t] for t in sorted(bars)[-400:]]
        for symbol, q in frame.get("quotes", {}).items():
            if symbol not in self.identity["symbols"]:
                raise IntegrityError("unexpected_quote_instrument")
            if not 0 < D(q["bid"]) <= D(q["ask"]):
                raise IntegrityError("crossed_or_invalid_quote")
            state["quotes"][symbol] = q

    def _fresh(self, state, symbol, now):
        q = state["quotes"].get(symbol)
        return q if q and 0 <= now-q["timestamp"] <= QUOTE_MAX_AGE else None

    def _bars_fresh(self, bars, now):
        return bars and 0 <= now-bars[-1]["timestamp"]-BAR_SECONDS <= 600

    def _equity(self, state):
        pos, cash = state["position"], D(state["cash"])
        if not pos:
            return cash
        q = state["quotes"].get(pos["symbol"])
        bid = D(q["bid"]) if q else D(pos["avg_price"])
        return cash+D(pos["quantity"])*bid

    def _mark(self, state):
        equity = self._equity(state)
        peak = max(D(state["peak"]), equity)
        state["peak"] = str(peak)
        dd = (peak-equity)/peak*100 if peak > 0 else D(0)
        state["max_drawdown_pct"] = str(max(D(state["max_drawdown_pct"]), dd).quantize(D(".01")))
        if not state["floor_breached"] and equity < D(HENRY_V2_RULES["equity_floor"]):
            state["floor_breached"] = True
            state["halt"] = "equity_floor_breached"

    def _skip(self, state, reason):
        state["skips"][reason] = state["skips"].get(reason, 0)+1

    def _increments(self, q):
        return D(q.get("increment", "0.00000001")), D(q.get("minimum_quantity", "0")), q.get("price_increment")

    def _execute_pending(self, state, now):
        order = state["pending"]
        if not order:
            return
        q = state["quotes"].get(order["symbol"])
        if not q or q["timestamp"] <= order["created"]:
            if now-order["created"] > QUOTE_MAX_AGE:
                state["pending"] = None
                self._skip(state, "unfilled_expired")
            return
        state["pending"] = None
        if now-q["timestamp"] > QUOTE_MAX_AGE:
            self._skip(state, "unfilled_expired")
            return
        buy, slip, bps = order["side"] == "buy", D(self.identity["slippage_bps"]), D(self.identity["fee_bps"])
        price = D(q["ask"] if buy else q["bid"])*(1+(1 if buy else -1)*slip/10000)
        step, minimum, price_step = self._increments(q)
        if price_step:
            price = (price/D(price_step)).to_integral_value(rounding=ROUND_UP if buy else ROUND_DOWN)*D(price_step)
        bucket = str(q.get("capacity_bucket", q["timestamp"]))
        used = state["liquidity_used"].get(order["symbol"], {})
        consumed = D(used["quantity"]) if used.get("bucket") == bucket else D(0)
        capacity = max(D(q.get("capacity", "0")), D(HENRY_V2_RULES["liquidity_floor_usd"])/price)
        available = max(D(0), capacity-consumed)
        if buy:
            budget = min(D(order["budget"]), D(state["cash"]))
            quantity = floor_to(min(budget/(price*(1+bps/10000)), available), step)
        else:
            quantity = D(state["position"]["quantity"])  # exits are never liquidity-capped
        if quantity <= 0 or buy and (quantity < minimum or quantity*price < D(HENRY_V2_RULES["min_notional"])):
            self._skip(state, "unfilled_price_or_liquidity")
            return
        value = quantity*price
        cost = fee(value, bps)
        state["liquidity_used"][order["symbol"]] = {"bucket": bucket, "quantity": str(consumed+quantity)}
        state["fees_paid"] = str(D(state["fees_paid"])+cost)
        state["fills"] += 1
        if buy:
            if value+cost > D(state["cash"]):
                quantity = floor_to((D(state["cash"])-D(".01"))/(price*(1+bps/10000)), step)
                if quantity <= 0:
                    self._skip(state, "unfunded")
                    return
                value, cost = quantity*price, fee(quantity*price, bps)
            state["cash"] = str(D(state["cash"])-value-cost)
            pos = state["position"]
            if pos:  # press the winner
                total_cost = D(pos["cost"])+value+cost
                qty = D(pos["quantity"])+quantity
                pos.update(quantity=str(qty), cost=str(total_cost), avg_price=str(total_cost/qty), pressed=True,
                           invested=str(D(pos.get("invested", pos["cost"]))+value+cost),
                           stop=str(max(D(pos["stop"]), total_cost/qty*(1+bps/10000))))
            else:
                spec = BY_VERSION[order["strategy"]]
                stop = price*(1-D(spec.stop_fraction))
                state["position"] = {"symbol": order["symbol"], "strategy": order["strategy"], "opened": now,
                                     "quantity": str(quantity), "cost": str(value+cost),
                                     "avg_price": str((value+cost)/quantity), "entry_price": str(price),
                                     "stop": str(stop), "high_water": str(price), "pressed": False}
        else:
            pos = state["position"]
            proceeds = value-cost
            remaining = D(pos["quantity"])-quantity
            share = D(pos["cost"])*quantity/D(pos["quantity"])
            state["cash"] = str(D(state["cash"])+proceeds)
            realized = D(pos.get("realized", "0"))+proceeds-share
            if remaining > 0:
                pos.update(quantity=str(remaining), cost=str(D(pos["cost"])-share), realized=str(realized),
                           invested=str(D(pos.get("invested", pos["cost"]))))
                return
            invested = D(pos.get("invested", pos["cost"]))
            trade = {"symbol": pos["symbol"], "strategy": pos["strategy"], "opened": pos["opened"], "closed": now,
                     "pnl_usd": str(realized.quantize(D(".01"))),
                     "return_pct": str((realized/invested*100).quantize(D(".01"))) if invested > 0 else "0",
                     "pressed": pos["pressed"], "reason": order["reason"], "hold_minutes": int((now-pos["opened"])/60)}
            state["trades"] = (state["trades"]+[trade])[-500:]
            state["position"] = None

    def _decide(self, state, now):
        pos = state["position"]
        if pos:
            self._manage(state, pos, now)
            return
        if state["halt"]:
            return
        choice = self._best_signal(state, now)
        if not choice:
            return
        symbol, spec = choice
        budget = D(state["cash"])*D(HENRY_V2_RULES["entry_tranche"])
        if budget < D(HENRY_V2_RULES["min_notional"]):
            budget = D(state["cash"])
        if budget < D(HENRY_V2_RULES["min_notional"]):
            state["halt"], state["floor_breached"] = "bankroll_no_longer_executable", True
            return
        state["pending"] = {"side": "buy", "symbol": symbol, "strategy": spec.version, "budget": str(budget),
                            "created": now, "reason": "entry:"+spec.family}
        state["last_decision"] = {"at": now, "action": "enter", "symbol": symbol, "strategy": spec.version}

    def _best_signal(self, state, now):
        priority = HENRY_V2_RULES["family_priority"]
        candidates = []
        for symbol in self.identity["symbols"]:
            bars = state["histories"].get(symbol, [])
            if not self._fresh(state, symbol, now) or not self._bars_fresh(bars, now):
                continue
            for spec in CATALOG:
                if entry_signal(spec, bars):
                    lookback = bars[-spec.slow-1]["close"] if len(bars) > spec.slow else bars[0]["close"]
                    momentum = D(bars[-1]["close"])/D(lookback)-1
                    candidates.append((priority.index(spec.family), -momentum, symbol, spec.version, spec))
        if not candidates:
            return None
        best = sorted(candidates, key=lambda c: c[:4])[0]
        return best[2], best[4]

    def _manage(self, state, pos, now):
        q = self._fresh(state, pos["symbol"], now)
        if not q:
            return
        spec = BY_VERSION[pos["strategy"]]
        bid, stop_frac = D(q["bid"]), D(spec.stop_fraction)
        hwm = max(D(pos["high_water"]), bid)
        pos["high_water"] = str(hwm)
        trailed = hwm*(1-D(HENRY_V2_RULES["trail_stop_multiples"])*stop_frac)
        if bid >= D(pos["avg_price"])*(1+stop_frac):
            pos["stop"] = str(max(D(pos["stop"]), trailed, D(pos["avg_price"])*(1+D(self.identity["fee_bps"])/10000)))
        if state["halt"]:
            return self._exit(state, pos, now, "floor_liquidation")
        if bid <= D(pos["stop"]):
            return self._exit(state, pos, now, "trailing_stop" if D(pos["stop"]) >= D(pos["avg_price"]) else "stop_loss")
        bars = state["histories"].get(pos["symbol"], [])
        # A dip buy sits below trend by design; only momentum entries can be invalidated by a trend break.
        if spec.family != "mean_reversion" and self._trend_broken(spec, bars):
            return self._exit(state, pos, now, "trend_break")
        if (not pos["pressed"] and bid >= D(pos["entry_price"])*(1+D(HENRY_V2_RULES["press_after_stop_multiples"])*stop_frac)
                and self._trend_intact(spec, bars) and D(state["cash"]) >= D(HENRY_V2_RULES["min_notional"])):
            state["pending"] = {"side": "buy", "symbol": pos["symbol"], "strategy": pos["strategy"],
                                "budget": state["cash"], "created": now, "reason": "press_winner"}
            state["last_decision"] = {"at": now, "action": "press", "symbol": pos["symbol"]}

    def _trend_intact(self, spec, bars):
        closes = [float(b["close"]) for b in bars[-spec.slow*3:]]
        return len(closes) >= spec.slow and ema(closes, spec.fast) > ema(closes, spec.slow)

    def _trend_broken(self, spec, bars):
        n = HENRY_V2_RULES["trend_break_closes"]
        closes = [float(b["close"]) for b in bars[-spec.slow*3:]]
        if len(closes) < spec.slow+n:
            return False
        return all(closes[-i] < ema(closes[:len(closes)-i+1], spec.slow) for i in range(1, n+1))

    def _exit(self, state, pos, now, reason):
        state["pending"] = {"side": "sell", "symbol": pos["symbol"], "strategy": pos["strategy"],
                            "created": now, "reason": reason}
        state["last_decision"] = {"at": now, "action": "exit", "symbol": pos["symbol"], "reason": reason}

    # ---------- reporting ----------
    def report(self, state=None):
        state = self.state() if state is None else state
        trades = state["trades"]
        pnls = [D(t["pnl_usd"]) for t in trades]
        wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p <= 0]
        equity = self._equity(state)
        start = D(HENRY_V2_RULES["starting_cash"])
        avg_win = sum(wins, D(0))/len(wins) if wins else D(0)
        avg_loss = sum(losses, D(0))/len(losses) if losses else D(0)
        pos = state["position"]
        return {"book": "henry_v2", "mode": "virtual_only", "rules_version": HENRY_V2_RULES["version"],
                "identity_hash": digest(self.identity), "epoch": self.identity["epoch"],
                "timestamp": state["last_at"], "frames": state["frames"], "halt": state["halt"],
                "floor_breached": state["floor_breached"], "equity_floor_usd": HENRY_V2_RULES["equity_floor"],
                "cash_usd": str(D(state["cash"]).quantize(D(".01"))), "equity_usd": str(equity.quantize(D(".01"))),
                "net_pnl_usd": str((equity-start).quantize(D(".01"))),
                "return_pct": str(((equity-start)/start*100).quantize(D(".01"))),
                "peak_equity_usd": str(D(state["peak"]).quantize(D(".01"))),
                "max_drawdown_pct": state["max_drawdown_pct"], "fees_paid_usd": state["fees_paid"],
                "closed_trades": len(trades), "win_rate_pct": str((D(len(wins))/len(trades)*100).quantize(D(".1"))) if trades else "0",
                "avg_win_usd": str(avg_win.quantize(D(".01"))), "avg_loss_usd": str(avg_loss.quantize(D(".01"))),
                "payoff_ratio": str((avg_win/-avg_loss).quantize(D(".01"))) if avg_loss < 0 and wins else None,
                "best_trade_usd": str(max(pnls)) if pnls else None, "worst_trade_usd": str(min(pnls)) if pnls else None,
                "pressed_trades": sum(1 for t in trades if t["pressed"]),
                "position": {k: pos[k] for k in ("symbol", "strategy", "quantity", "avg_price", "stop", "high_water", "pressed")} if pos else None,
                "pending": state["pending"], "last_decision": state["last_decision"],
                "recent_trades": trades[-10:], "skips": state["skips"]}
