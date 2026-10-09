"""Does the frozen 50-day trend rule work outside crypto? Stocks and FX, judged before building anything.

  fetch  python -m evolver.trading.henry_tradfi fetch --out /data/tradfi.json.gz        (network)
  run    python -m evolver.trading.henry_tradfi run --data /data/tradfi.json.gz         (offline)

The rule is the one Henry trades in crypto, unchanged: hold while the close is above a rising
50-day average, sized to a 2.5% daily-volatility target, never levered, 3 ATR protective stop.
Choices made before any data was fetched:
  STOCKS  long only. Equity ETFs only enter while SPY is above its 100-day average (the BTC filter's
          twin). Bonds, gold and commodities get no filter: they often rise when stocks fall.
          Indexes and ETFs only: free single-stock history covers survivors, which flatters any rule.
  FX      long AND short (a pair has no natural long side), no filter. Interest carry is not
          modeled (no free rate history); results are reported net of spread only, a known gap.

Data: Yahoo's public chart API (adjusted closes for ETFs, so dividends count for both rule and hold);
ECB reference rates as an FX fallback (closes only, from 1999).

Scorecards (SCORECARDS below) were written before the data existed in this lab.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import random
import time
import urllib.request
import zipfile
from pathlib import Path

from . import henry_lab as L
from .henry_battery import metrics

RULE_STOCKS = {"family": "ma_trend", "tf": "1d", "ma": 50, "side": "long", "btc_filter": True}
RULE_FX = {"family": "ma_trend", "tf": "1d", "ma": 50, "side": "both", "btc_filter": False}
STOCK_COSTS = {"fee_bps": 0.0, "slip_bps": 1.0, "spread_bps": 2.0}   # commission-free broker, liquid ETFs
FX_COSTS = {"fee_bps": 0.0, "slip_bps": 0.5, "spread_bps": 1.5}      # majors at a retail FX broker
INDEXES = {"S&P 500": "^GSPC", "Nasdaq Composite": "^IXIC", "Russell 2000": "^RUT", "Nikkei 225": "^N225",
           "FTSE 100": "^FTSE", "DAX": "^GDAXI"}
EQUITY_ETFS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "VNQ", "XLE", "XLF", "XLK", "XLU", "XLV"]
OTHER_ETFS = ["TLT", "IEF", "GLD", "SLV", "DBC"]
FX_PAIRS = {"EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X", "AUDUSD": "AUDUSD=X",
            "USDCAD": "CAD=X", "USDCHF": "CHF=X", "NZDUSD": "NZDUSD=X", "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X"}
CRASHES = {"1973-74 bear": ("1973-01-11", "1974-10-03"), "1987 crash": ("1987-08-25", "1987-12-04"),
           "Dot-com bust": ("2000-03-24", "2002-10-09"), "2008 crisis": ("2007-10-09", "2009-03-09"),
           "COVID crash": ("2020-02-19", "2020-03-23"), "2022 bear": ("2022-01-03", "2022-10-12")}
NIKKEI_BUST = ("1989-12-29", "1992-08-18")

# Declared before any of this data was fetched.
SCORECARDS = {
    "stocks": {"drawdown_below_hold_every_dataset": True, "profitable_share_of_datasets": 0.75,
               "crashes_contained_min": 4,          # of 6 named S&P crashes: rule loses less than holding
               "lag_1d_sharpe_kept_min": 0.7, "double_costs_still_profitable": True, "neighbors_ok_min": 4,
               "bootstrap_p_sharpe_positive_min": 0.9},
    "fx": {"portfolio_sharpe_min": 0.3, "profitable_pairs_share_min": 0.5, "max_drawdown_pct": 25.0,
           "lag_1d_sharpe_kept_min": 0.7, "double_costs_still_profitable": True, "neighbors_ok_min": 4,
           "bootstrap_p_sharpe_positive_min": 0.9},
}


# ------------------------------------------------------------------ fetch
def _get(url, timeout=30, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0 (valor-research)"})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception:  # network or HTTP error: retry, then report the source missing
            time.sleep(2**k)
    return None


def parse_yahoo(raw, adjust=True):
    """[[t, o, h, l, c, v]] from Yahoo's chart JSON; with `adjust`, scaled to adjusted closes."""
    res = json.loads(raw)["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") if adjust else None
    out = []
    for k, t in enumerate(ts):
        o, h, lo, c = (q[x][k] for x in ("open", "high", "low", "close"))
        if None in (o, h, lo, c) or c <= 0:
            continue
        f = (adj[k]/c) if adj and adj[k] else 1.0
        day = int(t-t % 86400)
        out.append([day, o*f, max(h, o, c)*f, min(lo, o, c)*f, c*f, float(q["volume"][k] or 0)])
    dedup = {b[0]: b for b in out}
    return [dedup[t] for t in sorted(dedup)]


