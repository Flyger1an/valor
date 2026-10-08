"""Build Henry's research dataset from the Binance public data archive (data.binance.vision).

Hourly spot OHLCV plus perpetual funding (8h) for each coin, over N months, in one gzip JSON.
Public CDN, no keys, read-only. Runs wherever the archive is reachable (the droplet).

  python -m evolver.trading.henry_desk_data --months 6 --out /data/henry_desk.json.gz

Prices come from Binance, not Alpaca: same coins, deeper and more liquid books, so levels and
volume differ slightly from what Henry trades live. Good for research; noted in every report.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import time
import urllib.error
import urllib.request
import zipfile

BASE = "https://data.binance.vision/data"
# Valor symbol -> (spot pair, perp pair, perp price multiplier). SHIB perps trade per 1000 tokens.
SYMBOLS = {
    "BTC-USD": ("BTCUSDT", "BTCUSDT", 1),
    "ETH-USD": ("ETHUSDT", "ETHUSDT", 1),
    "ADA-USD": ("ADAUSDT", "ADAUSDT", 1),
    "SHIB-USD": ("SHIBUSDT", "1000SHIBUSDT", 1000),
    "WIF-USD": ("WIFUSDT", "WIFUSDT", 1),
    "SKY-USD": ("SKYUSDT", "SKYUSDT", 1),
}


def shift_months(when, k):
    """`when` moved back k calendar months (day clamped to 28 so every month is valid)."""
    y, m = when.year, when.month-k
    while m <= 0:
        y, m = y-1, m+12
    return when.replace(year=y, month=m, day=min(when.day, 28))


def months_back(n, end=None):
    end = end or dt.datetime.now(dt.timezone.utc)
    y, m, out = end.year, end.month, []
    for _ in range(n+1):  # include the current month's daily files below
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y-1, 12
    return list(reversed(out))


def fetch_rows(url, tries=3):
    for attempt in range(tries):
        try:
            raw = urllib.request.urlopen(url, timeout=30).read()
            z = zipfile.ZipFile(io.BytesIO(raw))
            return list(csv.reader(io.StringIO(z.read(z.namelist()[0]).decode())))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2**attempt)
        except (urllib.error.URLError, TimeoutError, zipfile.BadZipFile):
            time.sleep(2**attempt)
    return None


def _ms(value):
    v = int(value)
    return v//1000 if v > 10**14 else v  # newer dumps use microseconds


def klines(pair, months, log=print, offset=0):
    """{hour_start_seconds: [open, high, low, close, volume]} from monthly files, then the
    current month's daily files (monthly dumps publish after the month ends). With an offset,
    the window ends `offset` months ago and only complete monthly files are used."""
    out = {}
    now = dt.datetime.now(dt.timezone.utc)
    month_list = months_back(months, shift_months(now, offset))
    for ym in month_list[:-1]:
        rows = fetch_rows(f"{BASE}/spot/monthly/klines/{pair}/1h/{pair}-1h-{ym}.zip")
        if rows is None:
            log(f"  {pair} {ym}: not published")
            continue
        _add(out, rows)
    if offset:
        return out
    today = now.date()
    day = today.replace(day=1)
    while day < today:
        rows = fetch_rows(f"{BASE}/spot/daily/klines/{pair}/1h/{pair}-1h-{day.isoformat()}.zip")
        if rows:
            _add(out, rows)
        day += dt.timedelta(days=1)
    return out


def _add(out, rows):
    for r in rows:
        if r and r[0].isdigit():
            out[_ms(r[0])//1000] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]


def funding(pair, months, log=print, offset=0):
    """{funding_time_seconds: rate_per_8h} from USD-M perp monthly funding dumps."""
    out = {}
    for ym in months_back(months, shift_months(dt.datetime.now(dt.timezone.utc), offset))[:-1]:
        rows = fetch_rows(f"{BASE}/futures/um/monthly/fundingRate/{pair}/{pair}-fundingRate-{ym}.zip")
        if rows is None:
            log(f"  {pair} funding {ym}: not published")
            continue
        for r in rows:
            if len(r) >= 3 and r[0].isdigit():
                out[_ms(r[0])//1000] = float(r[2])
    return out


def build(months=6, symbols=None, log=print, offset=0):
    data = {"source": "binance_public_archive", "built_at": time.time(), "months": months,
            "months_ago_end": offset, "bar": "1h", "symbols": {}, "missing": {}}
    for sym, (spot, perp, _mult) in SYMBOLS.items():
        if symbols and sym not in symbols:
            continue
        log(f"{sym}: {spot} klines, {perp} funding")
        bars = klines(spot, months, log, offset)
        fund = funding(perp, months, log, offset)
        if not bars:
            data["missing"][sym] = "no spot klines in the archive"
            continue
        data["symbols"][sym] = {"pair": spot, "perp": perp,
                                "bars": [[t, *bars[t]] for t in sorted(bars)],
                                "funding": [[t, fund[t]] for t in sorted(fund)]}
        log(f"  {len(bars)} hourly bars, {len(fund)} funding prints")
    return data


def load(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--months", type=int, default=6)
    p.add_argument("--out", required=True)
    p.add_argument("--symbols", help="comma-separated subset of " + ",".join(SYMBOLS))
    p.add_argument("--months-ago", type=int, default=0,
                   help="end the window this many months ago (e.g. 6 for an out-of-sample holdout)")
    args = p.parse_args(argv)
    data = build(args.months, set(args.symbols.split(",")) if args.symbols else None, offset=args.months_ago)
    with gzip.open(args.out, "wt") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"wrote {args.out}: {len(data['symbols'])} symbols, missing {data['missing'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
