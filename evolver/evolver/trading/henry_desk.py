"""Henry, research-desk edition: analysts, regime playbooks, written theses and a risk manager.

Every closed hour, a desk of analysts writes a scored note on each coin:
  regime       multi-timeframe trend (1h, 4h, daily) -> bull / range / bear, with confidence
  structure    48h and 7d ranges, position in range, nearest support and resistance
  volatility   ATR, realized-vol percentile, compression vs expansion
  positioning  perp funding level and percentile: who is crowded, where a squeeze sits
  cross_asset  BTC regime and the coin's strength relative to BTC
  execution    round-trip cost (fees, slippage, spread)

A playbook for the coin's regime proposes setups, each with an entry, an invalidation (stop)
and a target. A setup becomes a trade only if its written THESIS clears every bar:
reward-to-risk, expected move vs cost, and a conviction score built from the analysts' notes.
A reviewer (the LLM, live only) may veto a thesis; it can never create or resize one.
The risk manager sizes by risk-at-stop, scaled by conviction, throttled in drawdown.

Shorts are SIMULATED perpetual-futures positions (fees both ways, funding every 8h). They are
labeled simulated_perp: executing them would need a venue that offers perps.

Everything here is causal: an analysis at hour i only sees bars that had closed by then.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

HOUR, H4, DAY = 3600, 4*3600, 86400

DESK_RULES = {
    "version": "henry-desk-v2",
    "starting_cash": 500.0,
    "equity_floor": 250.0,
    "fee_bps": 25.0, "slippage_bps": 5.0,
    "spread_bps": {"BTC-USD": 4.0, "ETH-USD": 4.0, "default": 20.0},
    "risk_per_trade": 0.025,          # of equity, lost if the stop is hit (before conviction scaling)
    "max_positions": 2,
    "max_gross_exposure": 1.0,        # x equity; no leverage, longs and simulated shorts combined
    "drawdown_throttle": {"at": 0.15, "risk_scale": 0.5},
    # The desk grades itself: when its own recent trades go sour it cuts size, then stands down.
    "performance_throttle": {"window": 20, "min_trades": 10, "half_size_below_avg_r": 0.0,
                             "pause_below_avg_r": -0.3, "pause_hours": 72},
    "stop_cost_multiple": 2.5,        # stop distance must be >= this x round-trip cost
    # Replay v1: when structure disagreed, trades averaged -0.69R; when it agreed, +0.31R.
    "structure_veto_below": -0.2,     # aligned structure score below this blocks the trade
    # Replay v1 caught ~40% of the bull: a desk holds beta when the whole market is trending up.
    "bull_core": {"enabled": True, "symbol": "BTC-USD", "fraction": 0.5, "catastrophe_stop": 0.12,
                  "daily_efficiency_days": 14, "daily_efficiency_min": 0.3, "cooldown_hours": 72,
                  "enter": "BTC regime bull, daily trend up, and 14-day daily efficiency >= 0.3",
                  "exit": "BTC daily trend turns down or regime turns bear; then 72h before re-entry"},
    "conviction_min": 60.0,
    "rr_min": 2.0,
    "edge_multiple": 2.5,             # expected move must be >= this x round-trip cost
    "weights": {"regime": 0.30, "structure": 0.20, "cross_asset": 0.20, "positioning": 0.15,
                "volatility": 0.05, "setup": 0.10},
    "regime": {"ema_4h": 20, "ema_1d": 20, "slope_4h_bars": 6, "slope_1d_bars": 5,
               "efficiency_4h_bars": 30, "trend_efficiency_min": 0.25},
    "management": {"breakeven_at_r": 1.0, "trail_after_r": 2.0, "trail_atr": 2.5,
                   "time_stop_hours": 72, "time_stop_min_r": 0.5},
    "market_leader": "BTC-USD",
    "short_execution": "simulated_perp",
}


# ---------------------------------------------------------------- features (causal arrays)
def ema_series(values, n):
    out, k, cur = [], 2/(n+1), None
    for v in values:
        cur = v if cur is None else cur+k*(v-cur)
        out.append(cur)
    return out


def rolling_extreme(values, n, fn):
    """fn(values[i-n+1..i]) for each i using a monotonic deque (max or min)."""
    from collections import deque
    out, dq = [], deque()
    better = (lambda a, b: a >= b) if fn is max else (lambda a, b: a <= b)
    for i, v in enumerate(values):
        while dq and better(v, values[dq[-1]]):
            dq.pop()
        dq.append(i)
        if dq[0] <= i-n:
            dq.popleft()
        out.append(values[dq[0]])
    return out


def aggregate(times, o, h, l, c, v, period):
    """Higher-timeframe bars plus, for each hourly index, the index of the last COMPLETED
    higher bar (-1 if none). A bar is complete once the hour that ends it has closed."""
    buckets, order = {}, []
    for i, t in enumerate(times):
        b = t-t % period
        if b not in buckets:
            buckets[b] = [o[i], h[i], l[i], c[i], v[i], t]
            order.append(b)
        else:
            x = buckets[b]
            x[1], x[2], x[3], x[4], x[5] = max(x[1], h[i]), min(x[2], l[i]), c[i], x[4]+v[i], t
    closes = [buckets[b][3] for b in order]
    ends = [b+period for b in order]
    last_done, j = [], -1
    for t in times:
        while j+1 < len(ends) and ends[j+1] <= t+HOUR:
            j += 1
        last_done.append(j)
    return closes, last_done


def efficiency(closes, end, n):
    if end < n:
        return 0.0
    w = closes[end-n:end+1]
    path = sum(abs(w[k]-w[k-1]) for k in range(1, len(w)))
    return abs(w[-1]-w[0])/path if path else 0.0


@dataclass
class Series:
    symbol: str
    t: list
    o: list
    h: list
    l: list
    c: list
    v: list
    funding_t: list = field(default_factory=list)
    funding_r: list = field(default_factory=list)

    def __post_init__(self):
        self.index = {t: i for i, t in enumerate(self.t)}
        self.ema20 = ema_series(self.c, 20)
        self.ema50 = ema_series(self.c, 50)
        tr = [self.h[0]-self.l[0]]+[max(self.h[i]-self.l[i], abs(self.h[i]-self.c[i-1]), abs(self.l[i]-self.c[i-1]))
                                    for i in range(1, len(self.c))]
        self.atr = ema_series(tr, 14)
        self.hi48, self.lo48 = rolling_extreme(self.h, 48, max), rolling_extreme(self.l, 48, min)
        self.hi168, self.lo168 = rolling_extreme(self.h, 168, max), rolling_extreme(self.l, 168, min)
        rets = [0.0]+[math.log(self.c[i]/self.c[i-1]) for i in range(1, len(self.c))]
        self.rv24, s, s2 = [], 0.0, 0.0
        for i, r in enumerate(rets):
            s, s2 = s+r, s2+r*r
            if i >= 24:
                s, s2 = s-rets[i-24], s2-rets[i-24]**2
            n = min(i+1, 24)
            self.rv24.append(math.sqrt(max(s2/n-(s/n)**2, 0.0)))
        self.vsum20, acc = [], 0.0
        for i, x in enumerate(self.v):
            acc += x
            if i >= 20:
                acc -= self.v[i-20]
            self.vsum20.append(acc)
        self.c4, self.done4 = aggregate(self.t, self.o, self.h, self.l, self.c, self.v, H4)
        self.cd, self.doned = aggregate(self.t, self.o, self.h, self.l, self.c, self.v, DAY)
        self.ema4 = ema_series(self.c4, DESK_RULES["regime"]["ema_4h"])
        self.emad = ema_series(self.cd, DESK_RULES["regime"]["ema_1d"])

    def volume_ratio(self, i):
        if i < 21 or self.vsum20[i-1] <= 0:
            return None
        return self.v[i]/(self.vsum20[i-1]/20)

    def rv_percentile(self, i, window=720, stride=6):
        if i < 48:
            return None
        sample = sorted(self.rv24[max(24, i-window):i:stride])
        return bisect.bisect_left(sample, self.rv24[i])/len(sample) if sample else None

    def funding_view(self, t):
        k = bisect.bisect_right(self.funding_t, t)-1
        if k < 2:
            return None
        recent = self.funding_r[max(0, k-8):k+1]
        hist = sorted(self.funding_r[max(0, k-90):k+1])
        return {"last": self.funding_r[k], "avg_3d": sum(recent)/len(recent),
                "pct_rank": bisect.bisect_left(hist, sum(recent)/len(recent))/len(hist)}


# ---------------------------------------------------------------- analysts
def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def analyst_regime(s, i):
    r = DESK_RULES["regime"]
    j4, jd = s.done4[i], s.doned[i]
    if j4 < r["efficiency_4h_bars"] or jd < r["ema_1d"]+r["slope_1d_bars"]:
        return {"label": "warming_up", "score": 0.0, "conf": 0.0,
                "notes": ["waiting for enough 4h and daily history; the desk does not trade half-blind"]}

    def trend(closes, ema, j, lag):
        if j < lag:
            return 0
        up = closes[j] > ema[j] and ema[j] > ema[j-lag]
        down = closes[j] < ema[j] and ema[j] < ema[j-lag]
        return 1 if up else -1 if down else 0
    t4 = trend(s.c4, s.ema4, j4, r["slope_4h_bars"])
    td = trend(s.cd, s.emad, jd, r["slope_1d_bars"])
    eff = efficiency(s.c4, j4, r["efficiency_4h_bars"])
    trending = eff >= r["trend_efficiency_min"]
    if t4 > 0 and trending and td != -1:
        label = "bull"
    elif t4 < 0 and trending and td != 1:
        label = "bear"
    else:
        label = "range"
    agree = [x for x in (t4, td) if x is not None]
    score = sum(agree)/len(agree) if agree else 0.0
    conf = clamp(0.4+eff+(0.2 if td is not None and td == t4 and t4 != 0 else 0), 0, 1)
    if label == "range":
        conf = clamp(0.4+(r["trend_efficiency_min"]-eff)*2, 0, 1)
    return {"label": label, "score": score, "conf": round(conf, 3), "trend_4h": t4, "trend_1d": td,
            "efficiency_4h": round(eff, 3),
            "notes": [f"4h trend {t4:+d}, daily {td if td is not None else 'n/a'}, 4h efficiency {eff:.2f} -> {label}"]}


def analyst_structure(s, i):
    c, hi7, lo7 = s.c[i], s.hi168[i], s.lo168[i]
    width = (hi7-lo7)/c if c else 0
    pos = (c-lo7)/(hi7-lo7) if hi7 > lo7 else 0.5
    prev_hi48, prev_lo48 = (s.hi48[i-1], s.lo48[i-1]) if i else (s.h[i], s.l[i])
    breakout = c > prev_hi48
    breakdown = c < prev_lo48
    score = 0.6 if breakout else -0.6 if breakdown else clamp((0.5-pos)*1.2)  # cheap near support
    return {"score": round(score, 3), "conf": round(clamp(width*10, 0.2, 1), 3), "range_pos_7d": round(pos, 3),
            "range_width_pct": round(width*100, 2), "hi48": prev_hi48, "lo48": prev_lo48, "hi7": hi7, "lo7": lo7,
            "breakout_48h": breakout, "breakdown_48h": breakdown,
            "notes": [f"7d range {lo7:.6g}-{hi7:.6g} ({width*100:.1f}% wide), price at {pos*100:.0f}% of it"
                      + (", breaking out of 48h high" if breakout else ", breaking 48h low" if breakdown else "")]}


def analyst_volatility(s, i):
    pct = s.rv_percentile(i)
    atr_pct = s.atr[i]/s.c[i]*100
    state = "unknown" if pct is None else "compressed" if pct < 0.25 else "extreme" if pct > 0.9 else "normal"
    return {"score": 0.0, "conf": 0.5, "atr_pct": round(atr_pct, 3), "rv_percentile": pct, "state": state,
            "notes": [f"ATR {atr_pct:.2f}%, realized vol {state}" + (f" ({pct*100:.0f}th pct)" if pct is not None else "")]}


def analyst_positioning(s, i):
    view = s.funding_view(s.t[i])
    if not view:
        return {"score": 0.0, "conf": 0.0, "notes": ["no funding data"]}
    avg, rank = view["avg_3d"], view["pct_rank"]
    if rank > 0.9 and avg > 0.0003:
        score, note = -0.6, "longs crowded (funding in top decile): long squeeze risk"
    elif rank < 0.1 and avg < 0:
        score, note = 0.6, "shorts crowded (deeply negative funding): short squeeze fuel"
    else:
        score, note = clamp(-avg*500), "positioning neutral"
    return {"score": round(score, 3), "conf": 0.6, **view,
            "notes": [f"funding 3d avg {avg*100:.4f}%/8h ({rank*100:.0f}th pct): {note}"]}


def analyst_cross_asset(s, i, leader, leader_regime):
    if s.symbol == DESK_RULES["market_leader"] or leader is None:
        return {"score": 0.0, "conf": 0.3, "leader_regime": leader_regime, "notes": ["is the market leader"]}
    j = leader.index.get(s.t[i])
    if j is None or i < 72 or j < 72:
        return {"score": 0.0, "conf": 0.0, "leader_regime": leader_regime, "notes": ["no aligned BTC history"]}
    rs = (s.c[i]/s.c[i-72]-1)-(leader.c[j]/leader.c[j-72]-1)
    lead = {"bull": 0.5, "bear": -0.5}.get(leader_regime, 0.0)
    score = clamp(lead+rs*8)
    return {"score": round(score, 3), "conf": 0.7, "leader_regime": leader_regime, "rel_strength_3d_pct": round(rs*100, 2),
            "notes": [f"BTC {leader_regime}; {rs*100:+.1f}% vs BTC over 3 days"]}


def round_trip_cost_pct(symbol):
    sp = DESK_RULES["spread_bps"].get(symbol, DESK_RULES["spread_bps"]["default"])
    return (2*DESK_RULES["fee_bps"]+2*DESK_RULES["slippage_bps"]+sp)/100


def analyst_execution(s, i):
    cost = round_trip_cost_pct(s.symbol)
    return {"score": 0.0, "conf": 1.0, "round_trip_cost_pct": round(cost, 3),
            "notes": [f"round trip about {cost:.2f}% (fees, slippage, spread)"]}


# ---------------------------------------------------------------- playbooks
def playbook(s, i, notes):
    """Setups for the coin's regime: (name, direction, stop, target, runner, reason)."""
    reg, st, vol = notes["regime"]["label"], notes["structure"], notes["volatility"]
    c, o, atr = s.c[i], s.o[i], s.atr[i]
    lows6, highs6 = min(s.l[max(0, i-5):i+1]), max(s.h[max(0, i-5):i+1])
    touched_ema = lambda side: any((s.l[k] <= s.ema20[k]*1.002) if side > 0 else (s.h[k] >= s.ema20[k]*0.998)  # noqa: E731
                                   for k in range(max(0, i-2), i+1))
    vr = s.volume_ratio(i) or 0
    out = []
    if reg == "bull":
        if touched_ema(1) and c > s.ema20[i] > s.ema50[i] and c > o:
            stop = lows6-0.5*atr
            target = st["hi7"] if st["hi7"] > c+atr else c+(st["hi48"]-st["lo48"])*0.6
            out.append(("trend_pullback_long", 1, stop, target, True, "pullback to the 1h EMA20 in a bull trend, reclaimed"))
        if st["breakout_48h"] and vr >= 1.5:
            stop = max(st["hi48"]-0.5*atr, c-1.5*atr)
            out.append(("breakout_long", 1, stop, c+(st["hi48"]-st["lo48"]), True,
                        f"48h breakout on {vr:.1f}x volume" + (" out of a volatility squeeze" if vol["state"] == "compressed" else "")))
    elif reg == "range":
        width = st["hi7"]-st["lo7"]
        if st["range_pos_7d"] <= 0.2 and c > o:
            out.append(("range_long_support", 1, st["lo7"]-0.5*atr, st["lo7"]+0.6*width, False,
                        "bid at the bottom of a 7d range with a rejection candle"))
        if st["range_pos_7d"] >= 0.8 and c < o:
            out.append(("range_short_resistance", -1, st["hi7"]+0.5*atr, st["hi7"]-0.6*width, False,
                        "offer at the top of a 7d range with a rejection candle"))
    elif reg == "bear":
        if touched_ema(-1) and c < s.ema20[i] < s.ema50[i] and c < o:
            stop = highs6+0.5*atr
            target = st["lo7"] if st["lo7"] < c-atr else c-(st["hi48"]-st["lo48"])*0.6
            out.append(("bear_rally_short", -1, stop, target, False, "rally into the 1h EMA20 in a bear trend, rejected"))
        if st["breakdown_48h"] and vr >= 1.5:
            stop = min(st["lo48"]+0.5*atr, c+1.5*atr)
            out.append(("breakdown_short", -1, stop, c-(st["hi48"]-st["lo48"]), True, f"48h breakdown on {vr:.1f}x volume"))
    return out


