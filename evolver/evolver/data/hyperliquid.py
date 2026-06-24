"""Hyperliquid provider — hourly funding (+ premium) and candles. Granular and
historically spikier funding than CEXes; great for live/forward signal. Perp DEX
(no clean spot leg), so hedge cross-venue. Funding history caps at 500/request."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


def _info(body: dict, tries: int = 6):
    for attempt in range(tries):
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=json.dumps(body).encode(), headers={"content-type": "application/json"})
        try:
            return json.load(urllib.request.urlopen(req, timeout=15))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                time.sleep(2 ** attempt)   # backoff on rate limit
                continue
            raise


def funding_history(coin: str = "ETH", start_ms: int = 0, pages: int = 12) -> dict:
    """{ts_ms: hourly_rate}, paginated forward from start_ms (500/page)."""
    out, cur = {}, start_ms
    for _ in range(pages):
        data = _info({"type": "fundingHistory", "coin": coin, "startTime": cur})
        if not data:
            break
        for x in data:
            out[int(x["time"])] = float(x["fundingRate"])
        last = max(int(x["time"]) for x in data)
        if last <= cur or len(data) < 500:
            break
        cur = last + 1
    return out


def closes(coin: str = "ETH", interval: str = "1h", start_ms: int = 0) -> dict:
    """{ts_ms: close} from candleSnapshot."""
    data = _info({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_ms}})
    return {int(c["t"]): float(c["c"]) for c in data}
