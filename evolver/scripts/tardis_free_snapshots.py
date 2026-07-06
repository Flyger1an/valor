"""Step 0 of the premium-data plan — harvest Tardis.dev's FREE first-of-month Deribit
options_chain files (no API key: datasets.tardis.dev serves the 1st of every month free —
verified 2026-07-06) into a small distilled OI-by-strike dataset, 2019-04 → today.

BANDWIDTH HONESTY: each daily file is tick-level (0.3–6.5 GB gz). We need ONE chain snapshot
per day, which lives in the first minutes of the stream — so we STREAM-gunzip and stop once a
full sweep of the chain has been seen (no new symbols for a while), reading ~5–50 MB per file
instead of GBs.

Distilled shape mirrors deribit.oi_by_strike():
  {date_iso: {coin: {"spot": median_underlying, "by_expiry":
      {expiry_ms: {"dte": days, "oi": total, "und": per-expiry underlying,
                   "strikes": {K: (call_oi, put_oi)}}}}}}
Saved to evolver/.tardis_monthly_oi.pkl (resumable — reruns skip harvested dates).

HONEST LIMIT (why this is only Step 0): the 1st of a month is never an expiry Friday, so this
CANNOT deliver the pinning verdict — it validates the machinery and supports a monthly-frequency
pre-test (scripts/pin_pretest.py) that informs whether the paid daily history is worth buying.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import pathlib
import pickle
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / ".tardis_monthly_oi.pkl"
URL = "https://datasets.tardis.dev/v1/deribit/options_chain/{y}/{m:02d}/01/OPTIONS.csv.gz"
UA = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) valor-research/0.1"}
START = (2019, 4)
MAX_ROWS = 400_000          # absolute safety cap per file
SWEEP_QUIET = 40_000        # stop after this many rows with no NEW symbol (chain fully seen)
MIN_ROWS = 20_000           # never stop before this many rows


def month_firsts():
    today = dt.date.today()
    y, m = START
    while (y, m) <= (today.year, today.month):
        yield dt.date(y, m, 1)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def harvest_day(date: dt.date) -> dict | None:
    """Stream one free file, stop after a full chain sweep, distil OI-by-strike per coin."""
    url = URL.format(y=date.year, m=date.month)
    req = urllib.request.Request(url, headers=UA)
    per_symbol: dict = {}          # symbol -> latest parsed row (within the window we read)
    seen, quiet = set(), 0
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            gz = gzip.GzipFile(fileobj=resp)
            reader = csv.DictReader(io.TextIOWrapper(gz, newline=""))
            for i, row in enumerate(reader):
                sym = row.get("symbol", "")
                if not sym:
                    continue
                if sym not in seen:
                    seen.add(sym)
                    quiet = 0
                else:
                    quiet += 1
                per_symbol[sym] = row
                if i >= MAX_ROWS or (i >= MIN_ROWS and quiet >= SWEEP_QUIET):
                    break
    except urllib.error.HTTPError as e:
        print(f"  {date}: HTTP {e.code} — skipped")
        return None
    except Exception as e:
        print(f"  {date}: {type(e).__name__}: {e} — skipped")
        return None

    out: dict = {}
    unds: dict = {}
    for sym, row in per_symbol.items():
        coin = sym.split("-")[0]
        if coin not in ("BTC", "ETH"):
            continue
        try:
            K = float(row["strike_price"])
            exp_ms = int(int(row["expiration"]) / 1000)          # micros -> ms
            typ = row["type"]
            oi = float(row.get("open_interest") or 0.0)
            und = float(row.get("underlying_price") or 0.0)
        except (KeyError, ValueError, TypeError):
            continue
        day_ms = int(dt.datetime(date.year, date.month, date.day,
                                 tzinfo=dt.timezone.utc).timestamp() * 1000)
        if exp_ms <= day_ms:
            continue
        c = out.setdefault(coin, {"by_expiry": {}})
        e = c["by_expiry"].setdefault(exp_ms, {"dte": (exp_ms - day_ms) / 86_400_000,
                                               "oi": 0.0, "strikes": {}, "und": None})
        call, put = e["strikes"].get(K, (0.0, 0.0))
        e["strikes"][K] = (call + oi, put) if typ == "call" else (call, put + oi)
        e["oi"] += oi
        if und > 0:
            unds.setdefault(coin, {}).setdefault(exp_ms, []).append(und)
    for coin, c in out.items():
        alls = []
        for exp_ms, e in c["by_expiry"].items():
            vals = unds.get(coin, {}).get(exp_ms, [])
            e["und"] = round(statistics.median(vals), 2) if vals else None
            alls += vals
        c["spot"] = round(statistics.median(alls), 2) if alls else None
    return out or None


def main():
    cache = pickle.loads(OUT.read_bytes()) if OUT.exists() else {}
    dates = [d for d in month_firsts() if d.isoformat() not in cache]
    print(f"harvesting {len(dates)} first-of-month snapshots ({len(cache)} already cached)")
    for d in dates:
        t0 = time.time()
        snap = harvest_day(d)
        if snap:
            cache[d.isoformat()] = snap
            OUT.write_bytes(pickle.dumps(cache))            # save-per-date: resumable
            coins = {c: (len(v["by_expiry"]), int(sum(e["oi"] for e in v["by_expiry"].values())))
                     for c, v in snap.items()}
            print(f"  {d}: {coins} ({time.time()-t0:.0f}s)")
        time.sleep(1.0)                                      # politeness to the free tier
    print(f"done: {len(cache)} months in {OUT}")


if __name__ == "__main__":
    main()