# ---------------------------------------------------------------- thesis
def conviction(direction, notes, setup_quality):
    w = DESK_RULES["weights"]
    parts = {}
    for name in ("regime", "structure", "cross_asset", "positioning", "volatility"):
        n = notes[name]
        parts[name] = direction*n["score"]*n["conf"]
    parts["setup"] = setup_quality
    raw = sum(w[k]*parts[k] for k in w)/sum(w.values())
    return round(50+50*raw, 1), {k: round(v, 3) for k, v in parts.items()}


def write_thesis(s, i, notes, setup, leader_regime):
    name, direction, stop, target, runner, reason = setup
    entry = s.c[i]
    risk, reward = abs(entry-stop), abs(target-entry)
    cost = notes["execution"]["round_trip_cost_pct"]
    rr = reward/risk if risk > 0 else 0.0
    move = reward/entry*100
    quality = 1.0 if (notes["volatility"]["state"] == "compressed" and "breakout" in name) else 0.6
    score, parts = conviction(direction, notes, quality)
    checks = {
        "reward_to_risk": (rr >= DESK_RULES["rr_min"], f"{rr:.2f} vs {DESK_RULES['rr_min']} minimum"),
        "pays_for_costs": (move >= cost*DESK_RULES["edge_multiple"],
                           f"{move:.2f}% target vs {cost*DESK_RULES['edge_multiple']:.2f}% needed"),
        "conviction": (score >= DESK_RULES["conviction_min"], f"{score} vs {DESK_RULES['conviction_min']} minimum"),
        "stop_on_correct_side": ((stop < entry < target) if direction > 0 else (target < entry < stop), "geometry"),
        "structure_does_not_oppose": (direction*notes["structure"]["score"] >= DESK_RULES["structure_veto_below"],
                                      f"structure says {direction*notes['structure']['score']:+.2f} for this side "
                                      f"(veto below {DESK_RULES['structure_veto_below']})"),
        "stop_wide_enough": (risk/entry*100 >= cost*DESK_RULES["stop_cost_multiple"],
                             f"{risk/entry*100:.2f}% stop vs {cost*DESK_RULES['stop_cost_multiple']:.2f}% needed so costs stay small in R"),
    }
    if s.symbol != DESK_RULES["market_leader"]:
        ok = leader_regime != ("bear" if direction > 0 else "bull")
        checks["market_alignment"] = (ok, f"BTC {leader_regime}")
    return {"symbol": s.symbol, "t": s.t[i], "setup": name, "direction": "long" if direction > 0 else "short",
            "execution": "spot" if direction > 0 else DESK_RULES["short_execution"],
            "regime": notes["regime"]["label"], "entry_ref": entry, "stop": stop, "target": target, "runner": runner,
            "reward_to_risk": round(rr, 2), "expected_move_pct": round(move, 3), "cost_pct": cost,
            "conviction": score, "conviction_parts": parts, "reason": reason,
            "evidence": {k: v["notes"] for k, v in notes.items()},
            "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in checks.items()},
            "approved_by_desk": all(v[0] for v in checks.values())}


