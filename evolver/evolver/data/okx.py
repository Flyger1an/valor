"""OKX historical data + small stats helpers (pure stdlib)."""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request


def _get(url: str, tries: int = 6):
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"user-agent": "valor/0.1"})
        try:
            return json.load(urllib.request.urlopen(req, timeout=15))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                time.sleep(0.5 * (2 ** attempt))   # backoff on OKX rate limit
                continue
            raise


def okx_daily_closes(base: str, limit: int = 300, inst: str | None = None) -> dict:
    """{ts_ms: close} of UTC daily candles. Spot by default; pass inst=f'{base}-USDT-SWAP' for perp."""
    inst = inst or f"{base}-USDT"
    body = _get(f"https://www.okx.com/api/v5/market/candles?instId={inst}&bar=1Dutc&limit={limit}")
    if body.get("code") != "0":
        raise RuntimeError(f"OKX candles {inst}: {body.get('msg')}")
    return {int(r[0]): float(r[4]) for r in body["data"]}


def okx_intraday_closes(base: str, bar: str = "1H", bars: int = 720, inst: str | None = None) -> dict:
    """{ts_ms: close} of intraday candles via history-candles, paginated back ~bars."""
    inst = inst or f"{base}-USDT"
    out, after = {}, None
    while len(out) < bars:
        url = f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar={bar}&limit=100"
        if after:
            url += f"&after={after}"
        body = _get(url)
        data = body.get("data") or []
        if body.get("code") != "0" or not data:
            break
        for r in data:
            out[int(r[0])] = float(r[4])
        after = min(int(r[0]) for r in data)  # page older
        if len(data) < 100:
            break
    return out


def okx_intraday_ohlc(base: str, bar: str = "1H", bars: int = 11_000, inst: str | None = None) -> dict:
    """{ts_ms: (o,h,l,c)} via history-candles, paginated back ~bars (for backfilling a dataset)."""
    inst = inst or f"{base}-USDT-SWAP"
    out, after = {}, None
    while len(out) < bars:
        url = f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar={bar}&limit=100"
        if after:
            url += f"&after={after}"
        body = _get(url)
        data = body.get("data") or []
        if body.get("code") != "0" or not data:
            break
        for r in data:
            out[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        after = min(int(r[0]) for r in data)
        if len(data) < 100:
            break
    return out


def okx_candles_ohlc(base: str, bar: str = "1H", limit: int = 300, inst: str | None = None) -> dict:
    """{ts_ms: (o,h,l,c)} of recent LIVE candles (perp by default). CLOSED bars only — the
    in-progress candle (confirm=='0') is dropped so the runner never acts on an unfinished bar."""
    inst = inst or f"{base}-USDT-SWAP"
    body = _get(f"https://www.okx.com/api/v5/market/candles?instId={inst}&bar={bar}&limit={limit}")
    if body.get("code") != "0":
        raise RuntimeError(f"OKX candles {inst}: {body.get('msg')}")
    return {int(r[0]): (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            for r in body["data"] if len(r) < 9 or r[8] == "1"}


def okx_funding_history(inst: str, days: int = 300) -> dict:
    """{fundingTime_ms: rate} of 8h funding, paginated back ~days."""
    out, after, needed = {}, None, days * 3 + 10
    while len(out) < needed:
        url = f"https://www.okx.com/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if after:
            url += f"&after={after}"
        body = _get(url)
        data = body.get("data") or []
        if not data:
            break
        for r in data:
            out[int(r["fundingTime"])] = float(r["fundingRate"])
        after = min(int(r["fundingTime"]) for r in data)  # page to older records
        if len(data) < 100:
            break
    return out


def daily_funding(funding_by_ts: dict) -> dict:
    """Aggregate 8h funding into {utc_day_ms: summed_daily_rate}."""
    daily = {}
    for ts, rate in funding_by_ts.items():
        day = (ts // 86_400_000) * 86_400_000
        daily[day] = daily.get(day, 0.0) + rate
    return daily


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def std(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def half_life_hours(series, period_h: float = 24.0, fallback: float = 48.0) -> float:
    if len(series) < 8:
        return fallback
    avg = mean(series)
    num = den = 0.0
    for t in range(1, len(series)):
        xp = series[t - 1] - avg
        num += xp * (series[t] - series[t - 1])
        den += xp * xp
    if den == 0:
        return fallback
    phi = 1 + num / den
    if phi <= 0 or phi >= 1:
        return fallback
    h = (-math.log(2) / math.log(phi)) * period_h
    return min(max(h, 1.0), 24 * 14) if math.isfinite(h) else fallback
