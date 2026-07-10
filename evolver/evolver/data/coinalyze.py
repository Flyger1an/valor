"""Coinalyze connector — deep DAILY liquidation-by-side history (free API key, ~40 req/min).

Probe findings (scripts/coinalyze_probe.py, 2026-07-10) that shaped this module:
  * free tier serves ~4.1 YEARS of DAILY long/short liquidation history (OKX BTC: back to
    2022-05) in a single call — vs the ~87d ceiling of our live hourly capture;
  * free-tier HOURLY history is only ~92d (no deeper than what we already hold) AND its hourly
    values disagree with our live-captured OKX aggregates by NON-constant factors (different
    capture instruments) — so hourly sources are NEVER blended; this module is daily-only;
  * OKX perp symbols look like  {COIN}USDT_PERP.3  (exchange code 3).

Auth: COINALYZE_API_KEY env (gitignored .env; never committed). Values are USD-converted
(convert_to_usd=true). Inherits the exchanges' liquidation-feed caps (throttled snapshots), so
totals are LOWER BOUNDS with side classification intact — same caveat class as the live capture.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

_BASE = "https://api.coinalyze.net/v1/"
_MIN_INTERVAL_S = 1.6          # stay politely under the 40 req/min free-tier cap
_last_call = [0.0]


def _get(path: str, tries: int = 4, **params):
    key = os.getenv("COINALYZE_API_KEY", "")
    if not key:
        raise RuntimeError("COINALYZE_API_KEY not set")
    wait = _MIN_INTERVAL_S - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    url = _BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"api_key": key, "user-agent": "valor/0.1"})
    for attempt in range(tries):
        try:
            _last_call[0] = time.time()
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                time.sleep(2.0 * (2 ** attempt))
                continue
            raise


def okx_symbol(coin: str) -> str:
    return f"{coin}USDT_PERP.3"


def liquidation_daily(coin: str, days: int = 1600, symbol: str | None = None) -> dict:
    """{day_ms: (long_liq_usd, short_liq_usd)} — daily long/short liquidation totals, as deep as
    the free tier serves (~4.1yr observed). Empty dict when the symbol isn't covered."""
    now = int(time.time())
    rows = _get("liquidation-history", symbols=symbol or okx_symbol(coin), interval="daily",
                convert_to_usd="true", **{"from": now - days * 86_400, "to": now})
    hist = rows[0].get("history", []) if rows else []
    return {int(h["t"]) * 1000: (float(h.get("l") or 0.0), float(h.get("s") or 0.0))
            for h in hist}
