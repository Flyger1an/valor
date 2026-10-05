"""Read-only market data. Public Coinbase data is paper-only; remote orders require Alpaca quotes."""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request

from .contracts import Quote, decimal, utc_timestamp


def public_get(path, params=None):
    url = "https://api.exchange.coinbase.com" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Valor-study/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def timestamp(value):
    return utc_timestamp(value)


class CoinbaseData:
    source = "coinbase_public"

    def quotes(self, instruments):
        result = {}
        for symbol in instruments:
            ticker = public_get("/products/" + symbol + "/ticker")
            result[symbol] = Quote(symbol, ticker["bid"], ticker["ask"], timestamp(ticker["time"]))
        return result

    def bars(self, instruments, now):
        # One bounded page per product per refresh. Older closed candles accumulate on disk.
        result = {}
        for symbol in instruments:
            raw = public_get("/products/" + symbol + "/candles", {"granularity": 300})
            result[symbol] = [{"timestamp": float(b[0]), "low": str(b[1]), "high": str(b[2]),
                               "open": str(b[3]), "close": str(b[4]), "volume": str(b[5])}
                              for b in raw if b[0] + 300 <= now]
        return result


class AlpacaData:
    source = "alpaca"

    def __init__(self, http):
        self.http = http

    def quotes(self, instruments):
        from .alpaca import latest_quotes
        return latest_quotes(self.http, instruments)

    def bars(self, instruments, now):
        from .alpaca import recent_bars
        return recent_bars(self.http, instruments, now)

    def increments(self, instruments):
        result = {}
        for s in instruments:
            asset = self.http.request("GET", "/v2/assets/"+urllib.parse.quote(s.replace("-", "/"), safe=""))
            if not asset or not asset.get("tradable") or decimal(asset["min_trade_increment"]) <= 0:
                raise ValueError("missing tradable instrument increments")
            result[s] = str(decimal(asset["min_trade_increment"]))
        return result


def merge_bars(existing, incoming, now):
    """Validate OHLC and freeze closed observations. A changed historical bar aborts the refresh."""
    merged = {b["timestamp"]: b for b in existing}
    for bar in incoming:
        stamp = float(decimal(bar["timestamp"]))
        low, high, opening, close, volume = (decimal(bar[k]) for k in ("low", "high", "open", "close", "volume"))
        if (stamp % 300 or stamp <= 0 or stamp + 300 > now or not 0 < low <= min(opening, close)
                or not max(opening, close) <= high or volume < 0):
            raise ValueError("invalid or incomplete market bar")
        previous = merged.get(stamp)
        if previous and any(decimal(previous[k]) != decimal(bar[k]) for k in ("low", "high", "open", "close", "volume")):
            raise ValueError("closed market bar changed; research provenance needs review")
        merged[stamp] = bar
    return sorted((b for b in merged.values() if b["timestamp"] >= now - 100*86400), key=lambda b: b["timestamp"])
