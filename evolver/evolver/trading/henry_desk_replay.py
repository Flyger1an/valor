"""Replay the research desk hour by hour over a dataset built by henry_desk_data, then grade it.

  python -m evolver.trading.henry_desk_replay --data /data/henry_desk.json.gz [--sweep] [--theses 10]

Each hour: manage open positions on the bar that just closed, settle simulated-perp funding,
then the desk writes theses on that close and the best approved ones fill at the next bar's open.

The report answers:
  - did Henry beat cash, and how did he do vs buy-and-hold in each market regime (bull/range/bear,
    judged on BTC) over the same hours
  - which setups and which side (spot longs vs simulated shorts) made the money
  - which analysts were right: for each, the average R of trades it agreed with vs disagreed with
  - first half vs second half, to catch a result that only worked once
and ends with a PASS/FAIL gate. Nothing goes live on a FAIL.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import statistics

from . import henry_desk as hd
from .henry_desk_data import load

GATE = {"min_return_pct": 0.0, "max_drawdown_pct": 25.0, "min_profit_factor": 1.2, "min_trades": 30,
        "min_half_return_pct": -5.0}
SWEEP = {"conviction_min": [55.0, 60.0, 65.0], "rr_min": [1.5, 2.0, 2.5]}


def series_from(data):
    out = {}
    for sym, d in data["symbols"].items():
        bars = d["bars"]
        if len(bars) < 200:
            continue
        cols = list(zip(*bars))
        fund = d.get("funding", [])
        out[sym] = hd.Series(sym, list(cols[0]), list(cols[1]), list(cols[2]), list(cols[3]), list(cols[4]), list(cols[5]),
                             [f[0] for f in fund], [f[1] for f in fund])
    return out


def replay(series, keep_theses=0):
    desk, book = hd.Desk(series), hd.Book()
    times = sorted(set().union(*(s.t for s in series.values())))
    leader = series.get(hd.DESK_RULES["market_leader"])
    marks, curve, regime_hours, sample_theses = {}, [], [], []
    approved = proposed = 0
    for k, t in enumerate(times):
        # 1. fills for orders decided at the previous close, at this bar's open
        for th in book.pending:
            s = series[th["symbol"]]
            i = s.index.get(t)
            if i is not None:
                book.open(th, s.o[i], t, marks)
        book.pending = []
        # 2. manage open positions on this bar, settle funding
        for sym, s in series.items():
            i = s.index.get(t)
            if i is None:
                continue
            marks[sym] = s.c[i]
            ft = bisect_funding(s, t)
            for rate in ft:
                book.apply_funding(sym, rate, s.c[i])
        for pos in list(book.positions):
            s = series[pos["symbol"]]
            i = s.index.get(t)
            if i is None or pos["opened"] >= t:
                continue
            label = hd.analyst_regime(s, i)["label"]
            book.manage(pos, (s.o[i], s.h[i], s.l[i], s.c[i]), t, label, s.atr[i])
        eq = book.mark(marks)
        lr = hd.analyst_regime(leader, leader.index[t])["label"] if leader and t in leader.index else None
        curve.append((t, eq))
        regime_hours.append(lr)
        if book.halted or book.standing_down(t):
            continue
        # 3. the desk writes theses on this close; the best approved ones queue for the next open
        theses = desk.theses(t)
        proposed += len(theses)
        good = [th for th in theses if th["approved_by_desk"] and th.get("reviewer", {}).get("approve")]
        approved += len(good)
        if keep_theses and good and len(sample_theses) < keep_theses:
            sample_theses.extend(good[:keep_theses-len(sample_theses)])
        good.sort(key=lambda th: (-th["conviction"], -th["reward_to_risk"]))
        open_syms = {p["symbol"] for p in book.positions}
        slots = hd.DESK_RULES["max_positions"]-len(book.positions)
        for th in good:
            if slots <= 0:
                break
            if th["symbol"] in open_syms:
                continue
            book.pending.append(th)
            open_syms.add(th["symbol"])
            slots -= 1
    # close anything left at the last close for a clean tally
    for pos in list(book.positions):
        s = series[pos["symbol"]]
        book.close(pos, s.c[-1], times[-1], "end_of_data")
    return grade(series, book, curve, regime_hours, proposed, approved, sample_theses)


def bisect_funding(s, t):
    """Funding prints that settle inside the hour ending at t+1h."""
    import bisect
    lo, hi = bisect.bisect_right(s.funding_t, t), bisect.bisect_right(s.funding_t, t+hd.HOUR)
    return s.funding_r[lo:hi]


def grade(series, book, curve, regime_hours, proposed, approved, sample_theses):
    start = hd.DESK_RULES["starting_cash"]
    end = curve[-1][1] if curve else start
    trades = book.trades
    wins = [t["pnl_usd"] for t in trades if t["pnl_usd"] > 0]
    losses = [t["pnl_usd"] for t in trades if t["pnl_usd"] <= 0]
    pf = sum(wins)/-sum(losses) if losses and sum(losses) < 0 else (float("inf") if wins else 0.0)
    leader = series.get(hd.DESK_RULES["market_leader"])
    # by market regime: Henry's equity change vs BTC buy-and-hold over the same hours
    by_regime = {}
    for k in range(1, len(curve)):
        lab = regime_hours[k] or "unknown"
        g = by_regime.setdefault(lab, {"hours": 0, "henry_pct": 0.0, "btc_hold_pct": 0.0})
        g["hours"] += 1
        g["henry_pct"] += (curve[k][1]/curve[k-1][1]-1)*100 if curve[k-1][1] else 0
        if leader is not None:
            a, b = leader.index.get(curve[k-1][0]), leader.index.get(curve[k][0])
            if a is not None and b is not None:
                g["btc_hold_pct"] += (leader.c[b]/leader.c[a]-1)*100
    by_regime = {k: {**v, "henry_pct": round(v["henry_pct"], 2), "btc_hold_pct": round(v["btc_hold_pct"], 2)}
                 for k, v in by_regime.items()}

    def group(key):
        out = {}
        for t in trades:
            g = out.setdefault(str(t[key]), {"trades": 0, "wins": 0, "pnl_usd": 0.0, "avg_r": 0.0})
            g["trades"] += 1
            g["wins"] += t["pnl_usd"] > 0
            g["pnl_usd"] += t["pnl_usd"]
            g["avg_r"] += t["r_multiple"]
        return {k: {**v, "pnl_usd": round(v["pnl_usd"], 2), "avg_r": round(v["avg_r"]/v["trades"], 3)} for k, v in out.items()}
    attribution = {}
    for name in hd.DESK_RULES["weights"]:
        agree = [t["r_multiple"] for t in trades if t["conviction_parts"].get(name, 0) > 0.05]
        disagree = [t["r_multiple"] for t in trades if t["conviction_parts"].get(name, 0) < -0.05]
        attribution[name] = {"agreed_trades": len(agree), "avg_r_when_agreed": round(statistics.mean(agree), 3) if agree else None,
                             "disagreed_trades": len(disagree),
                             "avg_r_when_disagreed": round(statistics.mean(disagree), 3) if disagree else None}
    half = len(curve)//2
    halves = [round((curve[half][1]/curve[0][1]-1)*100, 2) if curve else 0.0,
              round((curve[-1][1]/curve[half][1]-1)*100, 2) if curve else 0.0]
    hold = {}
    for sym, s in series.items():
        hold[sym] = round((s.c[-1]/s.o[0]-1)*100, 2)
    result = {
        "rules_version": hd.DESK_RULES["version"], "symbols": sorted(series),
        "days": round((curve[-1][0]-curve[0][0])/86400, 1) if curve else 0,
        "equity_usd": round(end, 2), "return_pct": round((end/start-1)*100, 2),
        "max_drawdown_pct": round(book.max_dd*100, 2), "halted": book.halted,
        "trades": len(trades), "win_rate_pct": round(len(wins)/len(trades)*100, 1) if trades else 0.0,
        "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        "avg_r": round(statistics.mean(t["r_multiple"] for t in trades), 3) if trades else 0.0,
        "fees_usd": round(book.fees, 2), "friction_usd": round(book.friction, 2),
        "net_funding_paid_usd": round(book.funding_paid, 2),
        "theses_written": proposed, "theses_approved": approved, "rejected_at_fill": book.rejected,
        "stand_downs": book.pauses,
        "by_market_regime": by_regime, "by_setup": group("setup"), "by_direction": group("direction"),
        "by_exit": group("reason"), "by_symbol": group("symbol"), "analyst_attribution": attribution,
        "halves_return_pct": halves, "buy_and_hold_pct": hold,
        "recent_trades": trades[-12:], "sample_theses": sample_theses,
        "caveats": ["Binance archive prices (not Alpaca); spreads modeled; fills at next hour's open",
                    "shorts are simulated perpetuals: executing them needs a perp venue",
                    "news/macro analyst and LLM reviewer are live-only and not in this replay"]}
    result["gate"] = gate(result)
    return result


def gate(r):
    checks = {
        "beats_cash": (r["return_pct"] > GATE["min_return_pct"], f"{r['return_pct']}%"),
        "drawdown": (r["max_drawdown_pct"] <= GATE["max_drawdown_pct"], f"{r['max_drawdown_pct']}% vs {GATE['max_drawdown_pct']}% max"),
        "profit_factor": (r["profit_factor"] == "inf" or r["profit_factor"] >= GATE["min_profit_factor"],
                          f"{r['profit_factor']} vs {GATE['min_profit_factor']} min"),
        "enough_trades": (r["trades"] >= GATE["min_trades"], f"{r['trades']} vs {GATE['min_trades']} min"),
        "both_halves": (min(r["halves_return_pct"]) >= GATE["min_half_return_pct"],
                        f"halves {r['halves_return_pct']} vs {GATE['min_half_return_pct']}% floor"),
    }
    return {"verdict": "PASS" if all(v[0] for v in checks.values()) else "FAIL",
            "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in checks.items()}}


def sweep(series):
    original = copy.deepcopy(hd.DESK_RULES)
    rows = []
    try:
        for cm, rr in itertools.product(SWEEP["conviction_min"], SWEEP["rr_min"]):
            hd.DESK_RULES.update(conviction_min=cm, rr_min=rr)
            r = replay(series)
            rows.append({"conviction_min": cm, "rr_min": rr, **{k: r[k] for k in (
                "return_pct", "max_drawdown_pct", "trades", "profit_factor", "halves_return_pct")}, "gate": r["gate"]["verdict"]})
    finally:
        hd.DESK_RULES.clear()
        hd.DESK_RULES.update(original)
    return sorted(rows, key=lambda x: -x["return_pct"])


def show(r):
    g = r["gate"]
    print(f"Henry desk {r['rules_version']}: {r['days']} days, {len(r['symbols'])} coins  ->  GATE {g['verdict']}")
    for k, v in g["checks"].items():
        print(f"   {'ok ' if v['pass'] else 'XX '} {k}: {v['detail']}")
    print(f"  equity ${r['equity_usd']}  return {r['return_pct']}%  max drawdown {r['max_drawdown_pct']}%"
          + (f"  HALTED: {r['halted']}" if r["halted"] else ""))
    print(f"  trades {r['trades']}  win {r['win_rate_pct']}%  profit factor {r['profit_factor']}  avg R {r['avg_r']}")
    print(f"  friction ${r['friction_usd']} (fees ${r['fees_usd']})  net funding paid ${r['net_funding_paid_usd']}")
    print(f"  theses written {r['theses_written']}, approved {r['theses_approved']}, gapped out at fill {r['rejected_at_fill']}, "
          f"stand-downs after bad streaks {r['stand_downs']}")
    print("  buy and hold:", ", ".join(f"{k} {v}%" for k, v in r["buy_and_hold_pct"].items()))
    print("  by market regime (Henry vs BTC hold over the same hours):")
    for k, v in r["by_market_regime"].items():
        print(f"     {k:10} {v['hours']:>5}h   Henry {v['henry_pct']:>7}%   BTC {v['btc_hold_pct']:>7}%")
    for title, key in (("setup", "by_setup"), ("side", "by_direction"), ("exit", "by_exit"), ("coin", "by_symbol")):
        print(f"  by {title}:")
        for k, v in sorted(r[key].items(), key=lambda kv: -kv[1]["pnl_usd"]):
            print(f"     {k:24} {v['trades']:>4} trades  {v['wins']:>3} wins  ${v['pnl_usd']:>8}  avg R {v['avg_r']}")
    print("  analyst attribution (avg R when the analyst agreed vs disagreed with the trade):")
    for k, v in r["analyst_attribution"].items():
        print(f"     {k:12} agreed {v['agreed_trades']:>3} -> {v['avg_r_when_agreed']}   "
              f"disagreed {v['disagreed_trades']:>3} -> {v['avg_r_when_disagreed']}")
    print(f"  halves: {r['halves_return_pct']}")
    for t in r["recent_trades"]:
        print(f"     {t['symbol']:9} {t['setup']:22} {t['direction']:5} {t['pnl_usd']:>8.2f} R {t['r_multiple']:>6} "
              f"{t['reason']:13} {t['hours']}h conv {t['conviction']}")
    for c in r["caveats"]:
        print("  note:", c)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True)
    p.add_argument("--sweep", action="store_true")
    p.add_argument("--theses", type=int, default=0, help="print this many full approved theses")
    p.add_argument("--json")
    args = p.parse_args(argv)
    series = series_from(load(args.data))
    r = replay(series, keep_theses=args.theses)
    show(r)
    for th in r["sample_theses"]:
        print(json.dumps(th, indent=1, default=str))
    if args.sweep:
        r["sweep"] = sweep(series)
        print("\n  sweep (one dataset: look for stable neighborhoods, not the top row):")
        for row in r["sweep"]:
            print(f"    conv>={row['conviction_min']} rr>={row['rr_min']}: {row['return_pct']:>7}%  dd {row['max_drawdown_pct']:>5}%  "
                  f"trades {row['trades']:>4}  pf {row['profit_factor']}  halves {row['halves_return_pct']}  {row['gate']}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(r, f, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
