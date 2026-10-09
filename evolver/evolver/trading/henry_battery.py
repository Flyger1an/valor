"""Robustness battery for ONE frozen rule: daily 50-day MA trend, long only, alts follow BTC.

  fetch  python -m evolver.trading.henry_battery fetch --out /data/battery.json.gz          (network)
  run    python -m evolver.trading.henry_battery run --battery /data/battery.json.gz \
             --stress-data /data/henry_lab_2017_2021.json.gz                                (offline)

The rule is not tuned here. It runs unchanged on data it never saw and is judged by a scorecard
written before any of that data was fetched (SCORECARD below):

  1. BTC before Binance (Bitstamp, 2013 to Aug 2017): the 2014 Mt. Gox collapse.
  2. Unseen coins (Binance, Aug 2017 to Sep 2021, and Oct 2021 to now), including coins that died
     or collapsed (LUNA, FTT, WAVES, SRM, EOS, ICP ...), so survivors do not flatter the result.
     A ticker reused for a different coin (LUNA after Terra) is split at the break and each piece
     treated as its own listing.
  3. Outside crypto (Stooq daily, 1990 to now): S&P 500, Nasdaq 100, gold, oil, long Treasuries,
     EUR/USD. No BTC filter there; costs of an ETF/futures trader.
  4. Stress on the 2017-21 file where it passed: act 1 and 2 days late, double costs, neighboring
     moving averages (30 to 100 days), and a block bootstrap of daily returns.

The rule's job is cutting crashes, not beating buy-and-hold in bull runs; the scorecard reflects that.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import math
import random
import statistics
import time
import urllib.request
from pathlib import Path

from . import henry_lab as L
from .henry_desk_data import BASE, _ms, load, months_back, parallel

RULE = {"family": "ma_trend", "tf": "1d", "ma": 50, "side": "long", "btc_filter": True}
RULE["id"] = L.config_id(RULE)

# Declared before any battery data was fetched.
SCORECARD = {
    "drawdown_below_hold_in_every_dataset": True,
    "profitable_share_of_datasets": 0.75,
    "dead_coins_loss_share_of_hold_loss_max": 0.5,   # on collapsed coins, lose at most half of what holding lost
    "lag_1d_sharpe_kept_min": 0.7,                    # acting a day late keeps >= 70% of the Sharpe
    "double_costs_still_profitable": True,
    "neighbors_ok_min": 4,                            # of 6 neighboring MAs: profitable with dd below hold
    "bootstrap_p_sharpe_positive_min": 0.9,
}
UNSEEN_COINS = ["XRP", "LTC", "BNB", "LINK", "DOGE", "XLM", "TRX", "BCH", "ETC", "EOS", "NEO", "IOTA", "ZEC", "DASH",
                "VET", "ATOM", "ALGO", "XTZ", "THETA", "SOL", "AVAX", "DOT", "UNI", "FIL", "ICP", "LUNA", "FTT",
                "WAVES", "SRM", "MATIC"]
DEAD_OR_COLLAPSED = {"LUNA", "FTT", "WAVES", "SRM", "EOS", "ICP", "IOTA", "NEO", "DASH", "ZEC", "THETA", "XTZ"}
TRADITIONAL = {"S&P 500": "^spx", "Nasdaq 100": "^ndx", "Gold": "xauusd", "Crude oil": "cl.f",
               "Long Treasuries (TLT)": "tlt.us", "EUR/USD": "eurusd"}
TRAD_COSTS = {"fee_bps": 1.0, "slip_bps": 1.0, "spread_bps": 2.0}
ERA_SPLIT = 1_633_046_400  # 2021-10-01


# ------------------------------------------------------------------ fetch
def _get(url, timeout=30, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"user-agent": "valor-research/1.0"})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception:  # network, 4xx/5xx: retry, then give up and report the source missing
            time.sleep(2**k)
    return None


def fetch_bitstamp(log=print):
    out, start = {}, 1_313_625_600  # 2011-08-18
    end = time.time()
    while start < end:
        raw = _get(f"https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=86400&limit=1000&start={int(start)}")
        if not raw:
            break
        rows = json.loads(raw).get("data", {}).get("ohlc", [])
        if not rows:
            break
        for r in rows:
            out[int(r["timestamp"])] = [int(r["timestamp"]), float(r["open"]), float(r["high"]), float(r["low"]),
                                        float(r["close"]), float(r["volume"])]
        nxt = max(out)+86400
        if nxt <= start:
            break
        start = nxt
    log(f"  bitstamp BTC: {len(out)} days" if out else "  bitstamp BTC: unavailable")
    return [out[t] for t in sorted(out)]


def fetch_binance_daily(coin, months):
    pair = coin+"USDT"
    month_list = months_back(months)[:-1]
    out = {}
    for rows in parallel([f"{BASE}/spot/monthly/klines/{pair}/1d/{pair}-1d-{ym}.zip" for ym in month_list]):
        for r in rows or []:
            if r and r[0].isdigit():
                t = _ms(r[0])//1000
                out[t] = [t, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
    return [out[t] for t in sorted(out)]


def parse_stooq(text):
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            t = int(dt.datetime.strptime(r["Date"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
            o, h, lo, c = (float(r[k]) for k in ("Open", "High", "Low", "Close"))
        except (KeyError, ValueError, TypeError):
            continue
        if c > 0 and t >= 631_152_000:  # 1990-01-01
            rows.append([t, o, max(h, o, c), min(lo, o, c), c, float(r.get("Volume") or 0)])
    return rows


def fetch(log=print):
    data = {"built_at": time.time(), "bitstamp_btc": [], "binance_daily": {}, "traditional": {}, "missing": {}}
    log("Bitstamp BTC daily since 2011")
    data["bitstamp_btc"] = fetch_bitstamp(log)
    if not data["bitstamp_btc"]:
        data["missing"]["bitstamp_btc"] = "unreachable"
    months = (dt.datetime.now(dt.timezone.utc).year-2017)*12+dt.datetime.now(dt.timezone.utc).month-7
    for coin in ["BTC"]+UNSEEN_COINS:
        bars = fetch_binance_daily(coin, months)
        log(f"  binance {coin}: {len(bars)} days")
        if bars:
            data["binance_daily"][coin] = bars
        else:
            data["missing"][coin] = "not in the archive"
    for name, ticker in TRADITIONAL.items():
        raw = _get(f"https://stooq.com/q/d/l/?s={ticker}&i=d")
        rows = parse_stooq(raw.decode(errors="replace")) if raw else []
        log(f"  stooq {name}: {len(rows)} days")
        if len(rows) > 300:
            data["traditional"][name] = rows
        else:
            data["missing"][name] = "unavailable from stooq"
    return data


# ------------------------------------------------------------------ evaluation helpers
def split_listings(sym, bars, max_gap_days=7, max_up_jump=20.0):
    """Split a series wherever the ticker clearly changed hands: a long trading gap, or an upward
    jump no real market makes in a day (old LUNA at $0.0001 to new LUNA at $2). Crashes of any size
    are NEVER split: they are exactly what this battery is here to see."""
    pieces, cur = [], [bars[0]] if bars else []
    for prev, b in zip(bars, bars[1:]):
        gap = (b[0]-prev[0])/86400
        jump = b[4]/prev[4] if prev[4] else 1
        if gap > max_gap_days or jump > max_up_jump:
            pieces.append(cur)
            cur = []
        cur.append(b)
    if cur:
        pieces.append(cur)
    pieces = [p for p in pieces if len(p) >= 120]
    if len(pieces) <= 1:
        return {sym: pieces[0]} if pieces else {}
    return {f"{sym}#{k+1}": p for k, p in enumerate(pieces)}


def metrics(rets, per_year):
    """rets: list of daily returns."""
    if not rets:
        return {"return_pct": 0.0, "cagr_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0}
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1+r
        peak = max(peak, eq)
        dd = max(dd, 1-eq/peak)
    sd = statistics.pstdev(rets)
    years = len(rets)/per_year
    return {"return_pct": round((eq-1)*100, 1), "cagr_pct": round((eq**(1/years)-1)*100, 1) if years > 0 and eq > 0 else -100.0,
            "sharpe": round(statistics.mean(rets)/sd*math.sqrt(per_year), 2) if sd else 0.0,
            "max_drawdown_pct": round(dd*100, 1)}


def hold_returns(bars, start_index):
    return [bars[i][4]/bars[i-1][4]-1 for i in range(start_index+1, len(bars))]


def rule_on(bars, symbol, leader, rule=RULE, costs=None):
    """Per-bar returns of the frozen rule (via henry_lab's simulator) from its warmup on."""
    saved = (L.FEE_BPS, L.SLIP_BPS, dict(L.SPREAD_BPS), L.DEFAULT_SPREAD_BPS)
    try:
        if costs:
            L.FEE_BPS, L.SLIP_BPS = costs["fee_bps"], costs["slip_bps"]
            L.SPREAD_BPS, L.DEFAULT_SPREAD_BPS = {}, costs["spread_bps"]
        rows, trades = L.simulate(rule, symbol, bars, [], leader)
    finally:
        L.FEE_BPS, L.SLIP_BPS, L.SPREAD_BPS, L.DEFAULT_SPREAD_BPS = saved
    warm = rule["ma"]+6
    return [r for _, r, _ in rows[warm+1:]], [p for _, _, p in rows], trades, warm


def evaluate_set(series, leader, per_year, costs=None):
    """Each symbol vs its own buy-and-hold, plus an equal-weight portfolio of whatever is listed."""
    per_symbol, port, hold_port = {}, {}, {}
    for sym, bars in series.items():
        if len(bars) < RULE["ma"]+40:
            continue
        rets, _, trades, warm = rule_on(bars, sym.split("#")[0]+"-USD", leader, costs=costs)
        hold = hold_returns(bars, warm)
        s, h = metrics(rets, per_year), metrics(hold, per_year)
        per_symbol[sym] = {"rule": s, "hold": h, "trades": len(trades),
                           "from": dt.datetime.fromtimestamp(bars[0][0], dt.timezone.utc).strftime("%Y-%m"),
                           "to": dt.datetime.fromtimestamp(bars[-1][0], dt.timezone.utc).strftime("%Y-%m")}
        for k, (r, hr) in enumerate(zip(rets, hold)):
            t = bars[warm+1+k][0]
            port.setdefault(t, []).append(r)
            hold_port.setdefault(t, []).append(hr)
    days = sorted(port)
    p = metrics([sum(port[d])/len(port[d]) for d in days], per_year)
    hp = metrics([sum(hold_port[d])/len(hold_port[d]) for d in days], per_year)
    return {"portfolio": {"rule": p, "hold": hp}, "per_symbol": per_symbol}


def era(series, lo, hi):
    out = {}
    for sym, bars in series.items():
        cut = [b for b in bars if lo <= b[0] < hi]
        if len(cut) >= 120:
            out[sym] = cut
    return out


# ------------------------------------------------------------------ stress tests (on the passed 2017-21 file)
def replay_positions(bars, positions, symbol, lag=0, cost_mult=1.0):
    cost = L.one_side_cost(symbol)*cost_mult
    pos = [0.0]*lag+positions[:len(positions)-lag] if lag else positions
    out = []
    for i in range(1, len(bars)):
        r = pos[i-1]*(bars[i][4]/bars[i-1][4]-1)-abs(pos[i]-pos[i-1])*cost
        out.append(r)
    return out


def stress(stress_data, log=print):
    coins = {s: L.resample(d["bars"], 86400) for s, d in stress_data.items()}
    coins = {s: b for s, b in coins.items() if len(b) > 200}
    leader = L.Leader(coins[L.LEADER]) if L.LEADER in coins else None
    results = {}

    def portfolio(fn):
        per_day = {}
        for sym, bars in coins.items():
            for t, r in fn(sym, bars):
                per_day.setdefault(t, []).append(r)
        return [sum(per_day[t])/len(per_day[t]) for t in sorted(per_day)]

    positions = {sym: rule_on(bars, sym, leader)[1] for sym, bars in coins.items()}
    warm = RULE["ma"]+6

    def variant(lag=0, mult=1.0):
        return lambda sym, bars: [(bars[i+1][0], r) for i, r in
                                  enumerate(replay_positions(bars, positions[sym], sym, lag, mult)) if i+1 > warm]
    base = metrics(portfolio(variant()), 365)
    results["base"] = base
    results["lag_1d"] = metrics(portfolio(variant(lag=1)), 365)
    results["lag_2d"] = metrics(portfolio(variant(lag=2)), 365)
    results["double_costs"] = metrics(portfolio(variant(mult=2.0)), 365)
    hold = metrics(portfolio(lambda sym, bars: [(bars[i][0], bars[i][4]/bars[i-1][4]-1) for i in range(warm+1, len(bars))]), 365)
    results["hold"] = hold
    neighbors = {}
    for n in (30, 40, 60, 70, 80, 100):
        rule = {**RULE, "ma": n}
        rets = portfolio(lambda sym, bars, rule=rule: [(bars[rule["ma"]+7+k][0], r) for k, r in
                                                         enumerate(rule_on(bars, sym, leader, rule)[0])])
        neighbors[str(n)] = metrics(rets, 365)
    results["neighbors"] = neighbors
    daily = portfolio(variant())
    rng = random.Random(7)
    sharpes, dds, block = [], [], 20
    for _ in range(2000):
        sample = []
        while len(sample) < len(daily):
            s = rng.randrange(0, len(daily)-block)
            sample += daily[s:s+block]
        m = metrics(sample[:len(daily)], 365)
        sharpes.append(m["sharpe"])
        dds.append(m["max_drawdown_pct"])
    sharpes.sort()
    dds.sort()
    results["bootstrap"] = {"p_sharpe_positive": round(sum(x > 0 for x in sharpes)/len(sharpes), 3),
                            "sharpe_5_50_95": [sharpes[100], sharpes[1000], sharpes[1900]],
                            "max_drawdown_5_50_95": [dds[100], dds[1000], dds[1900]]}
    return results


# ------------------------------------------------------------------ the battery
def run(battery, stress_data=None, log=print):
    out = {"rule": RULE["id"], "scorecard": SCORECARD, "datasets": {}, "missing": battery.get("missing", {})}
    binance = {}
    for coin, bars in battery["binance_daily"].items():
        binance.update(split_listings(coin, bars))
    btc = battery["binance_daily"].get("BTC")
    leader = L.Leader(btc) if btc else None
    unseen = {k: v for k, v in binance.items() if not k.startswith("BTC")}
    if battery.get("bitstamp_btc"):
        pre = [b for b in battery["bitstamp_btc"] if b[0] < 1_502_928_000]  # before 2017-08-17
        if len(pre) > 400:
            out["datasets"]["BTC before Binance (Bitstamp 2011-2017)"] = evaluate_set({"BTC": pre}, None, 365)
    for name, (lo, hi) in {"Unseen coins, Aug 2017 to Sep 2021": (0, ERA_SPLIT),
                           "Unseen coins, Oct 2021 to now": (ERA_SPLIT, 4_102_444_800)}.items():
        s = era(unseen, lo, hi)
        if s:
            out["datasets"][name] = evaluate_set(s, leader, 365)
    for name, bars in battery.get("traditional", {}).items():
        out["datasets"][f"{name} (no BTC filter)"] = evaluate_set({name.replace(' ', '_'): bars}, None, 252, costs=TRAD_COSTS)
    dead = {}
    for sym, bars in unseen.items():
        if sym.split("#")[0] in DEAD_OR_COLLAPSED:
            r = evaluate_set({sym: bars}, leader, 365)["per_symbol"].get(sym)
            if r and r["hold"]["return_pct"] < -50:
                dead[sym] = r
    out["dead_coins"] = dead
    if stress_data:
        out["stress"] = stress(stress_data, log)
    out["verdict"] = score(out)
    return out


def score(out):
    checks = {}
    sets = out["datasets"]
    if sets:
        below = [n for n, d in sets.items() if d["portfolio"]["rule"]["max_drawdown_pct"] >= d["portfolio"]["hold"]["max_drawdown_pct"]]
        checks["drawdown_below_hold_everywhere"] = (not below, "all datasets" if not below else f"not in: {', '.join(below)}")
        prof = sum(d["portfolio"]["rule"]["return_pct"] > 0 for d in sets.values())
        checks["profitable_in_most_datasets"] = (prof/len(sets) >= SCORECARD["profitable_share_of_datasets"],
                                                 f"{prof} of {len(sets)} datasets")
    if out.get("dead_coins"):
        ratios = []
        for sym, r in out["dead_coins"].items():
            rule_loss = max(0.0, -r["rule"]["return_pct"])
            ratios.append(rule_loss/abs(r["hold"]["return_pct"]))
        worst = max(ratios)
        checks["dead_coins_contained"] = (statistics.mean(ratios) <= SCORECARD["dead_coins_loss_share_of_hold_loss_max"],
                                          f"rule lost {statistics.mean(ratios)*100:.0f}% of what holding lost on average "
                                          f"(worst {worst*100:.0f}%) across {len(ratios)} collapsed coins")
    st = out.get("stress")
    if st:
        kept = st["lag_1d"]["sharpe"]/st["base"]["sharpe"] if st["base"]["sharpe"] else 0
        checks["survives_a_day_of_lag"] = (kept >= SCORECARD["lag_1d_sharpe_kept_min"], f"keeps {kept*100:.0f}% of its Sharpe")
        checks["survives_double_costs"] = (st["double_costs"]["return_pct"] > 0, f"{st['double_costs']['return_pct']}%")
        ok = sum(m["return_pct"] > 0 and m["max_drawdown_pct"] < st["hold"]["max_drawdown_pct"] for m in st["neighbors"].values())
        checks["neighbors_agree"] = (ok >= SCORECARD["neighbors_ok_min"], f"{ok} of {len(st['neighbors'])} neighboring MAs")
        p = st["bootstrap"]["p_sharpe_positive"]
        checks["bootstrap"] = (p >= SCORECARD["bootstrap_p_sharpe_positive_min"], f"Sharpe > 0 in {p*100:.1f}% of resamples")
    return {"verdict": "PASS" if checks and all(v[0] for v in checks.values()) else "FAIL",
            "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in checks.items()}}


def show(out):
    v = out["verdict"]
    print(f"Robustness battery for {out['rule']}  ->  {v['verdict']}")
    for k, c in v["checks"].items():
        print(f"   {'ok ' if c['pass'] else 'XX '} {k}: {c['detail']}")
    if out["missing"]:
        print(f"  sources missing: {out['missing']}")
    for name, d in out["datasets"].items():
        r, h = d["portfolio"]["rule"], d["portfolio"]["hold"]
        print(f"\n  {name}")
        print(f"     rule: {r['return_pct']:>9}%  cagr {r['cagr_pct']:>6}%  Sharpe {r['sharpe']:>5}  dd {r['max_drawdown_pct']:>5}%")
        print(f"     hold: {h['return_pct']:>9}%  cagr {h['cagr_pct']:>6}%  Sharpe {h['sharpe']:>5}  dd {h['max_drawdown_pct']:>5}%")
        if len(d["per_symbol"]) > 1:
            rows = sorted(d["per_symbol"].items(), key=lambda kv: kv[1]["hold"]["return_pct"])
            for sym, s in rows:
                print(f"       {sym:10} {s['from']}..{s['to']}  rule {s['rule']['return_pct']:>8}% dd {s['rule']['max_drawdown_pct']:>5}%   "
                      f"hold {s['hold']['return_pct']:>9}% dd {s['hold']['max_drawdown_pct']:>5}%")
    if out.get("dead_coins"):
        print("\n  collapsed coins (holding lost > 50%):")
        for sym, r in sorted(out["dead_coins"].items(), key=lambda kv: kv[1]["hold"]["return_pct"]):
            print(f"     {sym:10} rule {r['rule']['return_pct']:>7}%   hold {r['hold']['return_pct']:>7}%")
    st = out.get("stress")
    if st:
        print("\n  stress tests on the 2017-21 coins:")
        for k in ("base", "lag_1d", "lag_2d", "double_costs", "hold"):
            m = st[k]
            print(f"     {k:13} {m['return_pct']:>8}%  Sharpe {m['sharpe']:>5}  dd {m['max_drawdown_pct']:>5}%")
        for n, m in st["neighbors"].items():
            print(f"     MA {n:>3}       {m['return_pct']:>8}%  Sharpe {m['sharpe']:>5}  dd {m['max_drawdown_pct']:>5}%")
        b = st["bootstrap"]
        print(f"     bootstrap: Sharpe 5/50/95th pct {b['sharpe_5_50_95']}, max dd {b['max_drawdown_5_50_95']}, "
              f"P(Sharpe>0) {b['p_sharpe_positive']}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--battery", required=True)
    r.add_argument("--stress-data")
    r.add_argument("--json")
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        data = fetch()
        with gzip.open(args.out, "wt") as fh:
            json.dump(data, fh)
        print(f"wrote {args.out}; missing: {data['missing'] or 'none'}")
        return 0
    with gzip.open(args.battery, "rt") as fh:
        battery = json.load(fh)
    stress_data = L.merge([load(args.stress_data)]) if args.stress_data else None
    out = run(battery, stress_data)
    show(out)
    if args.json:
        Path(args.json).write_text(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
