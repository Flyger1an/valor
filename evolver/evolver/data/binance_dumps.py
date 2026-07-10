"""Binance public data dumps (data.binance.vision) — DEEP history via CDN.

The CDN is reachable even where Binance's trading API is geo-blocked. Monthly CSV
zips give years of funding + klines — the source of choice for backtest/validation
across past high-funding regimes. Pure stdlib.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import urllib.request
import zipfile

_BASE = "https://data.binance.vision/data"


def _months_back(n: int, end: dt.datetime | None = None) -> list[str]:
    end = end or dt.datetime.now(dt.timezone.utc)
    y, m, out = end.year, end.month, []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _fetch_csv(url: str):
    raw = urllib.request.urlopen(url, timeout=25).read()
    z = zipfile.ZipFile(io.BytesIO(raw))
    return list(csv.reader(io.StringIO(z.read(z.namelist()[0]).decode())))


def metrics_oi_day(symbol: str, date_iso: str):
    """(close_price, oi_contracts) for one UTC day from the free daily METRICS dump (5-min rows,
    published since 2020-09). oi = the day's LAST row's sum_open_interest (base contracts — the
    honest positioning measure; USD value conflates price moves with position changes). close is
    derived from the SAME row as sum_open_interest_value / sum_open_interest = the mark price at
    ~23:55 UTC — same venue, same file, no kline fetch, sources never mixed.
    Returns None on 404 (symbol not listed yet / dump not published) or unusable rows."""
    url = f"{_BASE}/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date_iso}.zip"
    try:
        rows = _fetch_csv(url)
    except Exception:
        return None
    best = None
    for r in rows[1:]:
        # columns: create_time, symbol, sum_open_interest, sum_open_interest_value, ...
        if len(r) >= 4 and r[0].startswith(date_iso):
            best = r
    if not best:
        return None
    try:
        qty, val = float(best[2]), float(best[3])
    except ValueError:
        return None
    if qty <= 0 or val <= 0:
        return None
    return (val / qty, qty)


def funding_history(symbol: str = "ETHUSDT", months: int = 18) -> dict:
    """{calc_time_ms: 8h_rate} of futures (UM) funding over the last `months`."""
    out = {}
    for ym in _months_back(months):
        url = f"{_BASE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip"
        try:
            rows = _fetch_csv(url)
        except Exception:
            continue  # month missing / not published yet
        for row in rows:
            if len(row) >= 3 and row[0].isdigit():
                out[int(row[0])] = float(row[2])
    return out


def intraday_ohlc(symbol: str = "ETHUSDT", market: str = "futures/um",
                  bar: str = "1h", months: int = 18) -> dict:
    """{open_time_ms: (open, high, low, close)} of intraday klines — highs/lows let
    the backtest see the adverse intraday excursion daily closes smooth over."""
    out = {}
    for ym in _months_back(months):
        url = f"{_BASE}/{market}/monthly/klines/{symbol}/{bar}/{symbol}-{bar}-{ym}.zip"
        try:
            rows = _fetch_csv(url)
        except Exception:
            continue
        for row in rows:
            if row and row[0].isdigit():
                out[int(row[0])] = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
    return out


def daily_closes(symbol: str = "ETHUSDT", market: str = "futures/um", months: int = 18) -> dict:
    """{utc_day_ms: close} of 1d klines. market: 'futures/um' (perp) or 'spot'."""
    out = {}
    for ym in _months_back(months):
        url = f"{_BASE}/{market}/monthly/klines/{symbol}/1d/{symbol}-1d-{ym}.zip"
        try:
            rows = _fetch_csv(url)
        except Exception:
            continue
        for row in rows:
            if row and row[0].isdigit():
                out[int(row[0])] = float(row[4])  # close
    return out
