"""Henry's research lab: learn which setups work on one era, freeze the lessons, test once on another.

  train  python -m evolver.trading.henry_lab train --data A.json.gz [--data B.json.gz ...] --out lessons.json
  test   python -m evolver.trading.henry_lab test  --data T.json.gz --lessons lessons.json --ledger ledger.json

TRAINING (e.g. Oct 2021 to Oct 2026)
  Every setup in the library (breakouts, trend filters, momentum, pullbacks in trend, volatility
  squeezes, fair value gap retraces, on 4h and daily bars, long-only or long/short, with or without a BTC trend filter for
  alts) is backtested with costs, simulated-perp funding for shorts and intrabar stops.
  The training span is cut into 6-month blocks. A setup survives only if, decided in advance:
    - it made money in at least 60% of the blocks, and lost no more than 15% in its worst block
    - profit factor >= 1.2 over at least 20 trades
    - its Sharpe beats the LUCK BAR: the best Sharpe any setup reached on shuffled copies of the
      same data (same volatility, no real patterns). Testing many ideas guarantees some look good
      by chance; a real pattern has to beat the best chance result.
  Survivors are ranked by Sharpe, at most one per family, at most three, and combined with equal
  capital. The result is written as frozen LESSONS with a fingerprint.

TEST (e.g. Aug 2017 to Sep 2021)
  Runs the frozen lessons once on data the lab never saw, against a gate declared below before any
  test was run. The ledger records every test of every lessons file; a second test of the same
  lessons is flagged, because a holdout used to decide a change is no longer a holdout.

Costs: 25 bps fee + 5 bps slippage + half the spread per side; shorts pay or receive funding
(0.01%/8h assumed where the archive has none, before Binance perps existed). Prices are Binance's.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import math
import random
import statistics
from pathlib import Path

from .henry_desk_data import load

TF = {"4h": 4*3600, "1d": 86400}
BARS_PER_DAY = {"4h": 6, "1d": 1}
FEE_BPS, SLIP_BPS = 25.0, 5.0
SPREAD_BPS = {"BTC-USD": 4.0, "ETH-USD": 4.0}
DEFAULT_SPREAD_BPS = 20.0
DEFAULT_FUNDING_8H = 0.0001
LEADER = "BTC-USD"
TARGET_DAILY_VOL = 0.025          # vol-targeted sizing, capped at 1x the sleeve (no leverage)
BLOCK_DAYS = 182

SELECTION = {"min_positive_block_share": 0.6, "worst_block_floor_pct": -15.0, "min_profit_factor": 1.2,
             "min_trades": 20, "max_selected": 3, "one_per_family": True, "must_beat_luck_bar": True}
# Declared before any test run. The test is judged against this and nothing else.
TEST_GATE = {"min_return_pct": 0.0, "min_sharpe": 0.5, "max_drawdown_pct": 35.0, "drawdown_below_btc_hold": True,
             "min_share_of_years_profitable": 0.5, "worst_year_floor_pct": -20.0}


def one_side_cost(symbol):
    return (FEE_BPS+SLIP_BPS+SPREAD_BPS.get(symbol, DEFAULT_SPREAD_BPS)/2)/10000


# ------------------------------------------------------------------ data
def merge(datasets):
    out = {}
    for d in datasets:
        for sym, s in d["symbols"].items():
            m = out.setdefault(sym, {"bars": {}, "funding": {}})
            for b in s["bars"]:
                m["bars"][b[0]] = b
            for f in s.get("funding", []):
                m["funding"][f[0]] = f[1]
    return {sym: {"bars": [v["bars"][t] for t in sorted(v["bars"])],
                  "funding": sorted(v["funding"].items())} for sym, v in out.items()}


def resample(hourly, period):
    """Complete higher-timeframe bars only: every hour of the bucket must be present."""
    out, cur, n = [], None, 0
    need = period//3600
    for t, o, h, l, c, v in hourly:
        b = t-t % period
        if cur is None or cur[0] != b:
            if cur and n == need:
                out.append(cur)
            cur, n = [b, o, h, l, c, v], 1
        else:
            cur[2], cur[3], cur[4], cur[5] = max(cur[2], h), min(cur[3], l), c, cur[5]+v
            n += 1
    if cur and n == need:
        out.append(cur)
    return out


def shuffled(hourly, seed):
    """Same bars, random order: keeps each bar's size and shape, destroys every real pattern."""
    rng = random.Random(seed)
    steps = []
    for k in range(1, len(hourly)):
        pc = hourly[k-1][4]
        t, o, h, l, c, v = hourly[k]
        steps.append((o/pc, h/c, l/c, c/pc, v))
    rng.shuffle(steps)
    out, prev = [hourly[0]], hourly[0][4]
    for k, (ro, rh, rl, rc, v) in enumerate(steps, start=1):
        c = prev*rc
        o = prev*ro
        out.append([hourly[k][0], o, max(c*rh, o, c), min(c*rl, o, c), c, v])
        prev = c
    return out


