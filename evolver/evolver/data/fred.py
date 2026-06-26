"""FRED (St. Louis Fed) historical short-rate feed for fx_carry — REAL, verifiable interest rates to
replace fx_carry's embedded approximate table. Free API; needs FRED_API_KEY (free key at
fredaccount.stlouisfed.org). Graceful: no key -> returns {} and fx_carry falls back to its table.

SERIES are best-guess FRED ids per currency. US/Euro-area are reliable; the others (OECD immediate/
interbank) are inconsistent and some were discontinued ~2023 — run `python -m evolver.data.fred --check`
to see which resolve and how recent they are, then edit SERIES. Currencies without FRED data fall back
to fx_carry's embedded table per-currency. Cached to a pkl (rates move monthly).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import pickle
import sys
import urllib.error
import urllib.parse
import urllib.request

# Best-guess FRED series ids (monthly, % p.a.). Verify/edit with --check.
SERIES = {
    "USD": "FEDFUNDS",          # Federal Funds Effective Rate — reliable, current
    "EUR": "ECBDFR",            # ECB Deposit Facility Rate — reliable, current
    "JPY": "IRSTCB01JPM156N",   # OECD immediate call/interbank — may be discontinued
    "GBP": "IRSTCB01GBM156N",
    "AUD": "IRSTCB01AUM156N",
    "CAD": "IRSTCB01CAM156N",
    "CHF": "IRSTCB01CHM156N",
    "NZD": "IRSTCB01NZM156N",
}
_BASE = "https://api.stlouisfed.org/fred/series/observations"
RATES_PKL = pathlib.Path(os.getenv("FX_RATES_PKL", str(pathlib.Path(__file__).resolve().parents[2] / ".fx_rates.pkl")))


def fred_series(series_id: str, start: str = "2019-01-01") -> dict:
    """{ts_ms: rate} for a FRED series, or {} on missing key / error / empty."""
    key = os.getenv("FRED_API_KEY", "")
    if not key:
        return {}
    url = _BASE + "?" + urllib.parse.urlencode(
        {"series_id": series_id, "api_key": key, "file_type": "json", "observation_start": start})
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.loads(r.read())
    except Exception:
        return {}
    out = {}
    for o in d.get("observations", []):
        v = o.get("value", ".")
        if v in (".", ""):
            continue
        try:
            ms = int(dt.datetime.strptime(o["date"], "%Y-%m-%d")
                     .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
            out[ms] = float(v)
        except Exception:
            continue
    return out


def fetch_rates(series: dict | None = None) -> dict:
    """{ccy: {ts_ms: rate}} for currencies whose FRED series resolved (non-empty)."""
    out = {}
    for ccy, sid in (series or SERIES).items():
        d = fred_series(sid)
        if d:
            out[ccy] = d
    return out


def refresh_to_pkl(path: pathlib.Path | None = None) -> dict:
    """Fetch + persist real rates to the cache pkl (rates move monthly). Returns what resolved."""
    path = path or RATES_PKL
    rates = fetch_rates()
    if rates:
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps(rates))
        os.replace(tmp, path)
    return rates


def _load_env() -> None:
    envf = pathlib.Path(__file__).resolve().parents[2] / ".env"
    if not envf.exists():
        return
    for line in envf.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main(argv: list[str]) -> int:
    _load_env()
    if not os.getenv("FRED_API_KEY"):
        print("✖ no FRED_API_KEY (env or evolver/.env) — get a free key at fredaccount.stlouisfed.org")
        return 2
    print("FRED coverage check (✓ = real rates; ✖ = falls back to fx_carry's embedded table):")
    ok = 0
    for ccy, sid in SERIES.items():
        d = fred_series(sid)
        if d:
            last = max(d)
            when = dt.datetime.fromtimestamp(last / 1000, dt.timezone.utc).strftime("%Y-%m")
            print(f"  ✓ {ccy} {sid:18} {len(d):3} obs, latest {d[last]:.2f}% @ {when}")
            ok += 1
        else:
            print(f"  ✖ {ccy} {sid:18} no data — edit SERIES or it uses the embedded table")
    print(f"{ok}/{len(SERIES)} currencies have real FRED rates.")
    if argv[:1] == ["--save"]:
        refresh_to_pkl()
        print(f"→ saved to {RATES_PKL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
