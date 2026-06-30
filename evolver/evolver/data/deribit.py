"""Deribit connector — for the vol-premium (variance-risk-premium) family, build #4. Pure-stdlib (no
numpy on the box). Provides the two sides of the premium plus the delta-hedged version's inputs:
  - implied side:  dvol_history() (the DVOL vol index), option_ticker() (per-option mark_iv + greeks)
  - realized side: price_history() (underlying perpetual, for realized vol)
  - chain:         option_chain(), index_price()

Public market data only — no auth, no orders. Verified live (Phase 0 probe, scripts/deribit_probe.py):
reachable from sandbox AND droplet (no geo-block); DVOL ~2.7yr daily, price ~3yr, 868 BTC options with
full greeks. Times are ms; DVOL/IV are annualized vol in PERCENT points (e.g. 44.75 = 44.75%).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

_BASE = "https://www.deribit.com/api/v2/public/"
UNIVERSE = ["BTC", "ETH"]   # Deribit's liquid option markets


def _get(method, tries: int = 5, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    for attempt in range(tries):
        req = urllib.request.Request(_BASE + method + "?" + q, headers={"user-agent": "valor/0.1"})
        try:
            return json.load(urllib.request.urlopen(req, timeout=20)).get("result")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                time.sleep(0.5 * (2 ** attempt))   # backoff on rate limit
                continue
            raise


def index_price(currency: str = "BTC") -> float:
    """Current underlying index (e.g. btc_usd)."""
    return _get("get_index_price", index_name=f"{currency.lower()}_usd")["index_price"]


def dvol_history(currency: str = "BTC", days: int = 1000, resolution: int = 86400) -> dict:
    """{ts_ms: DVOL close} — Deribit's implied-vol index (annualized %), the IMPLIED side of the premium.
    resolution in seconds (86400=daily, 3600=hourly). ~2.7yr of daily history available."""
    now = int(time.time() * 1000)
    d = _get("get_volatility_index_data", currency=currency,
             start_timestamp=now - days * 86400000, end_timestamp=now, resolution=resolution)
    return {int(r[0]): float(r[4]) for r in (d or {}).get("data", [])}   # rows are [ts,o,h,l,c]


def price_history(currency: str = "BTC", days: int = 1000, resolution: str = "1D") -> dict:
    """{ts_ms: close} of the perpetual — the REALIZED-vol source. ~3yr of daily history available."""
    now = int(time.time() * 1000)
    d = _get("get_tradingview_chart_data", instrument_name=f"{currency}-PERPETUAL",
             start_timestamp=now - days * 86400000, end_timestamp=now, resolution=resolution)
    if not d or d.get("status") == "no_data":
        return {}
    return {int(t): float(c) for t, c in zip(d.get("ticks", []), d.get("close", []))}


def option_chain(currency: str = "BTC") -> list:
    """[{instrument_name, strike, expiry_ms, type}] of LIVE options (for the delta-hedged version)."""
    inst = _get("get_instruments", currency=currency, kind="option", expired="false") or []
    return [{"instrument_name": i["instrument_name"], "strike": i.get("strike"),
             "expiry_ms": i.get("expiration_timestamp"), "type": i.get("option_type")} for i in inst]


def option_ticker(instrument_name: str) -> dict:
    """{mark_iv, greeks{delta,gamma,vega,theta,rho}, mark_price, underlying_price} — per-option implied
    vol + greeks (annualized % for mark_iv)."""
    tk = _get("ticker", instrument_name=instrument_name) or {}
    return {"mark_iv": tk.get("mark_iv"), "greeks": tk.get("greeks") or {},
            "mark_price": tk.get("mark_price"), "underlying_price": tk.get("underlying_price")}
