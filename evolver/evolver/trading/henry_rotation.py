"""ETF rotation: one declared test, on the US ETFs we would actually trade.

  run   python -m evolver.trading.henry_rotation run --data /data/tradfi.json.gz      (offline)

The data file is the one henry_tradfi already fetched (Yahoo adjusted closes, so dividends count).

THE RULE (taken from the published research on relative and absolute momentum, not fitted here):
  - Universe: the 16 ETFs below (US stocks, sectors, international, real estate, bonds, gold,
    silver, commodities). An ETF is eligible once it has 12 months of history.
  - At each month's last trading close, rank eligible ETFs by 12-month total return.
  - Hold the top 3, a third of the book each. A pick whose 12-month return is not above zero
    leaves its third in cash (earning 0%, a conservative stand-in for T-bills).
  - Trades fill at the NEXT day's close (a full day of lag built in). Costs per side: 1 bp
    slippage plus half a 2 bp spread, no commission. Holdings drift between rebalances.
  - The test starts at the first month-end with at least 10 eligible ETFs.

THE SCORECARD (declared before this rule was ever run on this data; SCORECARD below):
  vs holding SPY over the same days:
    - Sharpe at least SPY's
    - max drawdown at most 75% of SPY's
    - in each half of the period: drawdown below SPY's and Sharpe at least 80% of SPY's
  vs the equal-weight basket of the same ETFs: higher Sharpe
  stress:
    - one more day of lag keeps at least 70% of the Sharpe
    - double costs keep at least 90% of the Sharpe
    - neighbors: lookbacks 6/9/12 months x top 2/3/4: at least 6 of 9 have Sharpe >= SPY's
    - paired block bootstrap: rotation's Sharpe beats SPY's in at least 75% of resamples
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import gzip
import json
import math
import random
import statistics

UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "VNQ", "XLE", "XLF", "XLK", "XLU", "XLV",
            "TLT", "IEF", "GLD", "SLV", "DBC"]
RULE = {"lookback_months": 12, "top": 3, "absolute_filter": True, "lag_days": 1, "min_eligible": 10}
COSTS_BPS = 1.0+2.0/2   # one side: slippage + half spread
SCORECARD = {"sharpe_vs_spy_min": 1.0, "drawdown_vs_spy_max": 0.75,
             "halves_sharpe_vs_spy_min": 0.8, "beats_equal_weight_sharpe": True,
             "extra_lag_sharpe_kept_min": 0.7, "double_costs_sharpe_kept_min": 0.9,
             "neighbors_ok_min": 6, "bootstrap_p_beats_spy_min": 0.75}
PER_YEAR = 252


def metrics(rets):
    if not rets:
        return {"return_pct": 0.0, "cagr_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0}
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1+r
        peak = max(peak, eq)
        dd = max(dd, 1-eq/peak)
    sd = statistics.pstdev(rets)
    years = len(rets)/PER_YEAR
    return {"return_pct": round((eq-1)*100, 1),
            "cagr_pct": round((eq**(1/years)-1)*100, 1) if eq > 0 and years > 0 else -100.0,
            "sharpe": round(statistics.mean(rets)/sd*math.sqrt(PER_YEAR), 2) if sd else 0.0,
            "max_drawdown_pct": round(dd*100, 1)}


def sharpe(rets):
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0
    return statistics.mean(rets)/sd*math.sqrt(PER_YEAR) if sd else 0.0


def align(etfs):
    """Calendar = SPY's trading days. Closes forward-filled; None before an ETF's first day."""
    days = [b[0] for b in etfs["SPY"]]
    closes = {}
    for sym in UNIVERSE:
        bars = etfs.get(sym) or []
        bt, bc = [b[0] for b in bars], [b[4] for b in bars]
        col = []
        for d in days:
            k = bisect.bisect_right(bt, d)-1
            col.append(bc[k] if k >= 0 else None)
        closes[sym] = col
    return days, closes


def month_ends(days):
    out = []
    for i, d in enumerate(days):
        if i+1 == len(days) or dt.datetime.fromtimestamp(days[i+1], dt.timezone.utc).month != \
                dt.datetime.fromtimestamp(d, dt.timezone.utc).month:
            out.append(i)
    return out


def targets(closes, ends, m, rule):
    """Target weights decided at month-end index ends[m], or None while too few ETFs are eligible."""
    lb = rule["lookback_months"]
    if m < lb:
        return None
    i, j = ends[m], ends[m-lb]
    mom = {s: closes[s][i]/closes[s][j]-1 for s in UNIVERSE if closes[s][j] and closes[s][i]}
    if len(mom) < rule["min_eligible"]:
        return None
    picks = sorted(mom, key=lambda s: -mom[s])[:rule["top"]]
    w = {}
    for s in picks:
        if not rule["absolute_filter"] or mom[s] > 0:
            w[s] = 1.0/rule["top"]
    return w   # the remainder is cash


def simulate(days, closes, rule=RULE, cost_bps=COSTS_BPS, equal_weight=False):
    """Daily returns from the first tradable rebalance. Returns (start index, returns, rebalances)."""
    ends = month_ends(days)
    sched = {}   # day index at which new weights take effect at the close -> weights
    start = None
    for m in range(len(ends)):
        if equal_weight:
            elig = [s for s in UNIVERSE if closes[s][ends[m]] and m >= rule["lookback_months"]
                    and closes[s][ends[m-rule["lookback_months"]]]]
            w = {s: 1.0/len(elig) for s in elig} if len(elig) >= rule["min_eligible"] else None
        else:
            w = targets(closes, ends, m, rule)
        if w is None:
            continue
        k = ends[m]+rule["lag_days"]
        if k >= len(days):
            break
        sched[k] = w
        if start is None:
            start = k
    if start is None:
        return None, [], 0
    hold, rets = {}, []
    c = cost_bps/10000
    for i in range(start, len(days)):
        r = 0.0
        if hold and i > start:
            new = {}
            for s, v in hold.items():
                g = closes[s][i]/closes[s][i-1]
                new[s] = v*g
                r += v*(g-1)
            hold = {s: v/(1+r) for s, v in new.items()}   # drifted weights; the cash leg earns 0
        if i in sched:
            w = sched[i]
            turnover = sum(abs(w.get(s, 0)-hold.get(s, 0)) for s in set(w) | set(hold))
            r -= turnover*c
            hold = dict(w)
        rets.append(r)
    return start, rets[1:], len(sched)


def spy_returns(days, closes, start):
    c = closes["SPY"]
    return [c[i]/c[i-1]-1 for i in range(start+1, len(days))]


def paired_bootstrap(a, b, n=2000, block=20, seed=11):
    rng = random.Random(seed)
    wins = 0
    for _ in range(n):
        ia = []
        while len(ia) < len(a):
            k = rng.randrange(0, max(1, len(a)-block))
            ia += range(k, min(k+block, len(a)))
        ia = ia[:len(a)]
        wins += sharpe([a[i] for i in ia]) > sharpe([b[i] for i in ia])
    return round(wins/n, 3)


def evaluate(data):
    days, closes = align(data["etfs"])
    start, rot, n_reb = simulate(days, closes)
    if start is None:
        return {"verdict": {"verdict": "NO DATA", "checks": {}}}
    spy = spy_returns(days, closes, start)
    s_eq, eq, _ = simulate(days, closes, equal_weight=True)
    eq = eq[len(eq)-len(rot):] if len(eq) >= len(rot) else eq
    half = len(rot)//2
    out = {"from": dt.datetime.fromtimestamp(days[start], dt.timezone.utc).date().isoformat(),
           "to": dt.datetime.fromtimestamp(days[-1], dt.timezone.utc).date().isoformat(),
           "rebalances": n_reb,
           "rotation": metrics(rot), "spy": metrics(spy), "equal_weight": metrics(eq),
           "halves": [{"rotation": metrics(rot[:half]), "spy": metrics(spy[:half])},
                      {"rotation": metrics(rot[half:]), "spy": metrics(spy[half:])}],
           "years": yearly(days, start, rot, spy)}
    base = sharpe(rot)

    def tail(rule=RULE, cost=COSTS_BPS):
        _, r, _ = simulate(days, closes, rule, cost)
        return r[len(r)-len(rot):] if len(r) >= len(rot) else r
    out["stress"] = {"extra_lag": metrics(tail(dict(RULE, lag_days=2))),
                     "double_costs": metrics(tail(cost=2*COSTS_BPS)),
                     "neighbors": {f"{lb}m top{n}": metrics(tail(dict(RULE, lookback_months=lb, top=n)))
                                   for lb in (6, 9, 12) for n in (2, 3, 4)},
                     "bootstrap_p_beats_spy": paired_bootstrap(rot, spy)}
    out["verdict"] = score(out, base)
    return out


def yearly(days, start, rot, spy):
    out = {}
    for k, (a, b) in enumerate(zip(rot, spy)):
        y = dt.datetime.fromtimestamp(days[start+1+k], dt.timezone.utc).year
        ra, rb = out.get(y, (1.0, 1.0))
        out[y] = (ra*(1+a), rb*(1+b))
    return {y: (round((a-1)*100, 1), round((b-1)*100, 1)) for y, (a, b) in out.items()}


def score(out, base_sharpe):
    card, ch = SCORECARD, {}
    R, S, E = out["rotation"], out["spy"], out["equal_weight"]
    ch["sharpe_vs_spy"] = (R["sharpe"] >= S["sharpe"]*card["sharpe_vs_spy_min"], f"{R['sharpe']} vs SPY {S['sharpe']}")
    ch["drawdown_vs_spy"] = (R["max_drawdown_pct"] <= S["max_drawdown_pct"]*card["drawdown_vs_spy_max"],
                             f"{R['max_drawdown_pct']}% vs SPY {S['max_drawdown_pct']}% (limit {S['max_drawdown_pct']*card['drawdown_vs_spy_max']:.1f}%)")
    for k, h in enumerate(out["halves"]):
        ok = (h["rotation"]["max_drawdown_pct"] < h["spy"]["max_drawdown_pct"]
              and h["rotation"]["sharpe"] >= card["halves_sharpe_vs_spy_min"]*h["spy"]["sharpe"])
        ch[f"half_{k+1}"] = (ok, f"Sharpe {h['rotation']['sharpe']} vs {h['spy']['sharpe']}, "
                                 f"dd {h['rotation']['max_drawdown_pct']}% vs {h['spy']['max_drawdown_pct']}%")
    ch["beats_equal_weight"] = (R["sharpe"] > E["sharpe"], f"{R['sharpe']} vs {E['sharpe']}")
    st = out["stress"]
    kept = st["extra_lag"]["sharpe"]/base_sharpe if base_sharpe > 0 else 0
    ch["extra_day_of_lag"] = (kept >= card["extra_lag_sharpe_kept_min"], f"keeps {kept*100:.0f}% of its Sharpe")
    kept = st["double_costs"]["sharpe"]/base_sharpe if base_sharpe > 0 else 0
    ch["double_costs"] = (kept >= card["double_costs_sharpe_kept_min"], f"keeps {kept*100:.0f}% of its Sharpe")
    ok = sum(m["sharpe"] >= S["sharpe"] for m in st["neighbors"].values())
    ch["neighbors"] = (ok >= card["neighbors_ok_min"], f"{ok} of 9 variants match SPY's Sharpe")
    p = st["bootstrap_p_beats_spy"]
    ch["bootstrap"] = (p >= card["bootstrap_p_beats_spy_min"], f"beats SPY's Sharpe in {p*100:.1f}% of resamples")
    return {"verdict": "PASS" if all(v[0] for v in ch.values()) else "FAIL",
            "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in ch.items()}}


def show(out):
    v = out["verdict"]
    print(f"\n===== ETF ROTATION: {v['verdict']}")
    for k, c in v["checks"].items():
        print(f"   {'ok ' if c['pass'] else 'XX '} {k}: {c['detail']}")
    if "rotation" not in out:
        return
    print(f"\n  {out['from']} to {out['to']}, {out['rebalances']} monthly rebalances")
    for name in ("rotation", "spy", "equal_weight"):
        m = out[name]
        print(f"     {name:13} {m['cagr_pct']:>6}%/yr  Sharpe {m['sharpe']:>5}  dd {m['max_drawdown_pct']:>5}%  total {m['return_pct']}%")
    st = out["stress"]
    print(f"  stress: extra lag Sharpe {st['extra_lag']['sharpe']}, double costs {st['double_costs']['sharpe']}")
    print("  neighbors: " + "  ".join(f"{k}:{m['sharpe']}" for k, m in st["neighbors"].items()))
    print("  by year (rotation vs SPY): " + "  ".join(f"{y}:{a}/{b}" for y, (a, b) in out["years"].items()))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", required=True)
    args = p.parse_args(argv)
    with gzip.open(args.data, "rt") as fh:
        data = json.load(fh)
    out = evaluate(data)
    show(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
