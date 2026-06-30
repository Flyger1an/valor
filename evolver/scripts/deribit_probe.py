"""Observe-first probe for Deribit (vol-premium build, Phase 0) — confirms the data the variance-risk-
premium family needs is real, reachable, and deep enough BEFORE any connector is written. The premium =
implied vol (DVOL / option mark_iv) minus subsequent REALIZED vol (from the underlying price), so the
backtest needs: a DVOL history (implied side), an underlying price history (realized side), and the
option chain + greeks (for the eventual delta-hedged version). No auth, no orders. Run from anywhere
with reachability (sandbox, or the droplet where it'll run — verified 200 on both).

    python3 scripts/deribit_probe.py
"""
import json
import time
import urllib.error
import urllib.request

_BASE = "https://www.deribit.com/api/v2/public/"


def _get(method, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(_BASE + method + "?" + q, headers={"user-agent": "valor/0.1"})
    return json.load(urllib.request.urlopen(req, timeout=20)).get("result")


def line(label, fn):
    try:
        return f"  {label:32s} {fn()}"
    except urllib.error.HTTPError as e:
        return f"  {label:32s} HTTP {e.code} ({'GEO-BLOCKED?' if e.code in (403, 451) else 'err'})"
    except Exception as e:
        return f"  {label:32s} {type(e).__name__}: {str(e)[:55]}"


def main():
    now = int(time.time() * 1000)
    print("=== Deribit observe-first probe (mainnet public; no auth, no orders) ===\n")

    print("underlying + chain (delta-hedged version's inputs):")
    print(line("index btc_usd", lambda: f"${_get('get_index_price', index_name='btc_usd')['index_price']:,.0f}"))

    def chain():
        inst = _get("get_instruments", currency="BTC", kind="option", expired="false")
        exps = sorted({i["instrument_name"].split("-")[1] for i in inst})
        return f"{len(inst)} live options across {len(exps)} expiries; sample {inst[0]['instrument_name']}"
    print(line("BTC option chain", chain))

    def ticker():
        inst = _get("get_instruments", currency="BTC", kind="option", expired="false")
        tk = _get("ticker", instrument_name=inst[0]["instrument_name"])
        return f"mark_iv={tk.get('mark_iv')} greeks={list((tk.get('greeks') or {}).keys())}"
    print(line("option ticker (iv + greeks)", ticker))

    print("\nimplied side — DVOL (Deribit's vol index), the cleanest implied series:")
    for lbl, days, res in [("recent 7d / 1h", 7, 3600), ("1yr / 1d", 365, 86400), ("3yr / 1d", 1095, 86400)]:
        def dvol(days=days, res=res):
            d = _get("get_volatility_index_data", currency="BTC",
                     start_timestamp=now - days * 86400000, end_timestamp=now, resolution=res).get("data", [])
            span = (d[-1][0] - d[0][0]) / 86400000 if len(d) > 1 else 0
            return f"{len(d)} pts, {span:.0f}d span (latest close {d[-1][4] if d else '?'})"
        print(line(f"DVOL {lbl}", dvol))

    print("\nrealized side — underlying price history (compute realized vol from returns):")
    def price(days=1095):
        d = _get("get_tradingview_chart_data", instrument_name="BTC-PERPETUAL",
                 start_timestamp=now - days * 86400000, end_timestamp=now, resolution="1D").get("ticks", [])
        return f"{len(d)} daily pts (BTC-PERPETUAL)"
    print(line("BTC price 3yr / 1d", price))

    print("\nReading: chain+greeks feed the delta-hedged model; DVOL depth = how far the implied series "
          "goes; price depth = realized-vol source. Both need to span a vol regime change for an honest test.")


if __name__ == "__main__":
    main()