# ------------------------------------------------------------------ indicators
def sma(x, n):
    out, s = [], 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i-n]
        out.append(s/n if i >= n-1 else None)
    return out


def atr(h, l, c, n=14):
    tr = [h[0]-l[0]]+[max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, len(c))]
    return sma(tr, n)


def rsi(c, n):
    out, gain, loss = [None]*len(c), 0.0, 0.0
    for i in range(1, len(c)):
        d = c[i]-c[i-1]
        g, lo = max(d, 0), max(-d, 0)
        if i <= n:
            gain, loss = gain+g/n, loss+lo/n
            if i == n:
                out[i] = 100 if loss == 0 else 100-100/(1+gain/loss)
        else:
            gain, loss = (gain*(n-1)+g)/n, (loss*(n-1)+lo)/n
            out[i] = 100 if loss == 0 else 100-100/(1+gain/loss)
    return out


def rolling_vol(c, n=20):
    rets = [0.0]+[math.log(c[i]/c[i-1]) for i in range(1, len(c))]
    out = []
    for i in range(len(c)):
        w = rets[max(1, i-n+1):i+1]
        out.append(statistics.pstdev(w) if len(w) >= 5 else None)
    return out


# ------------------------------------------------------------------ setup library
def library():
    out = []
    for tf, (n, m), side, filt in itertools.product(("4h", "1d"), ((20, 10), (55, 20)), ("long", "both"), (False, True)):
        out.append({"family": "donchian_breakout", "tf": tf, "entry": n, "exit": m, "side": side, "btc_filter": filt})
    for n, side, filt in itertools.product((50, 100, 200), ("long", "both"), (False, True)):
        out.append({"family": "ma_trend", "tf": "1d", "ma": n, "side": side, "btc_filter": filt})
    for lb, side, filt in itertools.product((30, 90), ("long", "both"), (False, True)):
        out.append({"family": "momentum", "tf": "1d", "lookback": lb, "side": side, "btc_filter": filt})
    for tf, th, filt in itertools.product(("4h", "1d"), (5, 10), (False, True)):
        out.append({"family": "trend_pullback", "tf": tf, "rsi_below": th, "side": "long", "btc_filter": filt})
    for tf, side, filt in itertools.product(("4h", "1d"), ("long", "both"), (False, True)):
        out.append({"family": "squeeze_breakout", "tf": tf, "side": side, "btc_filter": filt})
    for tf, side, filt in itertools.product(("4h", "1d"), ("long", "both"), (False, True)):
        out.append({"family": "fair_value_gap", "tf": tf, "side": side, "btc_filter": filt})
    for cfg in out:
        cfg["id"] = config_id(cfg)
    return out


def config_id(cfg):
    keys = [k for k in sorted(cfg) if k not in ("id",)]
    return "|".join(f"{k}={cfg[k]}" for k in keys)


# ------------------------------------------------------------------ per-symbol simulation
class Leader:
    """BTC daily trend, causal: usable at time t only once the day has closed."""

    def __init__(self, daily):
        self.t = [b[0] for b in daily]
        self.c = [b[4] for b in daily]
        self.ma = sma(self.c, 100)

    def side_ok(self, t, side):
        import bisect
        k = bisect.bisect_right(self.t, t-86400)-1  # last day whose close is <= t
        if k < 0 or self.ma[k] is None:
            return False
        return self.c[k] > self.ma[k] if side > 0 else self.c[k] < self.ma[k]


