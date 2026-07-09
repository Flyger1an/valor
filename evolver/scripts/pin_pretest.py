"""Step 0 pre-test: does spot gravitate toward max-pain? — on ~7 years of FREE monthly snapshots.

Uses the distilled first-of-month Deribit OI-by-strike dataset (scripts/tardis_free_snapshots.py)
plus free Binance daily closes (data.binance.vision) for the outcome at each expiry.

Per month x coin: dominant expiry (max OI, 14-45 dte) -> max-pain (evolver.optimize.options_flow,
the SAME function the live family uses) -> gap g = (MP - S0)/S0 -> outcome r = S_T/S0 - 1 at expiry.
Readouts: directional hit rate (sign r == sign g) vs 50% binomial null, OLS beta of r on g, and
the median |distance-to-pin| ratio.

HONEST LIMITS (this is a pre-test, NOT the verdict): month-start OI ≈ 3-4 weeks pre-expiry (the
pin thesis is strongest in the final days, with the final book); one snapshot/month; BTC & ETH are
correlated (effective n < printed n); outcome uses Binance daily close vs Deribit 08:00 settlement
(~16h slop on a ~30d horizon). A pass here justifies buying daily history; it clears no gate.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import pathlib
import pickle
import sys
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evolver.optimize.options_flow import max_pain  # noqa: E402

OI_PKL = ROOT / ".tardis_monthly_oi.pkl"
PX_PKL = ROOT / ".binance_daily_closes.pkl"
BURL = "https://data.binance.vision/data/spot/monthly/klines/{sym}/1d/{sym}-1d-{y}-{m:02d}.zip"
# The three DTE bands the doc reports — running this script reproduces its whole table (the headline
# anti-pin lives in the 1.5-8d expiring-weekly band, NOT the default 14-45d monthly band).
BANDS = [("14-45d monthly", 14.0, 45.0), ("8-14d mid", 8.0, 14.0), ("1.5-8d expiring-weekly", 1.5, 8.0)]


def binance_daily_closes(coin: str, start=(2019, 4)) -> dict:
    """{date_iso: close} from the free monthly 1d-kline zips; cached + resumable."""
    cache = pickle.loads(PX_PKL.read_bytes()) if PX_PKL.exists() else {}
    out = cache.setdefault(coin, {})
    sym = f"{coin}USDT"
    today = dt.date.today()
    y, m = start
    while (y, m) <= (today.year, today.month):
        tag = f"{y}-{m:02d}"
        if not any(k.startswith(tag) for k in out):        # month not yet cached
            try:
                req = urllib.request.Request(BURL.format(sym=sym, y=y, m=m),
                                             headers={"user-agent": "valor/0.1"})
                raw = urllib.request.urlopen(req, timeout=60).read()
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    rows = csv.reader(io.TextIOWrapper(z.open(z.namelist()[0])))
                    for r in rows:
                        if not r or not r[0].isdigit():
                            continue
                        ts = int(r[0])
                        ts = ts // 1000 if ts > 10**14 else ts   # some dumps stamp micros
                        d = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).date()
                        out[d.isoformat()] = float(r[4])
            except Exception:
                pass                                        # current partial month etc.
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    PX_PKL.write_bytes(pickle.dumps(cache))
    return out


def collect_observations(dte_lo, dte_hi):
    oi = pickle.loads(OI_PKL.read_bytes())
    px = {c: binance_daily_closes(c) for c in ("BTC", "ETH")}
    obs = []
    for date_iso in sorted(oi):
        for coin, book in oi[date_iso].items():
            cands = {e: v for e, v in book["by_expiry"].items()
                     if dte_lo <= v["dte"] <= dte_hi and v["strikes"] and v["oi"] > 0}
            if not cands:
                continue
            exp_ms, dom = max(cands.items(), key=lambda kv: kv[1]["oi"])
            s0 = dom.get("und") or book.get("spot")
            mp = max_pain(dom["strikes"])
            if not s0 or not mp or s0 <= 0:
                continue
            exp_date = dt.datetime.fromtimestamp(exp_ms / 1000, dt.timezone.utc).date()
            st = px[coin].get(exp_date.isoformat())
            if st is None:                                  # expiry in the future / gap
                continue
            g = mp / s0 - 1
            r = st / s0 - 1
            obs.append({"date": date_iso, "coin": coin, "dte": round(dom["dte"], 1),
                        "oi": round(dom["oi"]), "s0": s0, "mp": mp, "st": st,
                        "gap": g, "ret": r,
                        "dist_ratio": abs(st - mp) / abs(s0 - mp) if s0 != mp else None})
    return obs


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def report(obs, band_label=""):
    n = len(obs)
    print(f"[{band_label}] {n} month x coin observations "
          f"({sum(1 for o in obs if o['coin'] == 'BTC')} BTC / "
          f"{sum(1 for o in obs if o['coin'] == 'ETH')} ETH), "
          f"{min(o['date'] for o in obs)} .. {max(o['date'] for o in obs)}")
    for label, rows in [("POOLED", obs),
                        ("BTC", [o for o in obs if o["coin"] == "BTC"]),
                        ("ETH", [o for o in obs if o["coin"] == "ETH"])]:
        m = len(rows)
        if m < 10:
            continue
        hits = sum(1 for o in rows if o["gap"] * o["ret"] > 0)
        # binomial normal approx vs p=0.5
        zb = (hits - m * 0.5) / math.sqrt(m * 0.25)
        p_hit = 2 * (1 - _norm_cdf(abs(zb)))
        gs = [o["gap"] for o in rows]
        rs = [o["ret"] for o in rows]
        mg, mr = sum(gs) / m, sum(rs) / m
        sxx = sum((g - mg) ** 2 for g in gs)
        beta = sum((g - mg) * (r - mr) for g, r in zip(gs, rs)) / sxx if sxx > 0 else 0.0
        resid = [r - (mr + beta * (g - mg)) for g, r in zip(gs, rs)]
        se = (sum(e * e for e in resid) / max(m - 2, 1) / sxx) ** 0.5 if sxx > 0 else float("inf")
        tb = beta / se if se > 0 else 0.0
        drs = sorted(o["dist_ratio"] for o in rows if o["dist_ratio"] is not None)
        med_dr = drs[len(drs) // 2] if drs else float("nan")
        print(f"  {label:6} n={m:3d} | hit rate {hits}/{m} = {hits/m:.0%} (p={p_hit:.3f}) | "
              f"OLS beta {beta:+.2f} (t={tb:+.2f}) | median |dist-to-pin| ratio {med_dr:.2f}")
    print("  read: beta>0 => spot pulled TOWARD pin, beta<0 => REPELLED. The hit-rate p uses a 50% "
          "null that crypto positive drift + gap-sign asymmetry violate — lean on beta/t, not hit p.")
    print("  caveats: month-start OI (weak form), BTC/ETH correlated (effective n < printed), no fee claim.")


if __name__ == "__main__":
    if not OI_PKL.exists():
        print("no OI dataset — run scripts/tardis_free_snapshots.py first")
        sys.exit(1)
    print("=== max-pain pin pre-test across DTE bands (reproduces docs/step0-free-data-pretest.md) ===\n")
    for band_label, lo, hi in BANDS:
        obs = collect_observations(lo, hi)
        if obs:
            report(obs, band_label)
        else:
            print(f"[{band_label}] no observations")
        print()