def fetch_yahoo(symbol, adjust=True):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(symbol)}"
           f"?period1=0&period2={int(time.time())}&interval=1d&events=div%2Csplit&includeAdjustedClose=true")
    raw = _get(url)
    try:
        return parse_yahoo(raw, adjust) if raw else []
    except (KeyError, IndexError, TypeError, ValueError):
        return []


def parse_ecb(csv_text):
    """ECB reference rates (EUR base) -> close-only bars for USD-quoted majors and crosses."""
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    out = {k: [] for k in FX_PAIRS}
    for r in rows:
        try:
            t = int(dt.datetime.strptime(r["Date"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
            usd = float(r["USD"])
            v = {c: float(r[c]) for c in ("JPY", "GBP", "AUD", "CAD", "CHF", "NZD") if r.get(c) not in (None, "", "N/A")}
        except (KeyError, ValueError):
            continue
        rates = {"EURUSD": usd, "EURJPY": v.get("JPY")}
        if "GBP" in v:
            rates["GBPUSD"] = usd/v["GBP"]
            rates["GBPJPY"] = v["JPY"]/v["GBP"] if "JPY" in v else None
        if "JPY" in v:
            rates["USDJPY"] = v["JPY"]/usd
        if "AUD" in v:
            rates["AUDUSD"] = usd/v["AUD"]
        if "CAD" in v:
            rates["USDCAD"] = v["CAD"]/usd
        if "CHF" in v:
            rates["USDCHF"] = v["CHF"]/usd
        if "NZD" in v:
            rates["NZDUSD"] = usd/v["NZD"]
        for k, c in rates.items():
            if c:
                out[k].append([t, c, c, c, c, 0.0])
    return {k: sorted(v) for k, v in out.items() if v}


def fetch(log=print):
    data = {"built_at": time.time(), "indexes": {}, "etfs": {}, "fx": {}, "fx_source": None, "missing": {}}
    for name, sym in INDEXES.items():
        bars = fetch_yahoo(sym, adjust=False)
        log(f"  index {name}: {len(bars)} days")
        (data["indexes"].__setitem__(name, bars) if len(bars) > 500 else data["missing"].__setitem__(name, "unavailable"))
    for sym in EQUITY_ETFS+OTHER_ETFS:
        bars = fetch_yahoo(sym)
        log(f"  etf {sym}: {len(bars)} days")
        (data["etfs"].__setitem__(sym, bars) if len(bars) > 500 else data["missing"].__setitem__(sym, "unavailable"))
    for name, sym in FX_PAIRS.items():
        bars = fetch_yahoo(sym, adjust=False)
        if len(bars) > 500:
            data["fx"][name] = bars
    if len(data["fx"]) >= 5:
        data["fx_source"] = "yahoo"
    else:
        raw = _get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip")
        if raw:
            z = zipfile.ZipFile(io.BytesIO(raw))
            data["fx"] = parse_ecb(z.read(z.namelist()[0]).decode())
            data["fx_source"] = "ecb_reference_rates (closes only)"
    log(f"  fx: {len(data['fx'])} pairs from {data['fx_source']}")
    if not data["fx"]:
        data["missing"]["fx"] = "yahoo and ecb unavailable"
    return data


# ------------------------------------------------------------------ evaluation
def with_costs(costs, fn):
    """Run fn under TradFi costs. Funding is zeroed: the lab's default credits crypto perp funding to
    shorts, which would hand FX shorts ~11%/yr of carry that does not exist."""
    saved = (L.FEE_BPS, L.SLIP_BPS, dict(L.SPREAD_BPS), L.DEFAULT_SPREAD_BPS, L.DEFAULT_FUNDING_8H)
    try:
        L.FEE_BPS, L.SLIP_BPS, L.SPREAD_BPS, L.DEFAULT_SPREAD_BPS = costs["fee_bps"], costs["slip_bps"], {}, costs["spread_bps"]
        L.DEFAULT_FUNDING_8H = 0.0
        return fn()
    finally:
        L.FEE_BPS, L.SLIP_BPS, L.SPREAD_BPS, L.DEFAULT_SPREAD_BPS, L.DEFAULT_FUNDING_8H = saved


def run_symbol(rule, sym, bars, costs, leader=None, ma=None):
    rule = dict(rule, ma=ma or rule["ma"])
    use_leader = leader if rule["btc_filter"] else None

    def go():
        return L.simulate(rule, sym, bars, [], use_leader)
    rows, trades = with_costs(costs, go)
    warm = rule["ma"]+6
    rets = [r for _, r, _ in rows[warm+1:]]
    pos = [p for _, _, p in rows]
    hold = [bars[i][4]/bars[i-1][4]-1 for i in range(warm+1, len(bars))]
    return {"t": [b[0] for b in bars[warm+1:]], "rule": rets, "hold": hold, "pos": pos, "trades": trades, "warm": warm}


def portfolio(per_symbol, key):
    days = {}
    for r in per_symbol.values():
        for t, x in zip(r["t"], r[key]):
            days.setdefault(t, []).append(x)
    return [(t, sum(v)/len(v)) for t, v in sorted(days.items())]


def window(series, start, end):
    lo = dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp()
    hi = dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc).timestamp()
    inside = [r for t, r in series if lo < t <= hi]
    if not inside or min(t for t, _ in series) > lo+7*86400:
        return None  # window not covered by the data: reported missing, never scored as a loss
    g = 1.0
    for r in inside:
        g *= 1+r
    return round((g-1)*100, 1)


def stress(series_by_sym, rule, costs, per_year, leader_for=None):
    def port(ma=None, lag=0, mult=1.0):
        out = {}
        for sym, bars in series_by_sym.items():
            lead = leader_for(sym) if leader_for else None
            r = run_symbol(rule if not leader_for or lead else dict(rule, btc_filter=False), sym, bars, costs, lead, ma)
            pos = r["pos"]
            if lag or mult != 1.0:
                cost = (costs["fee_bps"]+costs["slip_bps"]+costs["spread_bps"]/2)/10000*mult
                p = [0.0]*lag+pos[:len(pos)-lag] if lag else pos
                rets = [p[i-1]*(bars[i][4]/bars[i-1][4]-1)-abs(p[i]-p[i-1])*cost for i in range(1, len(bars))]
                r = dict(r, rule=rets[r["warm"]:])
            out[sym] = r
        return [x for _, x in portfolio(out, "rule")]
    base = metrics(port(), per_year)
    res = {"base": base, "lag_1d": metrics(port(lag=1), per_year), "lag_2d": metrics(port(lag=2), per_year),
           "double_costs": metrics(port(mult=2.0), per_year),
           "neighbors": {str(n): metrics(port(ma=n), per_year) for n in (30, 40, 60, 70, 80, 100)}}
    daily = port()
    rng = random.Random(11)
    sh = []
    for _ in range(2000):
        s = []
        while len(s) < len(daily):
            k = rng.randrange(0, max(1, len(daily)-20))
            s += daily[k:k+20]
        sh.append(metrics(s[:len(daily)], per_year)["sharpe"])
    sh.sort()
    res["bootstrap"] = {"p_sharpe_positive": round(sum(x > 0 for x in sh)/len(sh), 3), "sharpe_5_50_95": [sh[100], sh[1000], sh[1900]]}
    return res


def run_stocks(data):
    out = {"datasets": {}, "crashes": {}}
    idx = data.get("indexes", {})
    per_idx = {name: run_symbol(dict(RULE_STOCKS, btc_filter=False), name, bars, STOCK_COSTS) for name, bars in idx.items()}
    for name, r in per_idx.items():
        out["datasets"][f"{name} index"] = {"rule": metrics(r["rule"], 252), "hold": metrics(r["hold"], 252),
                                            "from": dt.datetime.fromtimestamp(r["t"][0], dt.timezone.utc).year if r["t"] else None}
    if "S&P 500" in per_idx:
        r = per_idx["S&P 500"]
        rs, hs = list(zip(r["t"], r["rule"])), list(zip(r["t"], r["hold"]))
        out["crashes"] = {k: {"rule_pct": window(rs, *w), "hold_pct": window(hs, *w)} for k, w in CRASHES.items()}
    if "Nikkei 225" in per_idx:
        r = per_idx["Nikkei 225"]
        out["nikkei_1990_bust"] = {"rule_pct": window(list(zip(r["t"], r["rule"])), *NIKKEI_BUST),
                                   "hold_pct": window(list(zip(r["t"], r["hold"])), *NIKKEI_BUST)}
    etfs = data.get("etfs", {})
    spy = etfs.get("SPY")
    leader = L.Leader(spy) if spy else None
    leader_for = lambda s: leader if (s in EQUITY_ETFS and s != "SPY") else None  # noqa: E731
    per_etf = {}
    for sym, bars in etfs.items():
        lead = leader_for(sym)
        per_etf[sym] = run_symbol(RULE_STOCKS if lead else dict(RULE_STOCKS, btc_filter=False), sym, bars, STOCK_COSTS, lead)
    if per_etf:
        out["datasets"]["Multi-asset ETFs"] = {"rule": metrics([x for _, x in portfolio(per_etf, "rule")], 252),
                                               "hold": metrics([x for _, x in portfolio(per_etf, "hold")], 252)}
        out["etfs"] = {s: {"rule": metrics(r["rule"], 252), "hold": metrics(r["hold"], 252)} for s, r in per_etf.items()}
        out["stress"] = stress(etfs, RULE_STOCKS, STOCK_COSTS, 252, leader_for)
    out["verdict"] = score_stocks(out)
    return out


def run_fx(data):
    out = {"source": data.get("fx_source")}
    fx = data.get("fx", {})
    if not fx:
        out["verdict"] = {"verdict": "NO DATA", "checks": {}}
        return out
    per = {p: run_symbol(RULE_FX, p, bars, FX_COSTS) for p, bars in fx.items()}
    out["pairs"] = {p: {"rule": metrics(r["rule"], 260), "trades": len(r["trades"]),
                        "from": dt.datetime.fromtimestamp(r["t"][0], dt.timezone.utc).year if r["t"] else None} for p, r in per.items()}
    out["portfolio"] = metrics([x for _, x in portfolio(per, "rule")], 260)
    out["stress"] = stress(fx, RULE_FX, FX_COSTS, 260)
    out["verdict"] = score_fx(out)
    return out


def _stress_checks(st, card, hold_dd=None):
    checks = {}
    kept = st["lag_1d"]["sharpe"]/st["base"]["sharpe"] if st["base"]["sharpe"] > 0 else 0
    checks["survives_a_day_of_lag"] = (kept >= card["lag_1d_sharpe_kept_min"], f"keeps {kept*100:.0f}% of its Sharpe")
    checks["survives_double_costs"] = (st["double_costs"]["return_pct"] > 0, f"{st['double_costs']['return_pct']}%")
    ok = sum(m["return_pct"] > 0 and (hold_dd is None or m["max_drawdown_pct"] < hold_dd) for m in st["neighbors"].values())
    checks["neighbors_agree"] = (ok >= card["neighbors_ok_min"], f"{ok} of 6 neighboring MAs")
    p = st["bootstrap"]["p_sharpe_positive"]
    checks["bootstrap"] = (p >= card["bootstrap_p_sharpe_positive_min"], f"Sharpe > 0 in {p*100:.1f}% of resamples")
    return checks


def score_stocks(out):
    card, checks = SCORECARDS["stocks"], {}
    ds = out["datasets"]
    if ds:
        worse = [n for n, d in ds.items() if d["rule"]["max_drawdown_pct"] >= d["hold"]["max_drawdown_pct"]]
        checks["drawdown_below_hold_everywhere"] = (not worse, "all datasets" if not worse else "not in: "+", ".join(worse))
        prof = sum(d["rule"]["return_pct"] > 0 for d in ds.values())
        checks["profitable_in_most_datasets"] = (prof/len(ds) >= card["profitable_share_of_datasets"], f"{prof} of {len(ds)}")
    if out.get("crashes"):
        covered = [c for c in out["crashes"].values() if c["rule_pct"] is not None]
        held = sum(c["rule_pct"] > c["hold_pct"] for c in covered)
        checks["crashes_contained"] = (held >= card["crashes_contained_min"],
                                       f"lost less than holding in {held} of {len(covered)} covered crashes")
    if out.get("stress"):
        hold_dd = ds.get("Multi-asset ETFs", {}).get("hold", {}).get("max_drawdown_pct")
        checks.update(_stress_checks(out["stress"], card, hold_dd))
    return {"verdict": "PASS" if checks and all(v[0] for v in checks.values()) else "FAIL",
            "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in checks.items()}}


def score_fx(out):
    card, checks = SCORECARDS["fx"], {}
    p = out["portfolio"]
    checks["portfolio_sharpe"] = (p["sharpe"] >= card["portfolio_sharpe_min"], f"{p['sharpe']} vs {card['portfolio_sharpe_min']}")
    prof = sum(v["rule"]["return_pct"] > 0 for v in out["pairs"].values())
    checks["most_pairs_profitable"] = (prof/len(out["pairs"]) >= card["profitable_pairs_share_min"], f"{prof} of {len(out['pairs'])} pairs")
    checks["drawdown"] = (p["max_drawdown_pct"] <= card["max_drawdown_pct"], f"{p['max_drawdown_pct']}% vs {card['max_drawdown_pct']}%")
    checks.update(_stress_checks(out["stress"], card))
    return {"verdict": "PASS" if all(v[0] for v in checks.values()) else "FAIL",
            "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in checks.items()}}


def show(stocks, fx, missing):
    for title, res in (("STOCKS", stocks), ("FX", fx)):
        v = res["verdict"]
        print(f"\n===== {title}: {v['verdict']}")
        for k, c in v["checks"].items():
            print(f"   {'ok ' if c['pass'] else 'XX '} {k}: {c['detail']}")
    if missing:
        print(f"\n  sources missing: {missing}")
    print("\n  STOCKS: rule vs buy-and-hold")
    for n, d in stocks["datasets"].items():
        r, h = d["rule"], d["hold"]
        print(f"     {n:22} rule {r['cagr_pct']:>6}%/yr Sharpe {r['sharpe']:>5} dd {r['max_drawdown_pct']:>5}%   "
              f"hold {h['cagr_pct']:>6}%/yr Sharpe {h['sharpe']:>5} dd {h['max_drawdown_pct']:>5}%"
              + (f"   (from {d['from']})" if d.get("from") else ""))
    if stocks.get("crashes"):
        print("  named S&P 500 crashes (rule vs hold):")
        for k, c in stocks["crashes"].items():
            print(f"     {k:14} " + ("not covered by the data" if c["rule_pct"] is None
                                     else f"rule {c['rule_pct']:>7}%   hold {c['hold_pct']:>7}%"))
    if stocks.get("nikkei_1990_bust") and stocks["nikkei_1990_bust"]["rule_pct"] is not None:
        c = stocks["nikkei_1990_bust"]
        print(f"     Nikkei 1990-92 rule {c['rule_pct']:>7}%   hold {c['hold_pct']:>7}%")
    for sym, r in stocks.get("etfs", {}).items():
        print(f"       {sym:5} rule {r['rule']['cagr_pct']:>6}%/yr dd {r['rule']['max_drawdown_pct']:>5}%   "
              f"hold {r['hold']['cagr_pct']:>6}%/yr dd {r['hold']['max_drawdown_pct']:>5}%")
    for title, res in (("STOCKS (ETFs)", stocks), ("FX", fx)):
        st = res.get("stress")
        if not st:
            continue
        print(f"  stress, {title}:")
        for k in ("base", "lag_1d", "lag_2d", "double_costs"):
            print(f"     {k:13} {st[k]['cagr_pct']:>6}%/yr Sharpe {st[k]['sharpe']:>5} dd {st[k]['max_drawdown_pct']:>5}%")
        print("     MAs 30..100: " + "  ".join(f"{n}:{m['sharpe']}" for n, m in st["neighbors"].items()))
        print(f"     bootstrap Sharpe 5/50/95th: {st['bootstrap']['sharpe_5_50_95']}")
    if fx.get("pairs"):
        print(f"\n  FX ({fx['source']}; unlevered, carry NOT modeled): portfolio {fx['portfolio']['cagr_pct']}%/yr "
              f"Sharpe {fx['portfolio']['sharpe']} dd {fx['portfolio']['max_drawdown_pct']}%")
        for pr, r in fx["pairs"].items():
            print(f"     {pr} from {r['from']}: {r['rule']['cagr_pct']:>6}%/yr Sharpe {r['rule']['sharpe']:>5} "
                  f"dd {r['rule']['max_drawdown_pct']:>5}%  trades {r['trades']}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", required=True)
    r.add_argument("--json")
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        data = fetch()
        with gzip.open(args.out, "wt") as fh:
            json.dump(data, fh)
        print(f"wrote {args.out}; missing: {data['missing'] or 'none'}")
        return 0
    with gzip.open(args.data, "rt") as fh:
        data = json.load(fh)
    stocks, fx = run_stocks(data), run_fx(data)
    show(stocks, fx, data.get("missing"))
    if args.json:
        Path(args.json).write_text(json.dumps({"stocks": stocks, "fx": fx}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