def funding_per_bar(bars, funding, period):
    import bisect
    ft = [f[0] for f in funding]
    out = []
    for b in bars:
        lo, hi = bisect.bisect_right(ft, b[0]), bisect.bisect_right(ft, b[0]+period)
        if ft and b[0] >= ft[0]:
            out.append(sum(f[1] for f in funding[lo:hi]))
        else:
            out.append(DEFAULT_FUNDING_8H*period/(8*3600))
    return out


def simulate(cfg, symbol, bars, funding, leader):
    """Decide at each bar's close, act from the next bar. Returns per-bar (t, net return of the
    sleeve, position) and the closed trades."""
    tf, period = cfg["tf"], TF[cfg["tf"]]
    t = [b[0] for b in bars]
    o, h, l, c = ([b[k] for b in bars] for k in (1, 2, 3, 4))
    a = atr(h, l, c)
    vol = rolling_vol(c)
    fund = funding_per_bar(bars, funding, period)
    fam = cfg["family"]
    if fam == "ma_trend":
        ma = sma(c, cfg["ma"])
    elif fam == "trend_pullback":
        ma200, ma5, r2 = sma(c, 200*BARS_PER_DAY[tf] if tf == "4h" else 200), sma(c, 5), rsi(c, 2)
    elif fam == "squeeze_breakout":
        mid = sma(c, 20)
        sd = [statistics.pstdev(c[max(0, i-19):i+1]) if i >= 19 else None for i in range(len(c))]
        bw = [(4*sd[i]/mid[i]) if mid[i] and sd[i] is not None else None for i in range(len(c))]
    if fam == "fair_value_gap":
        ma50 = sma(c, 50)
    gaps = []  # live fair value gaps: [direction, bottom, top, formed_at]
    plan = {"stop": None, "target": None}
    cost = one_side_cost(symbol)
    use_filter = cfg["btc_filter"] and symbol != LEADER and leader is not None
    warm = {"fair_value_gap": 55, "donchian_breakout": cfg.get("entry", 0)+2, "ma_trend": cfg.get("ma", 0)+6,
            "momentum": cfg.get("lookback", 0)+1, "trend_pullback": (200*BARS_PER_DAY[tf] if tf == "4h" else 200)+2,
            "squeeze_breakout": 125}[fam]
    pos, stop, entry_i, trade_ret, trade_open = 0.0, None, None, 1.0, None
    rows, trades = [], []

    def want(i):
        """Desired direction at the close of bar i (-1, 0, +1), given the current position."""
        d = 0 if pos == 0 else (1 if pos > 0 else -1)
        if fam == "donchian_breakout":
            n, m = cfg["entry"], cfg["exit"]
            hi_n, lo_n = max(h[i-n:i]), min(l[i-n:i])
            hi_m, lo_m = max(h[i-m:i]), min(l[i-m:i])
            if d > 0:
                return 0 if c[i] < lo_m else 1
            if d < 0:
                return 0 if c[i] > hi_m else -1
            return 1 if c[i] > hi_n else -1 if c[i] < lo_n else 0
        if fam == "ma_trend":
            n = cfg["ma"]
            up = c[i] > ma[i] and ma[i] > ma[i-5]
            down = c[i] < ma[i] and ma[i] < ma[i-5]
            return 1 if up else -1 if down else (d if d > 0 and c[i] > ma[i] else d if d < 0 and c[i] < ma[i] else 0)
        if fam == "momentum":
            r = c[i]/c[i-cfg["lookback"]]-1
            return 1 if r > 0 else -1 if r < 0 else 0
        if fam == "trend_pullback":
            if d > 0:
                return 0 if (c[i] > ma5[i] or i-entry_i >= 10) else 1
            return 1 if (ma200[i] and c[i] > ma200[i] and r2[i] is not None and r2[i] < cfg["rsi_below"]) else 0
        if fam == "fair_value_gap":
            return fvg(i, d)
        if fam == "squeeze_breakout":
            if d > 0:
                return 0 if c[i] < mid[i] else 1
            if d < 0:
                return 0 if c[i] > mid[i] else -1
            window = [x for x in bw[i-120:i] if x is not None]
            if not window or bw[i] is None:
                return 0
            squeezed = sorted(window)[len(window)//5] >= bw[i-1] if bw[i-1] is not None else False
            if not squeezed:
                return 0
            return 1 if c[i] > mid[i]+2*sd[i] else -1 if c[i] < mid[i]-2*sd[i] else 0
        return 0

    def fvg(i, d):
        """Fair value gap: candle i-2 and candle i leave an untraded gap. In trend, buy (sell) the
        first retrace into a bullish (bearish) gap that holds; stop beyond the gap, 2R target,
        20-bar time limit. Gaps under half an ATR are ignored as noise; gaps expire after 20 bars
        or once price closes through them."""
        if i >= 2 and a[i] is not None:
            if l[i] > h[i-2] and l[i]-h[i-2] >= 0.5*a[i]:
                gaps.append([1, h[i-2], l[i], i])
            if h[i] < l[i-2] and l[i-2]-h[i] >= 0.5*a[i]:
                gaps.append([-1, h[i], l[i-2], i])
        gaps[:] = [g for g in gaps if i-g[3] <= 20 and not (g[0] > 0 and c[i] < g[1]) and not (g[0] < 0 and c[i] > g[2])]
        if d > 0:
            if plan["target"] and c[i] >= plan["target"]:
                return 0
            return 0 if i-entry_i >= 20 else 1
        if d < 0:
            if plan["target"] and c[i] <= plan["target"]:
                return 0
            return 0 if i-entry_i >= 20 else -1
        if ma50[i] is None:
            return 0
        for g in reversed(gaps):
            if g[3] == i:
                continue  # needs a retrace after the gap forms
            if g[0] > 0 and c[i] > ma50[i] and l[i] <= g[2] and c[i] >= g[1]:
                plan["stop"] = g[1]-0.25*a[i]
                plan["target"] = c[i]+2*(c[i]-plan["stop"])
                gaps.remove(g)
                return 1
            if g[0] < 0 and c[i] < ma50[i] and h[i] >= g[1] and c[i] <= g[2]:
                plan["stop"] = g[2]+0.25*a[i]
                plan["target"] = c[i]-2*(plan["stop"]-c[i])
                gaps.remove(g)
                return -1
        return 0

    for i in range(len(bars)):
        # 1. earn this bar with the position decided at the previous close (intrabar stop first)
        r = 0.0
        if pos and i > 0:
            exit_px = None
            if pos > 0 and stop is not None and l[i] <= stop:
                exit_px = min(o[i], stop)
            elif pos < 0 and stop is not None and h[i] >= stop:
                exit_px = max(o[i], stop)
            if exit_px is not None:
                r = pos*(exit_px/c[i-1]-1)-abs(pos)*cost
                if pos < 0:
                    r += abs(pos)*fund[i]
                trade_ret *= 1+r
                trades.append({"symbol": symbol, "open": trade_open, "close": t[i], "ret": trade_ret-1, "reason": "stop"})
                pos, stop = 0.0, None
            else:
                r = pos*(c[i]/c[i-1]-1)
                if pos < 0:
                    r += abs(pos)*fund[i]
                trade_ret *= 1+r
        # 2. decide at this close
        new = 0
        if i >= warm and a[i] is not None and vol[i]:
            new = want(i)
            if cfg["side"] == "long" and new < 0:
                new = 0
            if new and use_filter and not leader.side_ok(t[i], new):
                new = 0 if (pos == 0 or (pos > 0) != (new > 0)) else new
        cur = 0 if pos == 0 else (1 if pos > 0 else -1)
        if new != cur:
            if pos:  # close at this close
                r -= abs(pos)*cost
                trade_ret *= 1-abs(pos)*cost
                trades.append({"symbol": symbol, "open": trade_open, "close": t[i], "ret": trade_ret-1, "reason": "signal"})
                pos, stop = 0.0, None
                if fam == "fair_value_gap" and not new:
                    plan["stop"] = plan["target"] = None
            if new:
                size = min(1.0, TARGET_DAILY_VOL/(vol[i]*math.sqrt(BARS_PER_DAY[tf])))
                pos = new*size
                stop = plan["stop"] if (fam == "fair_value_gap" and plan["stop"] is not None) else c[i]-new*3*a[i]
                entry_i, trade_open, trade_ret = i, t[i], 1-size*cost
                r -= size*cost
        elif pos and fam in ("donchian_breakout", "momentum", "ma_trend"):
            trail = c[i]-cur*3*a[i]  # catastrophe stop trails at 3 ATR
            stop = max(stop, trail) if cur > 0 else min(stop, trail)
        rows.append((t[i], r, pos))
    return rows, trades


# ------------------------------------------------------------------ portfolio and stats
def day_of(t):
    return t-t % 86400


_CACHE = {}


def bars_for(data, sym, tf):
    key = (id(data), sym, tf)
    if key not in _CACHE:
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = resample(data[sym]["bars"], TF[tf])
    return _CACHE[key]


def run_config(cfg, data, leader):
    """Equal-weight sleeves across the coins that exist on each day; daily net returns."""
    per_day, trades = {}, []
    for sym, d in data.items():
        bars = bars_for(data, sym, cfg["tf"])
        if len(bars) < 250:
            continue
        rows, tr = simulate(cfg, sym, bars, d["funding"], leader)
        trades += tr
        daily = {}
        for t, r, _ in rows:
            k = day_of(t)
            daily[k] = daily.get(k, 1.0)*(1+r)
        for k, g in daily.items():
            per_day.setdefault(k, []).append(g-1)
    days = sorted(per_day)
    return [(k, sum(per_day[k])/len(per_day[k])) for k in days], trades


def stats(daily, trades, leader_daily=None):
    if not daily:
        return {"return_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0, "trades": 0, "profit_factor": 0.0,
                "blocks": [], "by_year": {}}
    eq, peak, dd, curve = 1.0, 1.0, 0.0, []
    for k, r in daily:
        eq *= 1+r
        peak = max(peak, eq)
        dd = max(dd, 1-eq/peak)
        curve.append((k, eq))
    rets = [r for _, r in daily]
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0
    sharpe = statistics.mean(rets)/sd*math.sqrt(365) if sd else 0.0
    wins = sum(t["ret"] for t in trades if t["ret"] > 0)
    losses = -sum(t["ret"] for t in trades if t["ret"] <= 0)
    pf = wins/losses if losses > 0 else (99.0 if wins > 0 else 0.0)
    blocks, start = [], daily[0][0]
    for k, r in daily:
        idx = int((k-start)//(BLOCK_DAYS*86400))
        while len(blocks) <= idx:
            blocks.append(1.0)
        blocks[idx] *= 1+r
    by_year = {}
    for k, r in daily:
        y = str(dt.datetime.fromtimestamp(k, dt.timezone.utc).year)
        by_year[y] = by_year.get(y, 1.0)*(1+r)
    return {"return_pct": round((eq-1)*100, 2), "sharpe": round(sharpe, 3), "max_drawdown_pct": round(dd*100, 2),
            "trades": len(trades), "profit_factor": round(pf, 3),
            "win_rate_pct": round(sum(t["ret"] > 0 for t in trades)/len(trades)*100, 1) if trades else 0.0,
            "time_in_market_pct": None,
            "blocks": [round((b-1)*100, 2) for b in blocks],
            "by_year": {y: round((g-1)*100, 2) for y, g in by_year.items()}}


def btc_hold(data):
    bars = resample(data[LEADER]["bars"], 86400)
    daily = [(bars[0][0], bars[0][4]/bars[0][1]-1)]+[(bars[i][0], bars[i][4]/bars[i-1][4]-1) for i in range(1, len(bars))]
    return daily


def regime_breakdown(daily, data):
    """Strategy vs BTC hold, summed daily returns, by BTC regime (200-day MA and its 20-day slope)."""
    bars = resample(data[LEADER]["bars"], 86400)
    c = [b[4] for b in bars]
    ma = sma(c, 200)
    label = {}
    for i, b in enumerate(bars):
        if ma[i] is None or i < 220:
            continue
        up, down = c[i] > ma[i] and ma[i] > ma[i-20], c[i] < ma[i] and ma[i] < ma[i-20]
        label[b[0]+86400] = "bull" if up else "bear" if down else "range"  # known after the close
    hold = dict(btc_hold(data))
    out = {}
    for k, r in daily:
        lab = label.get(k)
        if not lab:
            continue
        g = out.setdefault(lab, {"days": 0, "henry_pct": 0.0, "btc_hold_pct": 0.0})
        g["days"] += 1
        g["henry_pct"] += r*100
        g["btc_hold_pct"] += hold.get(k, 0)*100
    return {k: {**v, "henry_pct": round(v["henry_pct"], 1), "btc_hold_pct": round(v["btc_hold_pct"], 1)} for k, v in out.items()}


# ------------------------------------------------------------------ training
def survives(s, luck_bar):
    blocks = s["blocks"]
    pos_share = sum(b > 0 for b in blocks)/len(blocks) if blocks else 0
    reasons = []
    if pos_share < SELECTION["min_positive_block_share"]:
        reasons.append(f"positive in {pos_share*100:.0f}% of blocks")
    if blocks and min(blocks) < SELECTION["worst_block_floor_pct"]:
        reasons.append(f"worst block {min(blocks)}%")
    if s["profit_factor"] < SELECTION["min_profit_factor"]:
        reasons.append(f"profit factor {s['profit_factor']}")
    if s["trades"] < SELECTION["min_trades"]:
        reasons.append(f"only {s['trades']} trades")
    if SELECTION["must_beat_luck_bar"] and s["sharpe"] <= luck_bar:
        reasons.append(f"Sharpe {s['sharpe']} does not beat the luck bar {luck_bar}")
    return not reasons, reasons


def luck_bar(data, configs, shuffles, log):
    best = -9.0
    for k in range(shuffles):
        fake = {sym: {"bars": shuffled(d["bars"], seed=1000+k*31+j), "funding": d["funding"]}
                for j, (sym, d) in enumerate(sorted(data.items()))}
        leader = Leader(resample(fake[LEADER]["bars"], 86400)) if LEADER in fake else None
        for cfg in configs:
            daily, trades = run_config(cfg, fake, leader)
            best = max(best, stats(daily, trades)["sharpe"])
        log(f"  shuffle {k+1}/{shuffles}: best Sharpe found by luck so far {best:.3f}")
    return round(best, 3)


def train(data, shuffles=2, log=print):
    configs = library()
    leader = Leader(resample(data[LEADER]["bars"], 86400)) if LEADER in data else None
    log(f"training on {len(data)} coins, {len(configs)} setups")
    results = []
    for cfg in configs:
        daily, trades = run_config(cfg, data, leader)
        results.append((cfg, stats(daily, trades), daily))
    log("measuring the luck bar on shuffled data")
    bar = luck_bar(data, configs, shuffles, log)
    board = []
    for cfg, s, _ in results:
        ok, why = survives(s, bar)
        board.append({"id": cfg["id"], "family": cfg["family"], "survived": ok, "why_not": why,
                      **{k: s[k] for k in ("return_pct", "sharpe", "max_drawdown_pct", "trades", "profit_factor", "blocks")}})
    survivors = sorted([b for b in board if b["survived"]], key=lambda b: -b["sharpe"])
    chosen, families = [], set()
    for b in survivors:
        if SELECTION["one_per_family"] and b["family"] in families:
            continue
        chosen.append(next(cfg for cfg, _, _ in results if cfg["id"] == b["id"]))
        families.add(b["family"])
        if len(chosen) >= SELECTION["max_selected"]:
            break
    ens_daily, ens_trades = ensemble(chosen, data, leader) if chosen else ([], [])
    family_summary = {}
    for b in board:
        f = family_summary.setdefault(b["family"], {"tested": 0, "survived": 0, "best_sharpe": -9.0})
        f["tested"] += 1
        f["survived"] += b["survived"]
        f["best_sharpe"] = max(f["best_sharpe"], b["sharpe"])
    span = (min(k for k, _ in results[0][2]) if results[0][2] else 0, max(k for k, _ in results[0][2]) if results[0][2] else 0)
    lessons = {"lab": "henry-lab-v1", "trained_on": {"from": span[0], "to": span[1], "coins": sorted(data)},
               "selection_rules": SELECTION, "luck_bar_sharpe": bar, "setups_tested": len(configs),
               "shuffles": shuffles, "chosen": chosen, "survivors": len(survivors),
               "training_result": stats(ens_daily, ens_trades) if chosen else None,
               "training_by_regime": regime_breakdown(ens_daily, data) if chosen else None,
               "family_summary": family_summary, "leaderboard": sorted(board, key=lambda b: -b["sharpe"])}
    lessons["fingerprint"] = fingerprint(lessons)
    return lessons


def fingerprint(lessons):
    core = {k: lessons[k] for k in ("lab", "chosen", "selection_rules", "luck_bar_sharpe", "trained_on")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


def ensemble(chosen, data, leader):
    """Equal capital per chosen setup, rebalanced daily."""
    curves, trades = [], []
    for cfg in chosen:
        d, tr = run_config(cfg, data, leader)
        curves.append(dict(d))
        trades += tr
    days = sorted(set().union(*curves))
    return [(k, sum(c.get(k, 0.0) for c in curves)/len(curves)) for k in days], trades


# ------------------------------------------------------------------ the one-shot test
def test(data, lessons, ledger_path=None):
    if fingerprint(lessons) != lessons.get("fingerprint"):
        raise ValueError("lessons file was edited after training; refusing to test it")
    prior = []
    if ledger_path and Path(ledger_path).exists():
        prior = [e for e in json.loads(Path(ledger_path).read_text()) if e["fingerprint"] == lessons["fingerprint"]]
    if not lessons["chosen"]:
        return {"verdict": "NOTHING TO TEST", "reason": "no setup survived training"}
    leader = Leader(resample(data[LEADER]["bars"], 86400)) if LEADER in data else None
    daily, trades = ensemble(lessons["chosen"], data, leader)
    s = stats(daily, trades)
    hold = stats(btc_hold(data), [])
    years = list(s["by_year"].values())
    checks = {
        "beats_cash": (s["return_pct"] > TEST_GATE["min_return_pct"], f"{s['return_pct']}%"),
        "sharpe": (s["sharpe"] >= TEST_GATE["min_sharpe"], f"{s['sharpe']} vs {TEST_GATE['min_sharpe']}"),
        "drawdown": (s["max_drawdown_pct"] <= TEST_GATE["max_drawdown_pct"], f"{s['max_drawdown_pct']}% vs {TEST_GATE['max_drawdown_pct']}%"),
        "drawdown_below_btc_hold": (s["max_drawdown_pct"] < hold["max_drawdown_pct"],
                                    f"{s['max_drawdown_pct']}% vs BTC hold {hold['max_drawdown_pct']}%"),
        "most_years_profitable": (sum(y > 0 for y in years)/len(years) >= TEST_GATE["min_share_of_years_profitable"] if years else False,
                                  f"{sum(y > 0 for y in years)} of {len(years)} years"),
        "no_disaster_year": (min(years) >= TEST_GATE["worst_year_floor_pct"] if years else False,
                             f"worst year {min(years) if years else None}%"),
    }
    result = {"verdict": "PASS" if all(v[0] for v in checks.values()) else "FAIL",
              "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in checks.items()},
              "result": s, "btc_hold": {k: hold[k] for k in ("return_pct", "sharpe", "max_drawdown_pct", "by_year")},
              "by_regime": regime_breakdown(daily, data), "fingerprint": lessons["fingerprint"],
              "previous_tests_of_these_lessons": len(prior),
              "per_setup": {cfg["id"]: {k: v for k, v in stats(*run_config(cfg, data, leader)).items() if k != "blocks"}
                            for cfg in lessons["chosen"]}}
    if ledger_path:
        entries = json.loads(Path(ledger_path).read_text()) if Path(ledger_path).exists() else []
        entries.append({"fingerprint": lessons["fingerprint"], "verdict": result["verdict"],
                        "return_pct": s["return_pct"], "at": dt.datetime.now(dt.timezone.utc).isoformat()})
        Path(ledger_path).write_text(json.dumps(entries, indent=1))
    return result


# ------------------------------------------------------------------ CLI
def show_lessons(L):
    print(f"Henry lab: trained on {', '.join(L['trained_on']['coins'])} "
          f"({dt.datetime.fromtimestamp(L['trained_on']['from'], dt.timezone.utc):%Y-%m-%d} to "
          f"{dt.datetime.fromtimestamp(L['trained_on']['to'], dt.timezone.utc):%Y-%m-%d})")
    print(f"  {L['setups_tested']} setups tested; luck bar (best Sharpe on shuffled data): {L['luck_bar_sharpe']}")
    print(f"  {L['survivors']} survived the walk-forward rules")
    print("  by family:")
    for f, v in L["family_summary"].items():
        print(f"     {f:20} tested {v['tested']:>2}  survived {v['survived']:>2}  best Sharpe {v['best_sharpe']}")
    print("  top of the leaderboard:")
    for b in L["leaderboard"][:12]:
        print(f"     {'KEEP' if b['survived'] else 'drop'}  Sharpe {b['sharpe']:>6}  {b['return_pct']:>8}%  dd {b['max_drawdown_pct']:>5}%  "
              f"pf {b['profit_factor']:>5}  trades {b['trades']:>4}  {b['id']}"
              + ("" if b["survived"] else f"\n           why not: {'; '.join(b['why_not'])}"))
    if L["chosen"]:
        print("  LESSONS (frozen):")
        for cfg in L["chosen"]:
            print(f"     {cfg['id']}")
        r = L["training_result"]
        print(f"  combined on training data: {r['return_pct']}%  Sharpe {r['sharpe']}  dd {r['max_drawdown_pct']}%  "
              f"pf {r['profit_factor']}  blocks {r['blocks']}")
        print(f"  by regime: {json.dumps(L['training_by_regime'])}")
    else:
        print("  LESSONS: none. Nothing beat both the walk-forward rules and the luck bar. Do not test.")
    print(f"  fingerprint {L['fingerprint'][:16]}")


def show_test(r):
    print(f"Henry lab one-shot test -> {r['verdict']}")
    if "checks" not in r:
        print("  ", r.get("reason"))
        return
    if r["previous_tests_of_these_lessons"]:
        print(f"  !! these lessons were already tested {r['previous_tests_of_these_lessons']} time(s): "
              "this holdout is spent for them; treat this result as in-sample")
    for k, v in r["checks"].items():
        print(f"   {'ok ' if v['pass'] else 'XX '} {k}: {v['detail']}")
    s, h = r["result"], r["btc_hold"]
    print(f"  Henry: {s['return_pct']}%  Sharpe {s['sharpe']}  dd {s['max_drawdown_pct']}%  trades {s['trades']}  "
          f"pf {s['profit_factor']}  win {s['win_rate_pct']}%")
    print(f"  BTC hold: {h['return_pct']}%  Sharpe {h['sharpe']}  dd {h['max_drawdown_pct']}%")
    print("  by year (Henry vs BTC hold):")
    for y, v in s["by_year"].items():
        print(f"     {y}  Henry {v:>8}%   BTC {h['by_year'].get(y):>8}%")
    print(f"  by regime: {json.dumps(r['by_regime'])}")
    for k, v in r["per_setup"].items():
        print(f"  setup {k}: {v['return_pct']}%  Sharpe {v['sharpe']}  dd {v['max_drawdown_pct']}%  trades {v['trades']}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    tr = sub.add_parser("train")
    tr.add_argument("--data", action="append", required=True)
    tr.add_argument("--out", required=True)
    tr.add_argument("--shuffles", type=int, default=2)
    te = sub.add_parser("test")
    te.add_argument("--data", action="append", required=True)
    te.add_argument("--lessons", required=True)
    te.add_argument("--ledger")
    args = p.parse_args(argv)
    data = merge([load(x) for x in args.data])
    if args.cmd == "train":
        lessons = train(data, args.shuffles)
        Path(args.out).write_text(json.dumps(lessons, default=str))
        show_lessons(lessons)
    else:
        lessons = json.loads(Path(args.lessons).read_text())
        show_test(test(data, lessons, args.ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
