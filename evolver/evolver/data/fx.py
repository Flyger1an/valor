"""OANDA v20 (practice) FX data — the FX twin of evolver/data/okx.py.

PRACTICE endpoint only (fake money). Reads OANDA_API_KEY from env (+ OANDA_ACCOUNT_ID for live pricing
/ the future demo executor). Like the OKX-demo executor, there is no live-money path here — going live
would be a separate, deliberately-written module. Instruments use OANDA form, e.g. "EUR_USD".

    from evolver.data.fx import oanda_candles, oanda_closes, fx_candles_history
    bars = oanda_candles("EUR_USD", "H1", 500)        # {ts_ms: (o,h,l,c)} completed candles
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OANDA_ENV = os.getenv("OANDA_ENV", "practice")
_BASE = "https://api-fxtrade.oanda.com" if OANDA_ENV == "live" else "https://api-fxpractice.oanda.com"


def _get(path: str, params: dict | None = None) -> dict:
    url = _BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    key = os.getenv("OANDA_API_KEY", "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}",
                                               "Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:          # rate-limited -> exponential backoff
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"OANDA {path}: HTTP {e.code} {(e.read() or b'')[:200]!r}") from None
    return {}


def _ts(s: str) -> int:
    """OANDA RFC-3339 (nanosecond) time -> epoch ms."""
    return int(dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
               .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def oanda_candles(instrument: str, granularity: str = "H1", count: int = 500) -> dict:
    """{ts_ms: (o,h,l,c)} of COMPLETED candles only (the in-progress candle, complete=false, is dropped
    — the FX analog of OKX's confirm filter, so the gate never acts on an unfinished bar)."""
    d = _get(f"/v3/instruments/{instrument}/candles",
             {"granularity": granularity, "count": min(count, 5000), "price": "M"})
    out = {}
    for c in d.get("candles", []):
        if not c.get("complete"):
            continue
        m = c["mid"]
        out[_ts(c["time"])] = (float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"]))
    return out


def fx_candles_history(instrument: str, granularity: str = "H1", bars: int = 8000) -> dict:
    """{ts_ms: (o,h,l,c)} paginated back ~bars (OANDA caps a single call at 5000), completed only."""
    out, to = {}, None
    while len(out) < bars:
        params = {"granularity": granularity, "count": 5000, "price": "M"}
        if to:
            params["to"] = to
        d = _get(f"/v3/instruments/{instrument}/candles", params)
        cands = d.get("candles", [])
        if not cands:
            break
        for c in cands:
            if c.get("complete"):
                m = c["mid"]
                out[_ts(c["time"])] = (float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"]))
        to = cands[0]["time"]                           # walk back from the earliest candle in the page
        if len(cands) < 5000:
            break
    return out


def oanda_closes(instrument: str, granularity: str = "D", count: int = 500) -> dict:
    return {t: v[3] for t, v in oanda_candles(instrument, granularity, count).items()}


def fx_closes_history(instrument: str, granularity: str = "D", bars: int = 800) -> dict:
    return {t: v[3] for t, v in fx_candles_history(instrument, granularity, bars).items()}


def oanda_accounts() -> list:
    """List the accounts the token can see (also the surest way to find your OANDA_ACCOUNT_ID)."""
    return _get("/v3/accounts").get("accounts", [])


def _load_env() -> None:
    """Load evolver/.env so the CLI works on the box without exported vars (mirrors the scripts)."""
    envf = pathlib.Path(__file__).resolve().parents[2] / ".env"
    if not envf.exists():
        return
    for line in envf.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main(argv: list[str]) -> int:
    _load_env()
    if not argv or argv[0] != "--check":
        print("usage: python -m evolver.data.fx --check   # verify the OANDA practice token + data path")
        return 0
    if not os.getenv("OANDA_API_KEY"):
        print("✖ no OANDA_API_KEY (env or evolver/.env) — add your PRACTICE token first")
        return 2
    print(f"env={OANDA_ENV}  base={_BASE}")
    try:
        accts = oanda_accounts()
    except Exception as e:
        print(f"✖ auth failed: {e}\n   (make sure the token is from the PRACTICE/demo account, not live)")
        return 2
    ids = [a.get("id") for a in accts]
    print(f"✓ token valid — {len(ids)} account(s): {ids}")
    print(f"  (put one in evolver/.env as OANDA_ACCOUNT_ID=... for the future demo executor)")
    try:
        bars = oanda_candles("EUR_USD", "H1", 5)
    except Exception as e:
        print(f"✖ data fetch failed: {e}")
        return 1
    if not bars:
        print("✖ no completed EUR_USD candles returned")
        return 1
    t = max(bars)
    when = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"✓ data OK — EUR_USD last completed H1 close {bars[t][3]} @ {when} ({len(bars)} bars)")
    print("→ ready. The FX hunt needs only OANDA_API_KEY; deploy with up -d --build.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
