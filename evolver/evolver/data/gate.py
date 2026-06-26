"""Gate.io USDT-perp connector — a SECOND crypto venue (cross-venue hunt). Exposes the same semantic
interface OKX does (hourly_ohlc / hourly_closes / daily_closes / funding / oi_daily / liquidations /
UNIVERSE), so the SAME engine + families run on Gate as a separate parallel hunt — selected by
EVOLVER_VENUE=gate. Verified reachable from the droplet (200 on all endpoints).

Gate quirks vs OKX (observed live): times are SECONDS (-> ms); prices are STRINGS (-> float). Bonus:
the contract_stats endpoint carries OI AND per-bar liquidation-by-side (long_liq_usd = longs
force-sold, short_liq_usd = shorts force-bought) WITH history — richer than OKX's separate feeds, so
liquidation_print gets real liq history here, not a sparse accumulate-over-time feed.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

_BASE = "https://api.gateio.ws/api/v4/futures/usdt"
# curated liquid majors (Gate lists each as {COIN}_USDT) — cross-venue with the OKX hunt
UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "DOT", "LTC", "ADA",
            "NEAR", "ARB", "OP", "INJ", "SUI", "APT", "SEI", "ATOM", "FIL"]


def _get(path, tries=5):
    for a in range(tries):
        req = urllib.request.Request(_BASE + path, headers={"user-agent": "valor/0.1"})
        try:
            return json.load(urllib.request.urlopen(req, timeout=20))
        except urllib.error.HTTPError as e:
            if e.code == 429 and a < tries - 1:
                time.sleep(0.5 * (2 ** a))
                continue
            raise


def _candles(coin, interval, limit):
    return _get(f"/candlesticks?contract={coin}_USDT&interval={interval}&limit={int(min(limit, 2000))}") or []


def hourly_ohlc(coin, n=2000):
    """{ts_ms: (o,h,l,c)} hourly."""
    return {int(r["t"]) * 1000: (float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]))
            for r in _candles(coin, "1h", n)}


def hourly_closes(coin, n=2000):
    return {int(r["t"]) * 1000: float(r["c"]) for r in _candles(coin, "1h", n)}


def daily_closes(coin, n=2000):
    return {int(r["t"]) * 1000: float(r["c"]) for r in _candles(coin, "1d", n)}


def funding(coin, days=300):
    """{ts_ms: 8h rate}. Gate keeps ~30 days of funding history (accumulates over time)."""
    d = _get(f"/funding_rate?contract={coin}_USDT&limit=1000") or []
    return {int(r["t"]) * 1000: float(r["r"]) for r in d}


def oi_daily(coin, period="1d"):
    """{ts_ms: open_interest} from contract_stats (~200 days)."""
    d = _get(f"/contract_stats?contract={coin}_USDT&interval={period}&limit=1000") or []
    return {int(r["time"]) * 1000: float(r["open_interest"]) for r in d if r.get("open_interest")}


def liquidations(coin):
    """{hour_ms: (long_liq_usd, short_liq_usd)} from contract_stats 1h — Gate aggregates liquidation
    notional BY SIDE with history, so liquidation_print has real history here (not accumulate-only)."""
    d = _get(f"/contract_stats?contract={coin}_USDT&interval=1h&limit=2000") or []
    return {int(r["time"]) * 1000: (float(r.get("long_liq_usd") or 0.0), float(r.get("short_liq_usd") or 0.0))
            for r in d}
