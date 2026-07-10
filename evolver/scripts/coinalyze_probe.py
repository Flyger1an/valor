"""Observe-first probe of the Coinalyze API (free key) — BEFORE building the collector.

Establishes ground truth the backfill design depends on:
  1. auth style + rate limits actually honored
  2. exchange codes + the exact OKX/Gate/Binance BTC-perp symbol names
  3. liquidation-history: real depth per interval, max range per call, response shape
  4. UNITS + consistency: Coinalyze's hourly long/short liq vs OUR live-captured values for the
     SAME recent hours (evolver's okx_liq_print dataset) — if the scales disagree we must not
     blend the sources silently.

Reads COINALYZE_API_KEY from evolver/.env (never committed). ~40 req/min free tier: this probe
makes <15 calls. Run:  python3 scripts/coinalyze_probe.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _l in ((ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []):
    if "=" in _l and not _l.strip().startswith("#"):
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

BASE = "https://api.coinalyze.net/v1/"
KEY = os.getenv("COINALYZE_API_KEY", "")


def get(path, **params):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"api_key": KEY, "user-agent": "valor/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    if not KEY:
        print("no COINALYZE_API_KEY in env")
        return 1
    print("=== 1) auth + exchanges ===")
    ex = get("exchanges")
    print(f"  {len(ex)} exchanges: " + ", ".join(f"{e['name']}({e['code']})" for e in ex[:14]) + " ...")

    print("=== 2) BTC perp symbols per exchange ===")
    mkts = get("future-markets")
    btc = [m for m in mkts if m.get("base_asset") == "BTC" and m.get("is_perpetual")
           and m.get("quote_asset") in ("USDT", "USD")]
    codes = {e["code"]: e["name"] for e in ex}
    for m in btc[:12]:
        exch = codes.get(m["symbol"].split(".")[-1], "?")
        print(f"  {m['symbol']:22} {exch}")

    def sym_for(exch_name):
        for m in btc:
            if codes.get(m["symbol"].split(".")[-1]) == exch_name and m["quote_asset"] == "USDT":
                return m["symbol"]
        return None

    okx_sym, gate_sym = sym_for("OKX") or sym_for("OKEx"), sym_for("Gate.io")
    print(f"  -> OKX BTC perp: {okx_sym} | Gate BTC perp: {gate_sym}")

    print("=== 3) liquidation-history depth (OKX BTC, daily then hourly) ===")
    now = int(time.time())
    for interval, span_d, label in (("daily", 3650, "10yr ask"), ("1hour", 400, "400d ask")):
        time.sleep(1.6)
        try:
            rows = get("liquidation-history", symbols=okx_sym, interval=interval,
                       **{"from": now - span_d * 86400, "to": now}, convert_to_usd="true")
            hist = rows[0]["history"] if rows else []
            if hist:
                t0, t1 = hist[0]["t"], hist[-1]["t"]
                days = (t1 - t0) / 86400
                print(f"  {interval:6} ({label}): {len(hist)} pts, {days:.0f} days "
                      f"({dt.datetime.utcfromtimestamp(t0):%Y-%m-%d} -> "
                      f"{dt.datetime.utcfromtimestamp(t1):%Y-%m-%d}) "
                      f"sample keys={sorted(hist[-1].keys())}")
            else:
                print(f"  {interval}: EMPTY response")
        except Exception as e:
            print(f"  {interval}: {type(e).__name__}: {str(e)[:140]}")

    print("=== 4) UNITS check — Coinalyze vs OUR captured liq, same recent hours (OKX BTC) ===")
    time.sleep(1.6)
    rows = get("liquidation-history", symbols=okx_sym, interval="1hour",
               **{"from": now - 5 * 86400, "to": now}, convert_to_usd="true")
    ca = {h["t"] * 1000: (h.get("l"), h.get("s")) for h in (rows[0]["history"] if rows else [])}
    import pickle
    ours_path = pathlib.Path(os.getenv("EVOLVER_RESEARCH_LIQ_PRINT",
                                       str(ROOT / ".okx_liq_print_dataset.pkl")))
    if not ours_path.exists():
        print("  (no local okx_liq_print dataset — run this part on the droplet volume)")
        print(json.dumps({str(k): v for k, v in list(ca.items())[-4:]}, indent=1))
        return 0
    ours = pickle.loads(ours_path.read_bytes()).get("BTC", {})
    common = sorted(set(ca) & set(ours))[-8:]
    print(f"  overlapping hours: {len(set(ca) & set(ours))}; last 8 compared:")
    for t in common:
        print(f"   {dt.datetime.utcfromtimestamp(t/1000):%m-%d %H:%M}  "
              f"coinalyze L/S=({ca[t][0]}, {ca[t][1]})   ours L/S=({ours[t][1]:.0f}, {ours[t][2]:.0f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
