"""Observe-first probe for candidate crypto venues BEFORE building a connector. Hits each venue's
PUBLIC API for the 5 data types the crypto families need — OHLC, funding (+ how deep), open interest,
liquidations, universe size — and reports reachability + availability. No auth, no orders. Run from
anywhere with reachability (your box, or the droplet where it'll actually run — geo may differ)."""
import json
import urllib.error
import urllib.request


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"user-agent": "valor-probe/0.1"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def span_days(ts_list):
    ts = [int(t) for t in ts_list if t]
    if len(ts) < 2:
        return 0
    rng = max(ts) - min(ts)
    return rng / 86_400_000 if rng > 10 ** 12 else rng / 86_400   # ms vs s


def line(label, fn):
    try:
        return f"  {label:30s} {fn()}"
    except urllib.error.HTTPError as e:
        return f"  {label:30s} HTTP {e.code} ({'GEO-BLOCKED?' if e.code in (403, 451) else 'err'})"
    except Exception as e:
        return f"  {label:30s} {type(e).__name__}: {str(e)[:50]}"


def probe_bybit():
    print("BYBIT (api.bybit.com, category=linear):")
    print(line("kline BTCUSDT 1h", lambda: f"{len(get('https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60&limit=200')['result']['list'])} bars"))
    def fund():
        d = get("https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=200")["result"]["list"]
        return f"{len(d)} records, span {span_days([x['fundingRateTimestamp'] for x in d]):.0f}d"
    print(line("funding history", fund))
    print(line("open interest 1d", lambda: f"{len(get('https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=1d&limit=200')['result']['list'])} points"))
    print(line("liquidations (REST?)", lambda: "no REST endpoint (Bybit liq is WS-only)"))
    print(line("universe (USDT perps)", lambda: f"{sum(1 for t in get('https://api.bybit.com/v5/market/tickers?category=linear')['result']['list'] if t['symbol'].endswith('USDT'))} symbols"))


def probe_gate():
    print("GATE (api.gateio.ws, futures/usdt):")
    print(line("candles BTC_USDT 1h", lambda: f"{len(get('https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=BTC_USDT&interval=1h&limit=200'))} bars"))
    def fund():
        d = get("https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract=BTC_USDT&limit=200")
        return f"{len(d)} records, span {span_days([x['t'] for x in d]):.0f}d"
    print(line("funding rate", fund))
    print(line("contract_stats (OI) 1d", lambda: f"{len(get('https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract=BTC_USDT&interval=1d&limit=200'))} points"))
    print(line("liq_orders", lambda: f"{len(get('https://api.gateio.ws/api/v4/futures/usdt/liq_orders?contract=BTC_USDT&limit=100'))} liquidations"))
    print(line("universe (contracts)", lambda: f"{len(get('https://api.gateio.ws/api/v4/futures/usdt/contracts'))} contracts"))


if __name__ == "__main__":
    print("=== candidate crypto-venue probe (no auth, no orders) ===\n")
    probe_bybit()
    print()
    probe_gate()
    print("\nReading: bars/points OK = usable; funding span = how far back (vs OKX's ~95d); "
          "GEO-BLOCKED = won't work from this network (the DROPLET is the real test).")
