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
            if len(r) >= 9 and r[8] != "1":   # drop the still-forming (unconfirmed) trailing bar
                continue
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
            if len(r) >= 9 and r[8] != "1":   # drop the still-forming (unconfirmed) trailing bar
                continue
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


def okx_oi_history(ccy: str, period: str = "1D") -> dict:
    """{ts_ms: open_interest} — total OKX contract open interest for a currency, from the rubik stats
    feed. OI is the 'how much leverage is in the system' signal (the OI-reversion family's input).
    NOTE: this feed has a LIMITED lookback (recent window only — OKX caps it), so the research cache
    ACCUMULATES it across cycles to build real history. Best-effort: returns {} on error/empty."""
    body = _get(f"https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume?ccy={ccy}&period={period}")
    if body.get("code") != "0":
        return {}
    out = {}
    for r in body.get("data", []):
        try:
            out[int(r[0])] = float(r[1])      # [ts_ms, open_interest, volume]
        except (ValueError, IndexError, TypeError):
            continue
    return out


def okx_liquidations(inst: str, pages: int = 6) -> dict:
    """{hour_ms: (long_liq_notional, short_liq_notional)} from the OKX liquidation-orders feed. A long
    liq = a long position force-SOLD (side='sell'); a short liq = a short force-BOUGHT (side='buy') —
    the actual forced flow, not a wick proxy. NOTE: this public feed is RECENT-only (and OKX has
    changed it over time), so the research cache ACCUMULATES it across cycles; if it stays empty the
    family just stays data-thin (a vendor feed is the real-history fix). Best-effort: {} on error."""
    agg, after = {}, None
    for _ in range(pages):
        url = f"https://www.okx.com/api/v5/public/liquidation-orders?instType=SWAP&instId={inst}&state=filled&limit=100"
        if after:
            url += f"&after={after}"
        try:
            body = _get(url)
        except Exception:
            break
        if body.get("code") != "0":
            break
        details, seen_ts = [], []
        for blk in body.get("data") or []:
            details.extend(blk.get("details") or [])
        if not details:
            break
        for d in details:
            try:
                t = int(d["ts"])
                seen_ts.append(t)
                hour = (t // 3_600_000) * 3_600_000
                notional = float(d["sz"]) * float(d.get("bkPx") or d.get("fillPx") or 0)
                ll, sl = agg.get(hour, (0.0, 0.0))
                if d.get("side") == "sell":           # a long was liquidated (force-sold)
                    agg[hour] = (ll + notional, sl)
                else:                                  # a short was liquidated (force-bought)
                    agg[hour] = (ll, sl + notional)
            except (KeyError, ValueError, TypeError):
                continue
        if not seen_ts or len(details) < 100:
            break
        after = min(seen_ts)                           # page to older records
    return agg


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
