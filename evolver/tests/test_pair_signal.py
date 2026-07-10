"""Fixes from the 2026-07-10 engine audit, locked in:
  1. ONE pair-signal builder (core/pair_signal.py) — the feed, the shadow book, and the calibration
     denominator must be literally the same code (the shadow's un-gated copy was measuring a
     different signal population than the live loop trades).
  2. refresh_liq_print must ACCUMULATE history (the old merge silently dropped everything older
     than the current ~2000h fetch window — depth was pinned at ~87d forever).
No network, no API key; scripts are imported with stubbed connectors."""
import math
import os
import pathlib
import pickle
import random
import sys
import tempfile
from types import SimpleNamespace

os.environ.setdefault("EVOLVER_USE_LLM", "0")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evolver.core.pair_signal import build_pair_signal, stated_confidence  # noqa: E402
from evolver.core import calibration as C  # noqa: E402

BASE, H = 1_700_000_000_000, 3_600_000


def _closes(seed=7, n=400, mode="revert"):
    """Two-leg closes whose ratio is OU-stationary ('revert'), trending ('trend'), or too short."""
    rng = random.Random(seed)
    a, b, x, pb = {}, {}, 0.0, 50.0
    for i in range(n):
        if mode == "revert":
            x = 0.7 * x + rng.gauss(0, 0.004)
        else:
            x = 0.004 * i + rng.gauss(0, 0.0005)     # deterministic drift -> unit root
        pb *= math.exp(rng.gauss(0, 0.01))
        t = BASE + i * H
        b[t] = pb
        a[t] = pb * 2.0 * math.exp(x)
    return {"A": a, "B": b}


def test_builder_gates_and_confidence_single_source():
    sig, z = build_pair_signal("A", "B", _closes(mode="revert"))
    assert sig is not None and z is not None
    assert sig["type"] == "stat_arb_pair" and sig["assets"] == ["A", "B"]
    assert sig["confidence"] == round(stated_confidence(z), 3)
    assert C.stated_confidence is stated_confidence          # calibration: literally the same object
    # trending ratio: GATED for entry, but z still returned (exit checks on open positions need it)
    gs, gz = build_pair_signal("A", "B", _closes(mode="trend"))
    assert gs is None and gz is not None
    # ungated variant still builds (the feed's FEED_REQUIRE_STATIONARY=false escape hatch)
    us, _ = build_pair_signal("A", "B", _closes(mode="trend"), require_stationary=False)
    assert us is not None and us["metadata"]["spread_stationary"] is False
    # short data -> nothing at all
    assert build_pair_signal("A", "B", _closes(n=50)) == (None, None)
    return True


def test_feed_and_shadow_share_the_builder():
    import signal_feed as SF
    import shadow_analyst as SA
    good = _closes(mode="revert")
    sig = SF.gen("A", "B", good)
    ssig, sz = SA._signal("A", "B", good)
    assert sig is not None and ssig is not None
    assert sig["zscore"] == ssig.zscore                      # identical z from identical code
    assert sig["confidence"] == ssig.confidence
    trend = _closes(mode="trend")
    assert SF.gen("A", "B", trend) is None                   # feed gates it...
    s2, z2 = SA._signal("A", "B", trend)
    assert s2 is None and z2 is not None                     # ...shadow gates it identically (THE fix),
    return True                                              # with z preserved for exits


def test_liq_print_accumulates_beyond_fetch_window():
    import research_tick as rt
    tmp = pathlib.Path(tempfile.mkdtemp()) / "liq.pkl"
    old = {BASE + i * H: (100.0, 5.0, 2.0) for i in range(300)}      # captured long ago
    tmp.write_bytes(pickle.dumps({"BTC": old}))
    new_start = BASE + 4000 * H                                       # fetch window far past old
    fresh_cl = {new_start + i * H: 101.0 for i in range(100)}
    fresh_lq = {new_start + 5 * H: (9.0, 1.0)}
    saved = rt.LIQ_PRINT, rt.V, rt.UNIVERSE
    rt.LIQ_PRINT = tmp
    rt.V = SimpleNamespace(hourly_closes=lambda c, n: dict(fresh_cl),
                           liquidations=lambda c: dict(fresh_lq))
    rt.UNIVERSE = ["BTC"]
    try:
        out = rt.refresh_liq_print()
    finally:
        rt.LIQ_PRINT, rt.V, rt.UNIVERSE = saved
    d = out["BTC"]
    assert all(ts in d for ts in old), "history older than the fetch window must be PRESERVED"
    assert d[BASE] == (100.0, 5.0, 2.0)                      # old captured liq intact
    assert d[new_start + 5 * H] == (101.0, 9.0, 1.0)         # fresh window overlaid
    assert len(d) == 400                                      # 300 old + 100 new, nothing dropped
    cap = max(d) - rt.MONTHS * 30 * 24 * H                    # rolling ~15mo cap still enforced
    assert min(d) >= cap
    return True
