"""Funding carry research: is the perp funding premium real, and does timing it add anything?

The trade (cash-and-carry): buy the coin on spot, short the same notional on the perpetual future.
Price moves cancel; the short leg collects funding when it is positive (leveraged longs pay it).
P&L per day = funding received + (spot return - perp return), minus costs. Capital per unit of
notional = 1.5 (spot paid in full, perp short margined at 2x). Equal sleeves across coins.

  train  python -m evolver.trading.henry_carry train --data A.json.gz [--data B ...] --out carry_lessons.json
  test   python -m evolver.trading.henry_carry test --data T.json.gz --lessons carry_lessons.json --ledger L.json

Datasets must be built with henry_desk_data --perp (daily perp prices for the basis leg).

Two questions, judged differently (decided before any run):
  1. PREMIUM: always-on carry, no timing. One pre-specified hypothesis, so no luck bar. It must make
     money after costs in at least 60% of 6-month blocks and its mean daily funding must be at least
     3 standard errors above zero.
  2. TIMING: enter when trailing funding is high, exit when it fades. Many variants were tried, so each
     must beat a LUCK BAR (best Sharpe of any timing variant on day-shuffled funding: same premium,
     no persistence to exploit) AND beat always-on carry.
The lessons are the best surviving timing rule, else always-on carry if the premium is real, else
nothing. The test is a one-shot on an unseen era with the gate below.

Costs (a major perp venue, not Alpaca): 10 bps spot fee, 5 bps perp fee, 5 bps slippage per leg per
side, plus half the spread. Every run also reports the same strategy with Alpaca's 25 bps spot fee.
Execution needs a perp venue you can legally use. Exchange risk (an FTX) is not in any backtest.
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

COSTS = {"spot_fee_bps": 10.0, "perp_fee_bps": 5.0, "slip_bps": 5.0,
         "half_spread_bps": {"BTC-USD": 1.0, "ETH-USD": 1.0}, "default_half_spread_bps": 5.0}
ALPACA_SPOT_FEE_BPS = 25.0
CAPITAL_PER_NOTIONAL = 1.5
REBALANCE_MOVE = 0.30
MAJORS = ("BTC-USD", "ETH-USD")
BLOCK_DAYS = 182
PREMIUM_RULES = {"min_positive_block_share": 0.6, "min_funding_t_stat": 3.0}
TIMING_RULES = {"min_positive_block_share": 0.6, "worst_block_floor_pct": -5.0, "min_trades": 10,
                "must_beat_luck_bar": True, "must_beat_always_on": True}
TEST_GATE = {"min_return_pct": 0.0, "min_sharpe": 1.0, "max_drawdown_pct": 15.0,
             "min_share_of_years_profitable": 0.5, "worst_year_floor_pct": -5.0}


def day_of(t):
    return int(t-t % 86400)


# ------------------------------------------------------------------ data
def merge(datasets):
    out = {}
    for d in datasets:
        for sym, s in d["symbols"].items():
            m = out.setdefault(sym, {"bars": {}, "funding": {}, "perp": {}})
            for b in s["bars"]:
                m["bars"][b[0]] = b
            for f in s.get("funding", []):
                m["funding"][f[0]] = f[1]
            for p in s.get("perp_daily", []):
                m["perp"][p[0]] = p
    return out


def daily_view(m):
    """{day: {"spot": close, "perp": close|None, "funding": sum of the day's prints|None}}."""
    spot = {}
    for t in sorted(m["bars"]):
        spot[day_of(t)] = m["bars"][t][4]
    fund = {}
    for t, r in m["funding"].items():
        fund[day_of(t)] = fund.get(day_of(t), 0.0)+r
    perp = {day_of(t): p[4] for t, p in m["perp"].items()}
    return {d: {"spot": spot[d], "perp": perp.get(d), "funding": fund.get(d)} for d in sorted(spot)}


def shuffle_funding(view, seed):
    rng = random.Random(seed)
    days = [d for d in view if view[d]["funding"] is not None]
    vals = [view[d]["funding"] for d in days]
    rng.shuffle(vals)
    out = {d: dict(v) for d, v in view.items()}
    for d, f in zip(days, vals):
        out[d]["funding"] = f
    return out


# ------------------------------------------------------------------ strategy
def grid():
    out = [{"rule": "always_on", "universe": u} for u in ("all", "majors")]
    for lb, entry, exit_frac, u in itertools.product((3, 7, 14), (5.0, 10.0, 20.0), (0.0, 0.5), ("all", "majors")):
        out.append({"rule": "timed", "lookback_days": lb, "entry_apr": entry, "exit_apr": entry*exit_frac, "universe": u})
    for cfg in out:
        cfg["id"] = "|".join(f"{k}={cfg[k]}" for k in sorted(cfg))
    return out


def leg_cost(symbol, spot_fee_bps):
    half = COSTS["half_spread_bps"].get(symbol, COSTS["default_half_spread_bps"])
    return (spot_fee_bps+COSTS["perp_fee_bps"]+2*COSTS["slip_bps"]+2*half)/10000  # both legs, one side


def simulate(cfg, symbol, view, spot_fee_bps=None):
    """Daily returns on the sleeve's capital and the carry episodes. Decide at a day's close,
    hold from the next day."""
    spot_fee = COSTS["spot_fee_bps"] if spot_fee_bps is None else spot_fee_bps
    cost = leg_cost(symbol, spot_fee)
    days = list(view)
    rows, trades = [], []
    on, ref, episode, history = False, None, 1.0, []
    for k, d in enumerate(days):
        v = view[d]
        r = 0.0
        if on and k:
            prev = view[days[k-1]]
            f = v["funding"] or 0.0
            basis = 0.0
            if v["perp"] and prev["perp"]:
                basis = (v["spot"]/prev["spot"]-1)-(v["perp"]/prev["perp"]-1)
            r = f+basis
            if ref and abs(v["spot"]/ref-1) > REBALANCE_MOVE:  # top up the short's margin, resize legs
                r -= REBALANCE_MOVE*cost
                ref = v["spot"]
            episode *= 1+r
        if v["funding"] is not None:
            history.append(v["funding"])
        want = on
        if cfg["rule"] == "always_on":
            want = v["funding"] is not None
        else:
            lb = cfg["lookback_days"]
            if v["funding"] is None or len(history) < lb:
                want = False
            else:
                apr = statistics.mean(history[-lb:])*365*100
                want = apr > cfg["entry_apr"] if not on else apr >= cfg["exit_apr"]
        if want != on:
            r -= cost
            episode *= 1-cost
            if on:
                trades.append({"symbol": symbol, "close": d, "ret": episode-1})
            else:
                episode, ref = 1-cost, v["spot"]
            on = want
        rows.append((d, r/CAPITAL_PER_NOTIONAL, on))
    if on:
        trades.append({"symbol": symbol, "close": days[-1], "ret": episode-1, "open_at_end": True})
    return rows, trades


def run(cfg, views, spot_fee_bps=None):
    per_day, trades, exposure = {}, [], {}
    for sym, view in views.items():
        if cfg["universe"] == "majors" and sym not in MAJORS:
            continue
        rows, tr = simulate(cfg, sym, view, spot_fee_bps)
        trades += tr
        for d, r, on in rows:
            per_day.setdefault(d, []).append(r)
            exposure.setdefault(d, []).append(on)
    days = sorted(per_day)
    daily = [(d, sum(per_day[d])/len(per_day[d])) for d in days]
    in_market = sum(any(exposure[d]) for d in days)/len(days)*100 if days else 0.0
    return daily, trades, round(in_market, 1)


def stats(daily, trades):
    if not daily:
        return {"return_pct": 0.0, "apr_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0, "trades": 0,
                "profit_factor": 0.0, "blocks": [], "by_year": {}}
    eq, peak, dd = 1.0, 1.0, 0.0
    for _, r in daily:
        eq *= 1+r
        peak = max(peak, eq)
        dd = max(dd, 1-eq/peak)
    rets = [r for _, r in daily]
    sd = statistics.pstdev(rets)
    years = len(daily)/365
    blocks, start, by_year = [], daily[0][0], {}
    for d, r in daily:
        i = int((d-start)//(BLOCK_DAYS*86400))
        while len(blocks) <= i:
            blocks.append(1.0)
        blocks[i] *= 1+r
        y = str(dt.datetime.fromtimestamp(d, dt.timezone.utc).year)
        by_year[y] = by_year.get(y, 1.0)*(1+r)
    wins = sum(t["ret"] for t in trades if t["ret"] > 0)
    losses = -sum(t["ret"] for t in trades if t["ret"] <= 0)
    return {"return_pct": round((eq-1)*100, 2), "apr_pct": round((eq**(1/years)-1)*100, 2) if years > 0 else 0.0,
            "sharpe": round(statistics.mean(rets)/sd*math.sqrt(365), 3) if sd else 0.0,
            "max_drawdown_pct": round(dd*100, 2), "trades": len(trades),
            "profit_factor": round(wins/losses, 3) if losses > 0 else (99.0 if wins > 0 else 0.0),
            "blocks": [round((b-1)*100, 2) for b in blocks],
            "by_year": {y: round((g-1)*100, 2) for y, g in by_year.items()}}


def premium_report(views):
    """Mean funding per coin and year (annualized) and its t-stat over all days."""
    out, every = {}, []
    for sym, view in views.items():
        per_year = {}
        for d, v in view.items():
            if v["funding"] is None:
                continue
            every.append(v["funding"])
            y = str(dt.datetime.fromtimestamp(d, dt.timezone.utc).year)
            per_year.setdefault(y, []).append(v["funding"])
        out[sym] = {y: round(statistics.mean(x)*365*100, 2) for y, x in sorted(per_year.items())}
    t = (statistics.mean(every)/(statistics.pstdev(every)/math.sqrt(len(every)))) if len(every) > 2 and statistics.pstdev(every) else 0.0
    return {"apr_by_coin_and_year_pct": out, "mean_daily_funding_t_stat": round(t, 2), "funding_days": len(every)}


# ------------------------------------------------------------------ training
def train(data, shuffles=3, log=print):
    views = {sym: daily_view(m) for sym, m in data.items() if m["funding"]}
    missing_perp = [s for s, m in data.items() if m["funding"] and not m["perp"]]
    configs = grid()
    results = {}
    for cfg in configs:
        daily, trades, exp = run(cfg, views)
        results[cfg["id"]] = (cfg, stats(daily, trades), exp)
    premium = premium_report(views)
    always = {u: results[f"rule=always_on|universe={u}"][1] for u in ("all", "majors")}
    prem_ok = {}
    for u, s in always.items():
        share = sum(b > 0 for b in s["blocks"])/len(s["blocks"]) if s["blocks"] else 0
        prem_ok[u] = share >= PREMIUM_RULES["min_positive_block_share"] and premium["mean_daily_funding_t_stat"] >= PREMIUM_RULES["min_funding_t_stat"]
    log("measuring the timing luck bar on day-shuffled funding")
    bar = -9.0
    for k in range(shuffles):
        fake = {sym: shuffle_funding(v, 500+k*17+j) for j, (sym, v) in enumerate(sorted(views.items()))}
        for cfg in configs:
            if cfg["rule"] == "timed":
                daily, trades, _ = run(cfg, fake)
                bar = max(bar, stats(daily, trades)["sharpe"])
    bar = round(bar, 3)
    board = []
    for cid, (cfg, s, exp) in results.items():
        why = []
        if cfg["rule"] == "timed":
            share = sum(b > 0 for b in s["blocks"])/len(s["blocks"]) if s["blocks"] else 0
            if share < TIMING_RULES["min_positive_block_share"]:
                why.append(f"positive in {share*100:.0f}% of blocks")
            if s["blocks"] and min(s["blocks"]) < TIMING_RULES["worst_block_floor_pct"]:
                why.append(f"worst block {min(s['blocks'])}%")
            if s["trades"] < TIMING_RULES["min_trades"]:
                why.append(f"only {s['trades']} episodes")
            if s["sharpe"] <= bar:
                why.append(f"Sharpe {s['sharpe']} does not beat the luck bar {bar}")
            if s["sharpe"] <= always[cfg["universe"]]["sharpe"]:
                why.append(f"does not beat always-on ({always[cfg['universe']]['sharpe']})")
        board.append({"id": cid, "rule": cfg["rule"], "survived": cfg["rule"] == "timed" and not why, "why_not": why,
                      "time_in_market_pct": exp, **{k: s[k] for k in ("return_pct", "apr_pct", "sharpe", "max_drawdown_pct",
                                                                     "trades", "profit_factor", "blocks")}})
    board.sort(key=lambda b: -b["sharpe"])
    timed = [b for b in board if b["survived"]]
    chosen, basis = None, None
    if timed:
        chosen, basis = results[timed[0]["id"]][0], "best timing rule that beat the luck bar and always-on"
    else:
        ok = [u for u in ("majors", "all") if prem_ok[u]]
        if ok:
            u = max(ok, key=lambda x: always[x]["sharpe"])
            chosen, basis = results[f"rule=always_on|universe={u}"][0], "always-on carry: premium real, timing added nothing"
    alpaca = {}
    if chosen:
        d, tr, _ = run(chosen, views, ALPACA_SPOT_FEE_BPS)
        alpaca = {k: v for k, v in stats(d, tr).items() if k != "blocks"}
    all_days = sorted({d for v in views.values() for d in v})
    lessons = {"lab": "henry-carry-v1", "costs": COSTS, "premium_rules": PREMIUM_RULES, "timing_rules": TIMING_RULES,
               "trained_on": {"from": all_days[0] if all_days else None, "to": all_days[-1] if all_days else None,
                              "coins": sorted(views)},
               "coins_without_perp_prices": missing_perp, "premium": premium, "premium_real": prem_ok,
               "always_on": always, "luck_bar_sharpe": bar, "variants_tested": len(configs), "leaderboard": board,
               "chosen": chosen, "basis": basis, "chosen_with_alpaca_spot_fees": alpaca}
    lessons["fingerprint"] = fingerprint(lessons)
    return lessons


def fingerprint(lessons):
    core = {k: lessons.get(k) for k in ("lab", "costs", "chosen", "luck_bar_sharpe", "trained_on", "basis")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


def test(data, lessons, ledger_path=None):
    if fingerprint(lessons) != lessons.get("fingerprint"):
        raise ValueError("carry lessons were edited after training; refusing to test them")
    if not lessons["chosen"]:
        return {"verdict": "NOTHING TO TEST", "reason": "training found no real premium and no timing edge"}
    prior = 0
    if ledger_path and Path(ledger_path).exists():
        prior = sum(1 for e in json.loads(Path(ledger_path).read_text()) if e["fingerprint"] == lessons["fingerprint"])
    views = {sym: daily_view(m) for sym, m in data.items() if m["funding"]}
    daily, trades, exp = run(lessons["chosen"], views)
    s = stats(daily, trades)
    ad, at, _ = run(lessons["chosen"], views, ALPACA_SPOT_FEE_BPS)
    years = list(s["by_year"].values())
    checks = {
        "beats_cash": (s["return_pct"] > TEST_GATE["min_return_pct"], f"{s['return_pct']}% ({s['apr_pct']}%/yr)"),
        "sharpe": (s["sharpe"] >= TEST_GATE["min_sharpe"], f"{s['sharpe']} vs {TEST_GATE['min_sharpe']}"),
        "drawdown": (s["max_drawdown_pct"] <= TEST_GATE["max_drawdown_pct"], f"{s['max_drawdown_pct']}% vs {TEST_GATE['max_drawdown_pct']}%"),
        "most_years_profitable": (bool(years) and sum(y > 0 for y in years)/len(years) >= TEST_GATE["min_share_of_years_profitable"],
                                  f"{sum(y > 0 for y in years)} of {len(years)} years"),
        "no_bad_year": (bool(years) and min(years) >= TEST_GATE["worst_year_floor_pct"], f"worst year {min(years) if years else None}%"),
    }
    result = {"verdict": "PASS" if all(v[0] for v in checks.values()) else "FAIL",
              "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in checks.items()},
              "result": {**s, "time_in_market_pct": exp}, "with_alpaca_spot_fees": stats(ad, at),
              "premium": premium_report(views), "previous_tests_of_these_lessons": prior,
              "coins_without_perp_prices": [s_ for s_, m in data.items() if m["funding"] and not m["perp"]],
              "fingerprint": lessons["fingerprint"]}
    if ledger_path:
        entries = json.loads(Path(ledger_path).read_text()) if Path(ledger_path).exists() else []
        entries.append({"fingerprint": lessons["fingerprint"], "lab": "henry-carry-v1", "verdict": result["verdict"],
                        "return_pct": s["return_pct"], "at": dt.datetime.now(dt.timezone.utc).isoformat()})
        Path(ledger_path).write_text(json.dumps(entries, indent=1))
    return result


# ------------------------------------------------------------------ CLI
def show_lessons(L):
    fmt = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d") if t else "?"  # noqa: E731
    print(f"Henry carry lab: {', '.join(L['trained_on']['coins'])} ({fmt(L['trained_on']['from'])} to {fmt(L['trained_on']['to'])})")
    if L["coins_without_perp_prices"]:
        print(f"  !! no perp prices for {L['coins_without_perp_prices']}: basis assumed flat there (rebuild with --perp)")
    p = L["premium"]
    print(f"  funding premium: t-stat {p['mean_daily_funding_t_stat']} over {p['funding_days']} coin-days")
    for sym, years in p["apr_by_coin_and_year_pct"].items():
        print(f"     {sym:9} " + "  ".join(f"{y}: {v:>6}%" for y, v in years.items()))
    for u, s in L["always_on"].items():
        print(f"  ALWAYS-ON ({u}): {s['return_pct']}%  {s['apr_pct']}%/yr  Sharpe {s['sharpe']}  dd {s['max_drawdown_pct']}%  "
              f"blocks {s['blocks']}  -> premium {'REAL' if L['premium_real'][u] else 'not proven'}")
    print(f"  timing: {L['variants_tested']-2} variants; luck bar (best Sharpe on day-shuffled funding) {L['luck_bar_sharpe']}")
    for b in L["leaderboard"][:10]:
        print(f"     {'KEEP' if b['survived'] else 'drop'}  Sharpe {b['sharpe']:>6}  {b['apr_pct']:>6}%/yr  dd {b['max_drawdown_pct']:>5}%  "
              f"in market {b['time_in_market_pct']:>5}%  {b['id']}" + (f"\n           why not: {'; '.join(b['why_not'])}" if b["why_not"] else ""))
    if L["chosen"]:
        a = L["chosen_with_alpaca_spot_fees"]
        print(f"  LESSON: {L['chosen']['id']}  ({L['basis']})")
        print(f"     same lesson with Alpaca's 25 bps spot fee: {a.get('return_pct')}%  {a.get('apr_pct')}%/yr  Sharpe {a.get('sharpe')}")
    else:
        print("  LESSON: none. No proven premium and no timing edge. Do not test.")
    print(f"  fingerprint {L['fingerprint'][:16]}")


def show_test(r):
    print(f"Henry carry one-shot test -> {r['verdict']}")
    if "checks" not in r:
        print("  ", r["reason"])
        return
    if r["previous_tests_of_these_lessons"]:
        print(f"  !! already tested {r['previous_tests_of_these_lessons']} time(s): this holdout is spent for these lessons")
    if r["coins_without_perp_prices"]:
        print(f"  !! no perp prices for {r['coins_without_perp_prices']}: basis assumed flat there")
    for k, v in r["checks"].items():
        print(f"   {'ok ' if v['pass'] else 'XX '} {k}: {v['detail']}")
    s, a = r["result"], r["with_alpaca_spot_fees"]
    print(f"  carry: {s['return_pct']}%  {s['apr_pct']}%/yr  Sharpe {s['sharpe']}  dd {s['max_drawdown_pct']}%  "
          f"episodes {s['trades']}  in market {s['time_in_market_pct']}%")
    print(f"  by year: {s['by_year']}")
    print(f"  with Alpaca's 25 bps spot fee: {a['return_pct']}%  {a['apr_pct']}%/yr  Sharpe {a['sharpe']}")
    p = r["premium"]
    print(f"  funding premium in this era: t-stat {p['mean_daily_funding_t_stat']}")
    for sym, years in p["apr_by_coin_and_year_pct"].items():
        print(f"     {sym:9} " + "  ".join(f"{y}: {v:>6}%" for y, v in years.items()))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    tr = sub.add_parser("train")
    tr.add_argument("--data", action="append", required=True)
    tr.add_argument("--out", required=True)
    tr.add_argument("--shuffles", type=int, default=3)
    te = sub.add_parser("test")
    te.add_argument("--data", action="append", required=True)
    te.add_argument("--lessons", required=True)
    te.add_argument("--ledger")
    args = p.parse_args(argv)
    data = merge([load(x) for x in args.data])
    if args.cmd == "train":
        L = train(data, args.shuffles)
        Path(args.out).write_text(json.dumps(L, default=str))
        show_lessons(L)
    else:
        show_test(test(data, json.loads(Path(args.lessons).read_text()), args.ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
