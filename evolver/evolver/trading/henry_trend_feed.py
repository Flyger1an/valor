"""Network jobs for the trend book (they run beside it, never inside it).

  screen  --out /henry/universe.json       one time, at book creation: pick the coins (UNIVERSE rule)
  seed    --out /henry/daily_seed.json     ~400 days of daily bars for the book's coins
  shadow  --out /henry/shadow.json         the same rule replayed on Binance daily data, last 60 days
  live    --out /market                    loop: Alpaca quotes every 10 s, closed 5-minute bars every 60 s,
                                           written as quotes.json / signals.json for the book to read

The book's coins come from /henry/universe.json when it exists, else from the policy.

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
from .contracts import utc_timestamp
from .engine import write_snapshot
from .henry_trend import RULE, UNIVERSE
from .ipc import read_object

DAY = 86400
ALPACA = "https://data.alpaca.markets/v1beta3/crypto/us"
KEEP_BARS = 360   # 30 hours of 5m bars: the book only reads bars newer than its last; seeds repair longer gaps


def _alpaca(path, params):
    url = f"{ALPACA}/{path}?"+urllib.parse.urlencode(params)
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"user-agent": "valor"}), timeout=30).read())


def alpaca_quotes(symbols):
    data = _alpaca("latest/quotes", {"symbols": ",".join(s.replace("-", "/") for s in symbols)})
    out = {}
    for sym, q in (data.get("quotes") or {}).items():
        if q.get("bp") and q.get("ap") and 0 < float(q["bp"]) <= float(q["ap"]):
            out[sym.replace("/", "-")] = {"bid": float(q["bp"]), "ask": float(q["ap"]), "timestamp": utc_timestamp(q["t"])}
    return out


def alpaca_bars_5m(symbols, start, now):
    params = {"symbols": ",".join(s.replace("-", "/") for s in symbols), "timeframe": "5Min", "limit": 10000,
              "start": dt.datetime.fromtimestamp(start, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    out, seen = {}, set()
    for _ in range(20):
        data = _alpaca("bars", params)
        for sym, bars in (data.get("bars") or {}).items():
            for b in bars:
                t = utc_timestamp(b["t"])
                if t+300 <= now:
                    out.setdefault(sym.replace("/", "-"), []).append(
                        {"timestamp": t, "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"], "volume": b["v"]})
        token = data.get("next_page_token")
        if not token or token in seen:
            break
        seen.add(token)
        params = {**params, "page_token": token}
    return out


def screen(rule=UNIVERSE, sample_gap=10.0, daily=None, quotes=None, sleep=time.sleep):
    """Apply the declared universe rule. Every candidate's evidence is kept, picked or not."""
    daily = daily or (lambda s: alpaca_daily([s], 420).get(s, []))
    quotes = quotes or alpaca_quotes
    now = time.time()
    rows = {}
    for coin in rule["candidates"]:
        sym = coin+"-USD"
        try:
            bars = [b for b in daily(sym) if b[0] < now-now % DAY]
        except Exception as exc:  # unlisted or unreachable: recorded, not picked
            rows[sym] = {"eligible": False, "why": f"no Alpaca data ({type(exc).__name__})"}
            continue
        if not bars:
            rows[sym] = {"eligible": False, "why": "not listed on Alpaca"}
            continue
        last30 = bars[-30:]
        rows[sym] = {"history_days": len(bars),
                     "dollar_volume_30d": round(sum(b[4]*b[5] for b in last30)/30, 2) if last30 else 0.0}
    listed = [s for s, r in rows.items() if "history_days" in r]
    spreads = {s: [] for s in listed}
    for k in range(rule["spread_samples"]):
        if k:
            sleep(sample_gap)
        try:
            got = quotes(listed)
        except Exception:
            got = {}
        for s, q in got.items():
            if s in spreads:
                spreads[s].append((q["ask"]-q["bid"])/((q["ask"]+q["bid"])/2)*10000)
    if not any(spreads.values()):
        raise ValueError("no live quotes from Alpaca: refusing to build a universe blind")
    for s in listed:
        r = rows[s]
        r["median_spread_bps"] = round(sorted(spreads[s])[len(spreads[s])//2], 1) if spreads[s] else None
        why = []
        if r["history_days"] < rule["min_history_days"]:
            why.append(f"only {r['history_days']} days of history")
        if r["median_spread_bps"] is None:
            why.append("no live quote")
        elif r["median_spread_bps"] > rule["max_median_spread_bps"]:
            why.append(f"spread {r['median_spread_bps']} bps")
        r["eligible"], r["why"] = not why, "; ".join(why) or "eligible"
    ranked = sorted((s for s in listed if rows[s]["eligible"]), key=lambda s: -rows[s]["dollar_volume_30d"])
    if len(ranked) < rule["min_eligible"]:
        raise ValueError(f"only {len(ranked)} eligible coins (need {rule['min_eligible']}); not building a thin book")
    picked = ranked[:rule["top"]]
    for s in rule["always"]:
        if s not in picked:
            if not rows.get(s, {}).get("history_days"):
                raise ValueError(f"{s} is required as the leader but has no Alpaca data")
            picked = picked[:rule["top"]-1]+[s]
    for k, s in enumerate(ranked):
        rows[s]["rank"] = k+1
    return {"built_at": now, "venue": rule["venue"], "rule": rule, "symbols": sorted(picked), "candidates": rows}


def symbols_for(universe_path, policy_path):
    if universe_path and Path(universe_path).exists():
        return sorted(json.loads(Path(universe_path).read_text())["symbols"])
    return sorted(Policy.from_dict(read_object(policy_path, limit=20_000)).allowed_instruments)


class LiveFeed:
    """Writes the two files the book's capture() reads. The book itself enforces freshness,
    closed bars only, and first-version-wins, so this stays deliberately small."""

    def __init__(self, symbols, out, quotes=None, bars=None):
        self.symbols, self.out = symbols, Path(out)
        self.quotes, self.bars = quotes or alpaca_quotes, bars or alpaca_bars_5m
        self.out.mkdir(parents=True, exist_ok=True)
        old = read_object(self.out/"signals.json", {}, limit=20_000_000)
        self.histories = {s: list(old.get("histories", {}).get(s, [])) for s in symbols}
        self.next_bars = 0.0

    def tick(self, now):
        q = self.quotes(self.symbols)
        write_snapshot(self.out/"quotes.json", {"source": "alpaca_public", "timestamp": now, "quotes": q})
        if now < self.next_bars:
            return
        self.next_bars = now+60
        newest = [h[-1]["timestamp"] for h in self.histories.values() if h]
        start = max(min(newest)-900, now-3*DAY) if len(newest) == len(self.symbols) else now-2*DAY
        for sym, bars in self.bars(self.symbols, start, now).items():
            if sym not in self.histories:
                continue
            have = {b["timestamp"] for b in self.histories[sym]}
            self.histories[sym] = sorted(self.histories[sym]+[b for b in bars if b["timestamp"] not in have],
                                         key=lambda b: b["timestamp"])[-KEEP_BARS:]
        write_snapshot(self.out/"signals.json", {"source": "alpaca_public", "timestamp": now, "histories": self.histories})


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
    """Alpaca daily bars (the venue Henry quotes from); per coin, the Binance archive if Alpaca has too few."""
    try:
        daily = alpaca_daily(symbols)
    except Exception as exc:  # unreachable or refused: fall back, and say so
        print(f"alpaca daily bars unavailable ({type(exc).__name__}); using the Binance archive")
        daily = {}
    short = [s for s in symbols if len(daily.get(s, [])) <= 100]
    for s in short:
        daily[s] = binance_daily(s)
    source = "alpaca_public_daily" if not short else f"alpaca_public_daily; binance_archive_daily for {','.join(short)}"
    return {"source": source, "built_at": time.time(), "daily": {s: daily.get(s, []) for s in symbols}}


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
    p.add_argument("command", choices=("screen", "seed", "shadow", "live"))
    p.add_argument("--policy", default="/config/henry-trend-policy.json")
    p.add_argument("--universe", default="/henry/universe.json")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    if args.command == "screen":
        if Path(args.out).exists():
            p.error("this book's universe is frozen; a re-screen needs a new book")
        data = screen()
        Path(args.out+".tmp").write_text(json.dumps(data, indent=1))
        Path(args.out+".tmp").replace(args.out)
        for s, r in sorted(data["candidates"].items(), key=lambda kv: kv[1].get("rank", 999)):
            mark = "PICK" if s in data["symbols"] else "    "
            print(f"  {mark} {s:10} {r.get('why', '')}  vol30d ${r.get('dollar_volume_30d', 0):,.0f}  "
                  f"spread {r.get('median_spread_bps')} bps  days {r.get('history_days')}")
        print(f"wrote {args.out}: {len(data['symbols'])} coins")
        return 0
    symbols = symbols_for(args.universe, args.policy)
    if args.command == "live":
        import signal
        import threading
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        feed = LiveFeed(symbols, args.out)
        while not stop.is_set():
            try:
                feed.tick(time.time())
            except Exception as exc:  # network blips: the book sees stale files and waits
                print(json.dumps({"event": "feed_error", "error_type": type(exc).__name__}), flush=True)
            stop.wait(10)
        return 0
    data = seed(symbols) if args.command == "seed" else shadow(symbols)
    tmp = Path(args.out+".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(args.out)
    sizes = {s: len(v) for s, v in data["daily"].items()} if args.command == "seed" else len(data["days"])
    print(f"wrote {args.out} from {data['source']}: {sizes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