def bull_core_signal(leader, i):
    """'hold' while BTC trends up on 4h and daily, 'exit' once the daily turns down or the regime
    is bear, None in between (hysteresis: a wobble into 'range' does not churn the core)."""
    if leader is None or i is None:
        return None
    reg = analyst_regime(leader, i)
    if reg["label"] == "warming_up":
        return None
    cfg = DESK_RULES["bull_core"]
    daily_eff = efficiency(leader.cd, leader.doned[i], cfg["daily_efficiency_days"])
    if reg["label"] == "bull" and reg.get("trend_1d") == 1 and daily_eff >= cfg["daily_efficiency_min"]:
        return "hold"
    if reg["label"] == "bear" or reg.get("trend_1d") == -1:
        return "exit"
    return None


def null_reviewer(thesis):
    """Replay stand-in for the LLM reviewer: approves every thesis the desk approved."""
    return True, "replay: reviewer not consulted"


class Desk:
    def __init__(self, series: dict, reviewer=null_reviewer):
        self.series, self.reviewer = series, reviewer
        self.leader = series.get(DESK_RULES["market_leader"])

    def analyze(self, symbol, t):
        s = self.series[symbol]
        i = s.index.get(t)
        if i is None or i < 60:
            return None
        lr = None
        if self.leader is not None and t in self.leader.index:
            lr = analyst_regime(self.leader, self.leader.index[t])["label"]
        notes = {"regime": analyst_regime(s, i), "structure": analyst_structure(s, i),
                 "volatility": analyst_volatility(s, i), "positioning": analyst_positioning(s, i),
                 "cross_asset": analyst_cross_asset(s, i, self.leader, lr), "execution": analyst_execution(s, i)}
        return i, notes, lr

    def theses(self, t):
        out = []
        for symbol, s in self.series.items():
            a = self.analyze(symbol, t)
            if not a or a[1]["regime"]["label"] == "warming_up":
                continue
            i, notes, lr = a
            for setup in playbook(s, i, notes):
                th = write_thesis(s, i, notes, setup, lr)
                if th["approved_by_desk"]:
                    ok, why = self.reviewer(th)
                    th["reviewer"] = {"approve": ok, "reason": why}
                out.append(th)
        return out


