"""Backfill REAL historical RV signals from OKX daily candles -> signals.jsonl.

Produces the locked contract (shared/signal.schema.json) so the loop + optimizer can
accrue a track record FAST instead of waiting weeks for live signals. Pure stdlib.

    python3 scripts/backfill_signals.py --out /tmp/backfill_signals.jsonl

Note: this is a backtest signal source (rolling pair z-scores on real OKX closes),
separate from Valor's live TS engine but emitting the same contract.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import urllib.request

# (assetA, assetB, contract type)
PAIRS = [
    ("ETH", "BTC", "cointegration_spread"),
    ("ETH", "SOL", "stat_arb_pair"),
    ("SOL", "BTC", "stat_arb_pair"),
]
WINDOW = 30          # rolling lookback (days)
Z_THRESHOLD = 1.0    # emit a signal when |z| >= this
LIMIT = 300          # daily candles to pull per asset (~10 months)


def _iso(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mean(xs):
    return sum(xs) / len(xs)


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def _half_life_hours(series, period_h=24, fallback=48.0):
    if len(series) < 8:
        return fallback
    avg = _mean(series)
    num = den = 0.0
    for t in range(1, len(series)):
        xp = series[t - 1] - avg
        num += xp * (series[t] - series[t - 1])
        den += xp * xp
    if den == 0:
        return fallback
    phi = 1 + num / den
    if phi <= 0 or phi >= 1:
        return fallback
    h = (-math.log(2) / math.log(phi)) * period_h
    return min(max(h, 1.0), 24 * 14) if math.isfinite(h) else fallback


def okx_daily_closes(base: str) -> dict:
    url = f"https://www.okx.com/api/v5/market/candles?instId={base}-USDT&bar=1Dutc&limit={LIMIT}"
    req = urllib.request.Request(url, headers={"user-agent": "valor-backfill/0.1"})
    body = json.load(urllib.request.urlopen(req, timeout=15))
    if body.get("code") != "0":
        raise RuntimeError(f"OKX {base}: {body.get('msg')}")
    return {int(r[0]): float(r[4]) for r in body["data"]}  # ts_ms -> close


def build(out_path: str) -> list:
    bases = sorted({b for p in PAIRS for b in p[:2]})
    closes = {b: okx_daily_closes(b) for b in bases}
    signals = []
    for a, b, typ in PAIRS:
        ts_common = sorted(set(closes[a]) & set(closes[b]))
        ratio = [(t, closes[a][t] / closes[b][t]) for t in ts_common]
        for i in range(WINDOW, len(ratio)):
            window = [r for _, r in ratio[i - WINDOW:i]]
            sd = _std(window)
            if sd == 0:
                continue
            cur_ts, cur = ratio[i]
            m = _mean(window)
            z = (cur - m) / sd
            if abs(z) < Z_THRESHOLD:
                continue
            rets = [window[k] / window[k - 1] - 1 for k in range(1, len(window))]
            vol = _std(rets) if rets else 0.02
            signals.append({
                "signal_id": f"bt_{a}{b}_{cur_ts}",
                "timestamp": _iso(cur_ts),
                "type": typ,
                "assets": [a, b],
                "zscore": round(z, 3),
                "spread_value": round((cur - m) / m, 6),
                "expected_convergence_hours": round(_half_life_hours([r for _, r in ratio[i - WINDOW:i + 1]]), 2),
                "risk_score": round(min(0.9, 0.2 + vol * 8), 3),
                "confidence": round(min(0.97, 0.6 + abs(z) / 6), 3),
                "regime": "high_vol" if vol > 0.04 else "low_vol_mean_revert",
                "metadata": {"vol_7d": round(vol, 4), "window": WINDOW},
            })
    signals.sort(key=lambda s: s["timestamp"])
    pathlib.Path(out_path).write_text("\n".join(json.dumps(s) for s in signals) + "\n")
    return signals


if __name__ == "__main__":
    from collections import Counter

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/backfill_signals.jsonl")
    args = ap.parse_args()
    sigs = build(args.out)
    print(f"wrote {len(sigs)} signals -> {args.out}")
    print("by type:  ", dict(Counter(s["type"] for s in sigs)))
    print("by regime:", dict(Counter(s["regime"] for s in sigs)))
    if sigs:
        print("date range:", sigs[0]["timestamp"], "->", sigs[-1]["timestamp"])
