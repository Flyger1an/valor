"""FX carry — long high-yield currencies, short low-yield, cross-sectionally. The most-documented
FX risk premium (Lustig-Roussanov-Verdelhan): you're paid the rate differential for bearing crash risk.

The carry of being long a pair = base-currency rate − quote-currency rate. That needs HISTORICAL rate
differentials — OANDA spot candles don't carry them, and OANDA's financing endpoint is a current
snapshot (using it on past data would be look-ahead). So we embed historical **policy rates** (the
dominant carry driver, public, slow-moving) and rank pairs by the differential AT EACH DATE — no
look-ahead. The per-trade return includes the carry actually earned over the hold.

CAVEAT (load-bearing): RATES below are APPROXIMATE quarterly central-bank policy rates from training
knowledge — directionally right (the cross-sectional RANKING is robust), but verify/replace with a
real rates feed (FRED, central-bank sites) before trusting the bps. The gate judges the holdout; the
ranking quality is the input you should harden.

universe: {pair: {day_ms: close}} daily (OANDA form, e.g. "EUR_USD").
"""
from __future__ import annotations

import datetime as dt

from evolver.config import RiskLimits, DEFAULT_LIMITS

# Approximate quarterly policy rates (annual %), 2021-2025. RANKING is reliable; exact bps are not.
RATES = {
    "USD": [("2021-01", 0.25), ("2022-04", 0.5), ("2022-07", 1.75), ("2022-10", 3.25), ("2023-01", 4.5),
            ("2023-04", 5.0), ("2023-07", 5.5), ("2024-07", 5.5), ("2024-10", 4.75), ("2025-01", 4.5)],
    "EUR": [("2021-01", -0.5), ("2022-07", 0.0), ("2022-10", 1.5), ("2023-01", 2.5), ("2023-04", 3.5),
            ("2023-07", 4.0), ("2024-07", 3.75), ("2024-10", 3.25), ("2025-01", 2.75)],
    "JPY": [("2021-01", -0.1), ("2024-04", 0.0), ("2024-07", 0.1), ("2024-10", 0.25), ("2025-01", 0.5)],
    "GBP": [("2021-01", 0.1), ("2022-04", 0.75), ("2022-07", 1.75), ("2022-10", 3.0), ("2023-01", 4.0),
            ("2023-04", 4.5), ("2023-07", 5.25), ("2024-07", 5.0), ("2024-10", 4.75), ("2025-01", 4.5)],
    "AUD": [("2021-01", 0.1), ("2022-07", 1.35), ("2022-10", 2.85), ("2023-01", 3.35), ("2023-07", 4.1),
            ("2023-10", 4.35), ("2024-10", 4.35), ("2025-01", 4.1)],
    "CAD": [("2021-01", 0.25), ("2022-04", 1.0), ("2022-07", 2.5), ("2022-10", 3.75), ("2023-01", 4.5),
            ("2023-07", 5.0), ("2024-07", 4.5), ("2024-10", 3.75), ("2025-01", 3.0)],
    "CHF": [("2021-01", -0.75), ("2022-07", -0.25), ("2022-10", 0.5), ("2023-01", 1.0), ("2023-07", 1.75),
            ("2024-04", 1.5), ("2024-07", 1.25), ("2024-10", 1.0), ("2025-01", 0.5)],
    "NZD": [("2021-01", 0.25), ("2022-04", 1.5), ("2022-07", 2.5), ("2022-10", 4.25), ("2023-04", 5.25),
            ("2023-07", 5.5), ("2024-10", 4.75), ("2025-01", 4.25)],
}


def _ms(ym):
    y, m = ym.split("-")
    return int(dt.datetime(int(y), int(m), 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


_RATE_MS = {c: sorted((_ms(d), r) for d, r in tbl) for c, tbl in RATES.items()}


def _rate(ccy, ts):
    tbl = _RATE_MS.get(ccy)
    if not tbl:
        return None
    r = tbl[0][1]
    for ms, rate in tbl:
        if ms <= ts:
            r = rate
        else:
            break
    return r


DEFAULT_PARAMS = {"holding": 10, "quantile": 0.33, "skip": 1, "fee_bps": 1.0}


def run_fx_carry(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    hold, q, sk = int(p["holding"]), p["quantile"], int(p["skip"])
    fee = 2 * p["fee_bps"] / 1e4
    coins = sorted(universe)
    dates = sorted(set().union(*[set(universe[c]) for c in coins])) if coins else []
    if len(dates) < hold + sk + 4:
        return []
    close = {}
    for c in coins:
        s, last, a = universe[c], None, []
        for d in dates:
            if d in s:
                last = s[d]
            a.append(last)
        close[c] = a

    out, n = [], len(dates)
    hold_yr = hold / 365.0
    t = sk + 2
    while t + hold < n:
        if lo is not None and dates[t] < lo:
            t += hold
            continue
        if hi is not None and dates[t] >= hi:
            break
        carry, present = [], []
        for c in coins:
            if "_" not in c:
                continue
            base, quote = c.split("_")
            rb, rq = _rate(base, dates[t]), _rate(quote, dates[t])
            a = close[c]
            if rb is None or rq is None or not (a[t] and a[t + hold] and a[t] > 0):
                continue
            carry.append(rb - rq)            # carry of long pair = base rate − quote rate (annual %)
            present.append(c)
        if len(present) < 6:
            t += hold
            continue
        order = sorted(range(len(present)), key=lambda i: -carry[i])   # highest carry first
        k = max(1, int(len(present) * q))
        longs, shorts = order[:k], order[-k:]

        def tot(i):                          # price move + carry actually earned over the hold
            a = close[present[i]]
            return (a[t + hold] / a[t] - 1) + carry[i] / 100.0 * hold_yr

        lr = sum(tot(i) for i in longs) / len(longs)
        sr = sum(tot(i) for i in shorts) / len(shorts)
        out.append((dates[t], (lr - sr) - fee))
        t += hold
    return out