# ---------------------------------------------------------------- risk manager and book
class Book:
    """Cash book with spot longs and simulated perp shorts. Fills at the next bar's open with
    adverse slippage and half the spread; stops and targets are checked against each bar's
    high/low (if both are touched in one bar, the stop is assumed first)."""

    def __init__(self):
        self.cash = DESK_RULES["starting_cash"]
        self.positions, self.trades, self.pending = [], [], []
        self.peak = self.cash
        self.max_dd = 0.0
        self.fees = self.funding_paid = self.friction = 0.0
        self.halted = ""
        self.vetoed, self.rejected = [], 0
        self.paused_until, self.pauses = 0, 0

    # -- the desk grading itself
    def recent_avg_r(self):
        pt = DESK_RULES["performance_throttle"]
        recent = self.trades[-pt["window"]:]
        if len(recent) < pt["min_trades"]:
            return None
        return sum(t["r_multiple"] for t in recent)/len(recent)

    def standing_down(self, t):
        pt = DESK_RULES["performance_throttle"]
        if t < self.paused_until:
            return True
        avg = self.recent_avg_r()
        if avg is not None and avg < pt["pause_below_avg_r"] and self.trades[-1]["closed"] > self.paused_until-pt["pause_hours"]*HOUR:
            self.paused_until = t+pt["pause_hours"]*HOUR
            self.pauses += 1
            return True
        return False

    # -- valuation
    def equity(self, marks):
        eq = self.cash
        for p in self.positions:
            px = marks.get(p["symbol"], p["entry"])
            eq += p["qty"]*px if p["dir"] > 0 else p["qty"]*(p["entry"]-px)+p["margin"]
        return eq

    def gross_exposure(self, marks):
        return sum(p["qty"]*marks.get(p["symbol"], p["entry"]) for p in self.positions)

    def mark(self, marks):
        eq = self.equity(marks)
        self.peak = max(self.peak, eq)
        self.max_dd = max(self.max_dd, (self.peak-eq)/self.peak if self.peak else 0)
        if not self.halted and eq < DESK_RULES["equity_floor"]:
            self.halted = "equity_floor_breached"
        return eq

    # -- fills
    def _fill_price(self, symbol, px, side):
        sp = DESK_RULES["spread_bps"].get(symbol, DESK_RULES["spread_bps"]["default"])/2
        adj = (DESK_RULES["slippage_bps"]+sp)/10000
        return px*(1+adj) if side > 0 else px*(1-adj)

    def _fee(self, notional):
        f = notional*DESK_RULES["fee_bps"]/10000
        self.fees += f
        return f

    def size(self, thesis, marks):
        eq = self.equity(marks)
        risk = DESK_RULES["risk_per_trade"]*eq
        if self.peak and (self.peak-eq)/self.peak >= DESK_RULES["drawdown_throttle"]["at"]:
            risk *= DESK_RULES["drawdown_throttle"]["risk_scale"]
        risk *= clamp((thesis["conviction"]-50)/30, 0.5, 1.2)
        avg = self.recent_avg_r()
        if avg is not None and avg < DESK_RULES["performance_throttle"]["half_size_below_avg_r"]:
            risk *= 0.5
        per_unit = abs(thesis["entry_ref"]-thesis["stop"])
        if per_unit <= 0:
            return 0.0
        qty = risk/per_unit
        room = DESK_RULES["max_gross_exposure"]*eq-self.gross_exposure(marks)
        if thesis["direction"] == "long":
            room = min(room, self.cash*0.995)
        return max(0.0, min(qty, room/thesis["entry_ref"]))

    def tactical(self):
        return [p for p in self.positions if not p.get("core")]

    def core(self):
        return next((p for p in self.positions if p.get("core")), None)

    def open_core(self, symbol, bar_open, t, marks):
        cfg = DESK_RULES["bull_core"]
        if self.halted or self.core() or t < getattr(self, "core_cooldown_until", 0):
            return None
        eq = self.equity(marks)
        price = self._fill_price(symbol, bar_open, 1)
        notional = min(cfg["fraction"]*eq, self.cash*0.995,
                       DESK_RULES["max_gross_exposure"]*eq-self.gross_exposure(marks))
        if notional < 10:
            return None
        thesis = {"symbol": symbol, "setup": "bull_core", "direction": "long", "execution": "spot", "regime": "bull",
                  "stop": price*(1-cfg["catastrophe_stop"]), "target": price*100, "runner": True, "conviction": 70.0,
                  "conviction_parts": {}, "reward_to_risk": 0, "expected_move_pct": 0,
                  "reason": "market-wide uptrend: hold beta in the leader", "entry_ref": price}
        qty = notional/price
        fee = self._fee(notional)
        self.friction += fee+abs(price-bar_open)*qty
        self.cash -= notional+fee
        pos = {"symbol": symbol, "dir": 1, "qty": qty, "entry": price, "margin": 0.0, "stop": thesis["stop"],
               "target": thesis["target"], "runner": True, "risk_per_unit": price*cfg["catastrophe_stop"], "opened": t,
               "best": price, "funding": 0.0, "fee_in": fee, "thesis": thesis, "target_hit": False, "core": True}
        self.positions.append(pos)
        return pos

    def open(self, thesis, bar_open, t, marks):
        if self.halted or len(self.tactical()) >= DESK_RULES["max_positions"]:
            return None
        if any(p["symbol"] == thesis["symbol"] and not p.get("core") for p in self.positions):
            return None
        d = 1 if thesis["direction"] == "long" else -1
        qty = self.size(thesis, marks)
        price = self._fill_price(thesis["symbol"], bar_open, d)
        if qty*price < 10:
            return None
        # A gap through the stop or target before the fill invalidates the thesis.
        if (d > 0 and not thesis["stop"] < price < thesis["target"]) or (d < 0 and not thesis["target"] < price < thesis["stop"]):
            self.rejected += 1
            return None
        notional = qty*price
        fee = self._fee(notional)
        self.friction += fee+abs(price-bar_open)*qty
        if d > 0:
            self.cash -= notional+fee
            margin = 0.0
        else:
            margin = notional  # 1x collateral set aside for the simulated short
            self.cash -= margin+fee
        pos = {"symbol": thesis["symbol"], "dir": d, "qty": qty, "entry": price, "margin": margin,
               "stop": thesis["stop"], "target": thesis["target"], "runner": thesis["runner"],
               "risk_per_unit": abs(price-thesis["stop"]), "opened": t, "best": price, "funding": 0.0,
               "fee_in": fee, "thesis": thesis, "target_hit": False}
        self.positions.append(pos)
        return pos

    def close(self, pos, price_raw, t, reason):
        d = pos["dir"]
        price = self._fill_price(pos["symbol"], price_raw, -d)
        notional = pos["qty"]*price
        fee = self._fee(notional)
        self.friction += fee+abs(price-price_raw)*pos["qty"]
        if d > 0:
            self.cash += notional-fee
            pnl = notional-pos["qty"]*pos["entry"]-fee-pos["fee_in"]
        else:
            gain = pos["qty"]*(pos["entry"]-price)
            self.cash += pos["margin"]+gain-fee
            pnl = gain-fee-pos["fee_in"]+pos["funding"]
        risk_usd = pos["risk_per_unit"]*pos["qty"]
        th = pos["thesis"]
        if pos.get("core"):
            self.core_cooldown_until = t+DESK_RULES["bull_core"]["cooldown_hours"]*HOUR
        self.trades.append({"symbol": pos["symbol"], "setup": th["setup"], "direction": th["direction"],
                            "execution": th["execution"], "regime": th["regime"], "opened": pos["opened"], "closed": t,
                            "hours": round((t-pos["opened"])/HOUR, 1), "entry": pos["entry"], "exit": price,
                            "pnl_usd": round(pnl, 4), "r_multiple": round(pnl/risk_usd, 3) if risk_usd else 0.0,
                            "funding_usd": round(pos["funding"], 4), "reason": reason, "conviction": th["conviction"],
                            "conviction_parts": th["conviction_parts"], "reward_to_risk": th["reward_to_risk"],
                            "expected_move_pct": th["expected_move_pct"], "thesis_reason": th["reason"]})
        self.positions.remove(pos)

    # -- per-bar management
    def apply_funding(self, symbol, rate, price):
        for p in self.positions:
            if p["symbol"] == symbol and p["dir"] < 0:
                amount = p["qty"]*price*rate  # positive funding: longs pay, shorts receive
                p["funding"] += amount
                self.cash += amount
                self.funding_paid -= amount

    def manage(self, pos, bar, t, regime_label, atr):
        """bar = (open, high, low, close) of the bar that just closed at t."""
        o, h, l, c = bar
        if pos.get("core"):  # the core only answers to its catastrophe stop and the core signal
            if l <= pos["stop"]:
                return self.close(pos, min(o, pos["stop"]), t, "core_catastrophe_stop")
            return None
        d, r = pos["dir"], pos["risk_per_unit"]
        m = DESK_RULES["management"]
        if d > 0:
            if l <= pos["stop"]:
                return self.close(pos, min(o, pos["stop"]), t, "stop" if pos["stop"] < pos["entry"] else "trailing_stop")
            if not pos["runner"] and h >= pos["target"]:
                return self.close(pos, max(o, pos["target"]), t, "target")
        else:
            if h >= pos["stop"]:
                return self.close(pos, max(o, pos["stop"]), t, "stop" if pos["stop"] > pos["entry"] else "trailing_stop")
            if not pos["runner"] and l <= pos["target"]:
                return self.close(pos, min(o, pos["target"]), t, "target")
        pos["best"] = max(pos["best"], h) if d > 0 else min(pos["best"], l)
        gained_r = d*(c-pos["entry"])/r if r else 0
        if pos["runner"] and not pos["target_hit"] and (h >= pos["target"] if d > 0 else l <= pos["target"]):
            pos["target_hit"] = True  # runners keep going past the target on a trailing stop
        if gained_r >= m["breakeven_at_r"]:
            be = pos["entry"]*(1+d*round_trip_cost_pct(pos["symbol"])/100)
            pos["stop"] = max(pos["stop"], be) if d > 0 else min(pos["stop"], be)
        if gained_r >= m["trail_after_r"] or pos["target_hit"]:
            trail = pos["best"]-d*m["trail_atr"]*atr
            pos["stop"] = max(pos["stop"], trail) if d > 0 else min(pos["stop"], trail)
        if (d > 0 and regime_label == "bear") or (d < 0 and regime_label == "bull"):
            return self.close(pos, c, t, "regime_flip")
        if (t-pos["opened"])/HOUR >= m["time_stop_hours"] and gained_r < m["time_stop_min_r"]:
            return self.close(pos, c, t, "time_stop")
        return None
