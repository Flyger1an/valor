"""Live RV signal emitter -> Redis 'valor.signals' -> evolver-loop (the inner analyst loop).

Wires the original inner loop to LIVE data: generates pair z-score signals across a wide
universe from OKX and XADDs the dislocated ones (per-pair cooldown so the same dislocation
isn't re-spammed). The loop's analyst gates these -> trades on the ~2σ ones, accruing a real
inner-loop track record. Maximizes honest data points without loosening the analyst's bar.

    python3 scripts/signal_feed.py            # one emit pass (cron)
    python3 scripts/signal_feed.py --loop 1800   # every 30 min
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _l in ((ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []):
    if "=" in _l and not _l.strip().startswith("#"):
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from evolver.core.pair_signal import build_pair_signal  # noqa: E402  (single source: no drift vs shadow/calibration)
from evolver.data.okx import okx_candles_ohlc  # noqa: E402

STREAM = os.getenv("SIGNAL_STREAM", "valor.signals")
EMIT_Z = float(os.getenv("FEED_EMIT_Z", "1.5"))      # emit when |z| >= this; analyst gates ~2.1σ to trade
COOLDOWN_H = float(os.getenv("FEED_COOLDOWN_H", "3"))
WINDOW = 168
REQUIRE_STATIONARY = os.getenv("FEED_REQUIRE_STATIONARY", "true").lower() != "false"
_adf_sig = os.getenv("FEED_ADF_SIGNIFICANCE", "0.05")
ADF_SIGNIFICANCE = 0.1 if _adf_sig == "0.1" else 0.01 if _adf_sig == "0.01" else 0.05
STATE = pathlib.Path(os.getenv("EVOLVER_FEED_STATE", str(ROOT / ".signal_feed_state.json")))
# wide RV universe: majors vs ETH and vs BTC + a few cross-pairs
PAIRS = ([(a, "ETH") for a in ("SOL", "BNB", "AVAX", "LINK", "DOT", "ARB", "OP", "INJ",
                               "NEAR", "ATOM", "LTC", "ADA", "DOGE", "XRP")]
         + [(a, "BTC") for a in ("ETH", "SOL", "BNB", "DOGE", "LTC", "XRP", "AVAX", "LINK")]
         + [("SOL", "BNB"), ("AVAX", "SOL"), ("OP", "ARB"), ("DOT", "ATOM")])
ASSETS = sorted({a for p in PAIRS for a in p})


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gen(a, b, closes):
    """Live stat-arb-pair signal dict (locked contract) — delegates to the SHARED builder
    (evolver/core/pair_signal.py) so the feed, the shadow book, and the calibration denominator
    can never drift apart again."""
    sig, _z = build_pair_signal(a, b, closes, window=WINDOW,
                                adf_significance=ADF_SIGNIFICANCE,
                                require_stationary=REQUIRE_STATIONARY)
    if sig:
        sig["metadata"]["feed_require_stationary"] = REQUIRE_STATIONARY
    return sig


def emit_pass():
    import redis
    r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
    state = json.loads(STATE.read_text()) if STATE.exists() else {"last": {}, "emitted": 0}
    try:
        feed = {a: {t: v[3] for t, v in okx_candles_ohlc(a, "1H", 300).items()} for a in ASSETS}
    except Exception as e:
        return f"[{_now()}] feed error: {e}"
    n = skipped = 0
    for a, b in PAIRS:
        sig = gen(a, b, feed)
        if not sig:
            skipped += 1
            continue
        if abs(sig["zscore"]) < EMIT_Z:
            continue
        key = f"{a}{b}"
        if time.time() * 1000 - state["last"].get(key, 0) < COOLDOWN_H * 3.6e6:
            continue
        r.xadd(STREAM, {"payload": json.dumps(sig)})
        state["last"][key] = int(time.time() * 1000)
        n += 1
    state["emitted"] = state.get("emitted", 0) + n
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, STATE)
    return (
        f"[{_now()}] emitted {n} signals -> {STREAM} "
        f"(skipped {skipped} non-stationary/short; lifetime {state['emitted']})"
    )


def main():
    a = sys.argv[1:]
    if a and a[0] == "--loop":
        every = int(a[1]) if len(a) > 1 else 1800
        print(f"signal-feed loop: emit every {every}s over {len(PAIRS)} pairs")
        while True:
            try:
                print(emit_pass())
            except Exception as e:
                print(f"[{_now()}] pass error: {e}")
            time.sleep(every)
    else:
        print(emit_pass())


if __name__ == "__main__":
    main()
