"""Henry v2: a standalone, aggressive, cash-only virtual book.

Henry v2 is deliberately independent of the three-book study (baseline, kelly, henry v1).
It has its own journal, identity, bankroll and rules, and reads the same market files
read-only. Nothing here can place a real order, touch the source ledger, or change the
three-book experiment.

Personality (versioned in HENRY_V2_RULES):
- Reads the market regime on 1h bars first: uptrend -> breakouts and trend entries, full
  aggression; consolidation -> dip buys only, take profit at the range middle; downtrend or an
  unclear transition -> cash (long-only spot cannot profit from it). Every trade is tagged with its regime.
- Hunts every approved strategy on every allowed symbol, 24/7, picking the strongest coin.
  No supervisor, news, session window or daily-loss gate.
- Stops and trails are sized from each coin's own volatility (ATR), not a fixed percent.
- Alts follow BTC: alt momentum needs a BTC uptrend; a BTC downtrend blocks alt dip buys and
  exits alt momentum trades. Breakouts and trend entries need a volume surge to count.
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
    "version": "henry-raging-bull-v4",
    "starting_cash": "500",
    "equity_floor": "250",
    "entry_tranche": "0.5",
    "max_symbols": 1,
    "min_notional": "10",
    "family_priority": ["breakout", "ema_trend", "mean_reversion"],
    "ranking": "family priority, then 24h relative strength on 1h bars",
    "regime": {"timeframe": "1h bars built from closed 5m bars", "ema_hours": 20, "slope_lookback_hours": 5,
               "efficiency_window_hours": 24, "trend_efficiency_min": "0.30", "range_efficiency_max": "0.20",
               "min_hours": 30, "retained_hours": 240},
    "regime_routes": {"uptrend": ["breakout", "ema_trend"], "consolidation": ["mean_reversion"],
                      "transition": [], "downtrend": [], "warming_up": []},
    "press_regimes": ["uptrend"],
    "market_leader": "BTC-USD",
    "market_filter": {"momentum_requires_leader": ["uptrend"], "dip_buys_block_leader": ["downtrend"],
                      "exit_alt_momentum_on_leader": ["downtrend"]},
    "volume_confirmation": {"families": ["breakout", "ema_trend"], "lookback_bars": 20, "min_ratio": "1.5"},
    "exit_on_downtrend": True,
    "range_target": "dip buys take profit at the 5m slow-window mean",
    "atr_bars": 14, "stop_atr": "2.5", "trail_atr": "3", "stop_pct_min": "0.8", "stop_pct_max": "6",
    "press_after_stop_distances": "2",
    "trend_break_closes": 2,
    "broken_quote_spread_bps": "150",
    "execution": "next-observed-quote IOC; adverse slippage; modeled fees",
    "entry_liquidity": "max(1% of last closed bar volume, liquidity_floor_usd) per bar",
    "liquidity_floor_usd": "2000",
    "exit_liquidity": "uncapped: exits always fill the whole position at bid less slippage",
    "trade_record": "one round trip per position, tagged with entry and exit regime",
    "gates_removed": ["supervisor", "news", "session_hours", "daily_loss_limit", "drawdown_halt",
                      "max_trades_per_day", "max_spread", "per_trade_loss_cap", "fixed_target", "max_hold"],
    "leverage": "none",
}
QUOTE_MAX_AGE = 30
BAR_SECONDS = 300
HOUR = 3600


def hourly_bars(bars, now, known=()):
    """Aggregate closed 5m bars into closed 1h bars. An hour is used only when the retained 5m
    window covers its start (never a truncated hour) and it closed at least ten minutes ago."""
    if not bars:
        return []
    first, groups = float(bars[0]["timestamp"]), {}
    for bar in bars:
        t = float(bar["timestamp"])
        groups.setdefault(t-t % HOUR, []).append(bar)
    known, out = set(known), []
    for h in sorted(groups):
        if h in known or h < first or now < h+HOUR+600:
            continue
        g = groups[h]
        out.append({"timestamp": h, "open": g[0]["open"], "close": g[-1]["close"],
                    "high": str(max(D(b["high"]) for b in g)), "low": str(min(D(b["low"]) for b in g)),
                    "volume": str(sum((D(b["volume"]) for b in g), D(0))), "bars": len(g)})
    return out


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
                "fees_paid": "0", "skips": {}, "last_decision": None, "hourly": {}, "regime_view": {}}

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
        keep = HENRY_V2_RULES["regime"]["retained_hours"]
        for symbol, seeded in frame.get("hourly_seed", {}).items():
            if symbol not in self.identity["symbols"]:
                raise IntegrityError("unexpected_seed_instrument")
            hours = {b["timestamp"]: b for b in state["hourly"].get(symbol, [])}
            for bar in seeded:  # existing hours win; malformed seed bars are ignored, never trusted
                t = float(bar["timestamp"])
                try:
                    lo, hi, op, close = (D(bar[k]) for k in ("low", "high", "open", "close"))
                except (KeyError, ValueError):
                    continue
                if t % HOUR or t+HOUR > now or t in hours or not 0 < lo <= min(op, close) <= max(op, close) <= hi:
                    continue
                hours[t] = bar
            state["hourly"][symbol] = [hours[t] for t in sorted(hours)[-keep:]]
        for symbol in self.identity["symbols"]:
            existing = state["hourly"].get(symbol, [])
            fresh = hourly_bars(state["histories"].get(symbol, []), now, (b["timestamp"] for b in existing))
            if fresh:
                hours = {b["timestamp"]: b for b in existing+fresh}
                state["hourly"][symbol] = [hours[t] for t in sorted(hours)[-keep:]]
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
                stop_pct = D(order["stop_pct"])
                state["position"] = {"symbol": order["symbol"], "strategy": order["strategy"], "opened": now,
                                     "quantity": str(quantity), "cost": str(value+cost),
                                     "avg_price": str((value+cost)/quantity), "entry_price": str(price),
                                     "stop": str(price*(1-stop_pct/100)), "stop_pct": str(stop_pct),
                                     "high_water": str(price), "pressed": False,
                                     "entry_regime": order["regime"], "entry_spread_bps": order["spread_bps"],
                                     "entry_atr_pct": order["atr_pct"], "family": BY_VERSION[order["strategy"]].family,
                                     "entry_market_regime": order.get("market_regime"),
                                     "entry_volume_ratio": order.get("volume_ratio")}
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
                     "pressed": pos["pressed"], "reason": order["reason"], "hold_minutes": int((now-pos["opened"])/60),
                     "family": pos["family"], "entry_regime": pos["entry_regime"],
                     "exit_regime": state["regime_view"].get(pos["symbol"], {}).get("regime"),
                     "stop_pct": pos["stop_pct"], "entry_atr_pct": pos["entry_atr_pct"],
                     "entry_spread_bps": pos["entry_spread_bps"],
                     "entry_market_regime": pos.get("entry_market_regime"),
                     "entry_volume_ratio": pos.get("entry_volume_ratio")}
            state["trades"] = (state["trades"]+[trade])[-500:]
            state["position"] = None

    def _decide(self, state, now):
        state["regime_view"] = {sym: self._regime(state, sym) for sym in self.identity["symbols"]}
        pos = state["position"]
        if pos:
            self._manage(state, pos, now)
            return
        if state["halt"]:
            return
        choice = self._best_signal(state, now)
        if not choice:
            return
        symbol, spec, regime, spread_bps, volume_ratio = choice
        budget = D(state["cash"])*D(HENRY_V2_RULES["entry_tranche"])
        if budget < D(HENRY_V2_RULES["min_notional"]):
            budget = D(state["cash"])
        if budget < D(HENRY_V2_RULES["min_notional"]):
            state["halt"], state["floor_breached"] = "bankroll_no_longer_executable", True
            return
        atr_pct = self._atr_pct(state["histories"].get(symbol, []))
        state["pending"] = {"side": "buy", "symbol": symbol, "strategy": spec.version, "budget": str(budget),
                            "created": now, "reason": "entry:"+spec.family, "regime": regime,
                            "atr_pct": str(atr_pct), "stop_pct": str(self._clamp_pct(atr_pct*D(HENRY_V2_RULES["stop_atr"]))),
                            "spread_bps": str(spread_bps), "market_regime": self._leader_regime(state),
                            "volume_ratio": volume_ratio}
        state["last_decision"] = {"at": now, "action": "enter", "symbol": symbol, "strategy": spec.version,
                                  "regime": regime, "market_regime": self._leader_regime(state)}

    # ---------- market reading ----------
    def _regime(self, state, symbol):
        r = HENRY_V2_RULES["regime"]
        closes = [float(b["close"]) for b in state["hourly"].get(symbol, [])]
        if len(closes) < r["min_hours"]:
            return {"regime": "warming_up", "hours": len(closes)}
        w, lag = r["efficiency_window_hours"], r["slope_lookback_hours"]
        now_ema, then_ema = ema(closes, r["ema_hours"]), ema(closes[:-lag], r["ema_hours"])
        window = closes[-w-1:]
        path = sum(abs(window[i]-window[i-1]) for i in range(1, len(window)))
        efficiency = abs(window[-1]-window[0])/path if path else 0.0
        last = closes[-1]
        if efficiency <= float(r["range_efficiency_max"]):
            label = "consolidation"
        elif last > now_ema > then_ema and efficiency >= float(r["trend_efficiency_min"]):
            label = "uptrend"
        elif last < now_ema < then_ema and efficiency >= float(r["trend_efficiency_min"]):
            label = "downtrend"
        else:
            label = "transition"
        return {"regime": label, "efficiency": round(efficiency, 3),
                "strength_24h_pct": round((last/window[0]-1)*100, 2),
                "ema_slope_pct": round((now_ema/then_ema-1)*100, 3), "hours": len(closes)}

    def _atr_pct(self, bars):
        n = HENRY_V2_RULES["atr_bars"]
        if len(bars) < n+1:
            return D("1")
        ranges = []
        for prev, bar in zip(bars[-n-1:-1], bars[-n:]):
            pc = D(prev["close"])
            ranges.append(max(D(bar["high"])-D(bar["low"]), abs(D(bar["high"])-pc), abs(D(bar["low"])-pc)))
        return (sum(ranges, D(0))/n/D(bars[-1]["close"])*100).quantize(D(".0001"))

    def _clamp_pct(self, value):
        return min(max(value, D(HENRY_V2_RULES["stop_pct_min"])), D(HENRY_V2_RULES["stop_pct_max"])).quantize(D(".0001"))

    def _best_signal(self, state, now):
        priority, routes = HENRY_V2_RULES["family_priority"], HENRY_V2_RULES["regime_routes"]
        candidates = []
        for symbol in self.identity["symbols"]:
            bars, q = state["histories"].get(symbol, []), self._fresh(state, symbol, now)
            if not q or not self._bars_fresh(bars, now):
                continue
            spread_bps = ((D(q["ask"])-D(q["bid"]))/D(q["ask"])*10000).quantize(D(".1"))
            if spread_bps > D(HENRY_V2_RULES["broken_quote_spread_bps"]):
                continue  # data-quality guard, not a strategy gate
            view = state["regime_view"].get(symbol) or self._regime(state, symbol)
            allowed = [f for f in routes[view["regime"]] if self._market_allows(state, symbol, f)]
            for spec in CATALOG:
                if spec.family not in allowed or not entry_signal(spec, bars):
                    continue
                volume_ratio = self._volume_ratio(bars)
                if spec.family in HENRY_V2_RULES["volume_confirmation"]["families"] and (
                        volume_ratio is None or volume_ratio < D(HENRY_V2_RULES["volume_confirmation"]["min_ratio"])):
                    key = f"{symbol}@{bars[-1]['timestamp']}"
                    if state.get("last_volume_skip") != key:
                        state["last_volume_skip"] = key
                        self._skip(state, "unconfirmed_by_volume")
                    continue
                candidates.append((priority.index(spec.family), -view.get("strength_24h_pct", 0), symbol,
                                   spec.version, spec, view["regime"], spread_bps, volume_ratio))
        if not candidates:
            return None
        best = sorted(candidates, key=lambda c: c[:4])[0]
        return best[2], best[4], best[5], best[6], (str(best[7]) if best[7] is not None else None)

    def _leader_regime(self, state):
        leader = HENRY_V2_RULES["market_leader"]
        if leader not in self.identity["symbols"]:
            return None
        return (state["regime_view"].get(leader) or self._regime(state, leader))["regime"]

    def _market_allows(self, state, symbol, family):
        """Alts move with BTC. An alt 'uptrend' while BTC is not trending up is usually a trap."""
        leader, f = self._leader_regime(state), HENRY_V2_RULES["market_filter"]
        if leader is None or symbol == HENRY_V2_RULES["market_leader"]:
            return True
        if family == "mean_reversion":
            return leader not in f["dip_buys_block_leader"]
        return leader in f["momentum_requires_leader"]

    def _volume_ratio(self, bars):
        n = HENRY_V2_RULES["volume_confirmation"]["lookback_bars"]
        if len(bars) < n+1:
            return None
        prior = sum((D(b["volume"]) for b in bars[-n-1:-1]), D(0))/n
        if prior <= 0:
            return None
        return (D(bars[-1]["volume"])/prior).quantize(D(".01"))

    def _manage(self, state, pos, now):
        q = self._fresh(state, pos["symbol"], now)
        if not q:
            return
        spec = BY_VERSION[pos["strategy"]]
        bars = state["histories"].get(pos["symbol"], [])
        regime = state["regime_view"].get(pos["symbol"], {}).get("regime")
        bid, stop_pct = D(q["bid"]), D(pos["stop_pct"])/100
        hwm = max(D(pos["high_water"]), bid)
        pos["high_water"] = str(hwm)
        if bid >= D(pos["avg_price"])*(1+stop_pct):
            trail = self._clamp_pct(self._atr_pct(bars)*D(HENRY_V2_RULES["trail_atr"]))/100
            # True breakeven: exit fee and exit slippage on top of the fee-inclusive average cost.
            breakeven = D(pos["avg_price"])*(1+(D(self.identity["fee_bps"])+D(self.identity["slippage_bps"]))/10000)
            pos["stop"] = str(max(D(pos["stop"]), hwm*(1-trail), breakeven))
        if state["halt"]:
            return self._exit(state, pos, now, "floor_liquidation")
        if bid <= D(pos["stop"]):
            return self._exit(state, pos, now, "trailing_stop" if D(pos["stop"]) >= D(pos["avg_price"]) else "stop_loss")
        if HENRY_V2_RULES["exit_on_downtrend"] and regime == "downtrend":
            return self._exit(state, pos, now, "regime_downtrend")
        if (spec.family != "mean_reversion" and pos["symbol"] != HENRY_V2_RULES["market_leader"]
                and self._leader_regime(state) in HENRY_V2_RULES["market_filter"]["exit_alt_momentum_on_leader"]):
            return self._exit(state, pos, now, "market_downtrend")
        if spec.family == "mean_reversion":
            closes = [D(b["close"]) for b in bars[-spec.slow:]]
            if len(closes) == spec.slow and D(bars[-1]["close"]) >= sum(closes, D(0))/len(closes):
                return self._exit(state, pos, now, "range_target")
            return  # dip buys are never pressed and never judged by trend
        if self._trend_broken(spec, bars):
            return self._exit(state, pos, now, "trend_break")
        if (not pos["pressed"] and pos["entry_regime"] in HENRY_V2_RULES["press_regimes"]
                and regime in HENRY_V2_RULES["press_regimes"]
                and bid >= D(pos["entry_price"])*(1+D(HENRY_V2_RULES["press_after_stop_distances"])*stop_pct)
                and self._trend_intact(spec, bars) and D(state["cash"]) >= D(HENRY_V2_RULES["min_notional"])):
            state["pending"] = {"side": "buy", "symbol": pos["symbol"], "strategy": pos["strategy"],
                                "budget": state["cash"], "created": now, "reason": "press_winner"}
            state["last_decision"] = {"at": now, "action": "press", "symbol": pos["symbol"], "regime": regime}

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
        by_regime = {}
        for t in trades:
            g = by_regime.setdefault(t.get("entry_regime", "unknown"), {"trades": 0, "wins": 0, "pnl_usd": D(0)})
            g["trades"] += 1
            g["wins"] += D(t["pnl_usd"]) > 0
            g["pnl_usd"] += D(t["pnl_usd"])
        by_regime = {k: {**v, "pnl_usd": str(v["pnl_usd"])} for k, v in by_regime.items()}
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
                "position": {k: pos[k] for k in ("symbol", "strategy", "quantity", "avg_price", "stop", "stop_pct",
                                                 "high_water", "pressed", "entry_regime")} if pos else None,
                "regimes": {s: v.get("regime") for s, v in state["regime_view"].items()},
                "regime_detail": state["regime_view"],
                "by_entry_regime": by_regime,
                "pending": state["pending"], "last_decision": state["last_decision"],
                "recent_trades": trades[-10:], "skips": state["skips"]}
