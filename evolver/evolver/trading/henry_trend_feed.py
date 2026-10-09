"""Network job for the trend book (runs beside it, never inside it).

  seed    --out /henry/daily_seed.json     ~400 days of daily bars for the policy's coins
  shadow  --out /henry/shadow.json         the same rule replayed on Binance daily data, last 60 days

Seed source: Alpaca's public crypto daily bars (the venue Henry quotes from); Binance's public
archive if Alpaca is unreachable. The shadow always uses Binance, an independent source, so a bug
in Henry's live bar building shows up as a disagreement.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import henry_lab as L
from .contracts import Policy
from .henry_desk_data import BASE, _ms, fetch_rows, months_back, parallel
from .henry_trend import RULE
from .ipc import read_object

DAY = 86400


def alpaca_daily(symbols, days=420):
    start = dt.datetime.fromtimestamp(time.time()-days*DAY, dt.timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    out, token = {}, None
    for _ in range(50):
        params = {"symbols": ",".join(s.replace("-", "/") for s in symbols), "timeframe": "1Day", "start": start, "limit": 10000}
        if token:
            params["page_token"] = token
        url = "https://data.alpaca.markets/v1beta3/crypto/us/bars?"+urllib.parse.urlencode(params)
        data = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"user-agent": "valor"}), timeout=30).read())
        for sym, bars in data.get("bars", {}).items():
            key = sym.replace("/", "-")
            for b in bars:
                t = int(dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp())
                out.setdefault(key, {})[t-t % DAY] = [t-t % DAY, b["o"], b["h"], b["l"], b["c"], b["v"]]
        token = data.get("next_page_token")
        if not token:
            break
    return {s: [v[t] for t in sorted(v)] for s, v in out.items()}


def binance_daily(symbol, days=420):
    pair = symbol.split("-")[0]+"USDT"
    months = days//30+2
    out = {}
    for rows in parallel([f"{BASE}/spot/monthly/klines/{pair}/1d/{pair}-1d-{ym}.zip" for ym in months_back(months)[:-1]]):
        for r in rows or []:
            if r and r[0].isdigit():
                t = _ms(r[0])//1000
                out[t] = [t, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
    today = dt.datetime.now(dt.timezone.utc).date()
    d = today.replace(day=1)
    while d < today:
        rows = fetch_rows(f"{BASE}/spot/daily/klines/{pair}/1d/{pair}-1d-{d.isoformat()}.zip")
        for r in rows or []:
            if r and r[0].isdigit():
                t = _ms(r[0])//1000
                out[t] = [t, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        d += dt.timedelta(days=1)
    return [out[t] for t in sorted(out)]


def seed(symbols):
    try:
        daily = alpaca_daily(symbols)
        if len(daily) == len(symbols) and all(len(v) > 100 for v in daily.values()):
            return {"source": "alpaca_public_daily", "built_at": time.time(), "daily": daily}
    except Exception as exc:  # unreachable or refused: fall back, and say so
        print(f"alpaca daily bars unavailable ({type(exc).__name__}); using the Binance archive")
    return {"source": "binance_archive_daily", "built_at": time.time(), "daily": {s: binance_daily(s) for s in symbols}}


def shadow(symbols, days_out=60):
    daily = {s: binance_daily(s, 300) for s in symbols}
    leader = L.Leader(daily[L.LEADER]) if daily.get(L.LEADER) else None
    out = {}
    for sym, bars in daily.items():
        if len(bars) < RULE["ma"]+7:
            continue
        rows, _ = L.simulate(RULE, sym, bars, [], leader)
        for t, _, pos in rows[-days_out:]:
            out.setdefault(str(int(t)), {})[sym] = pos
    return {"source": "binance_archive_daily", "built_at": time.time(), "rule": RULE["id"], "days": out}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("seed", "shadow"))
    p.add_argument("--policy", default="/config/henry-trend-policy.json")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    symbols = sorted(Policy.from_dict(read_object(args.policy, limit=20_000)).allowed_instruments)
    data = seed(symbols) if args.command == "seed" else shadow(symbols)
    tmp = Path(args.out+".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(args.out)
    sizes = {s: len(v) for s, v in data["daily"].items()} if args.command == "seed" else len(data["days"])
    print(f"wrote {args.out} from {data['source']}: {sizes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
