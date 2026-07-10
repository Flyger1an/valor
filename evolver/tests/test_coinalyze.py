"""Coinalyze daily-liq integration (2026-07-10): connector parsing, the refresh join/merge rules,
and the bar_hours funding fix that makes liquidation_print daily-capable. No network, no key."""
import os
import pathlib
import pickle
import sys
import tempfile
from types import SimpleNamespace

os.environ.setdefault("EVOLVER_USE_LLM", "0")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evolver.data.coinalyze as CA  # noqa: E402
from evolver.optimize.liquidation_print import run_liquidation_print  # noqa: E402
from evolver.optimize.liquidation_reversion import round_trip_cost  # noqa: E402
import research_tick as rt  # noqa: E402

BASE, D = 1_700_000_000_000, 86_400_000


def test_connector_parses_canned_response():
    canned = [{"symbol": "BTCUSDT_PERP.3", "history": [
        {"t": BASE // 1000, "l": 1234.5, "s": 0},
        {"t": BASE // 1000 + 86400, "l": None, "s": 999.0},
    ]}]
    saved = CA._get
    CA._get = lambda path, **kw: canned
    os.environ["COINALYZE_API_KEY"] = os.environ.get("COINALYZE_API_KEY", "test")
    try:
        out = CA.liquidation_daily("BTC")
    finally:
        CA._get = saved
    assert out[BASE] == (1234.5, 0.0)
    assert out[BASE + D] == (0.0, 999.0)                     # null l -> 0.0, never None
    assert CA.okx_symbol("SOL") == "SOLUSDT_PERP.3"
    return True


def test_refresh_liq_daily_join_and_merge():
    tmp = pathlib.Path(tempfile.mkdtemp()) / "liqd.pkl"
    # pre-existing capture for one day that Coinalyze no longer serves -> must be preserved
    old_day = BASE - 5 * D
    tmp.write_bytes(pickle.dumps({"BTC": {old_day: (90.0, 7e6, 0.0)}}))
    closes = {old_day: 90.0, BASE: 100.0, BASE + D: 101.0, BASE + 2 * D: 102.0}
    liq = {BASE: (5e6, 1e5), BASE + 2 * D: (0.0, 9e6)}       # no liq row for BASE+D
    saved = rt.LIQ_DAILY, rt.V, rt.UNIVERSE
    savedCA = CA.liquidation_daily
    rt.LIQ_DAILY = tmp
    rt.V = SimpleNamespace(daily_closes=lambda c, n: dict(closes))
    rt.UNIVERSE = ["BTC"]
    CA.liquidation_daily = lambda c, days=1600, symbol=None: dict(liq)
    os.environ["COINALYZE_API_KEY"] = os.environ.get("COINALYZE_API_KEY", "test")
    try:
        out = rt.refresh_liq_daily()
    finally:
        rt.LIQ_DAILY, rt.V, rt.UNIVERSE = saved
        CA.liquidation_daily = savedCA
    d = out["BTC"]
    assert d[BASE] == (100.0, 5e6, 1e5)                      # joined close + liq
    assert d[BASE + D] == (101.0, 0.0, 0.0)                  # close without liq -> zeros
    assert d[old_day] == (90.0, 7e6, 0.0)                    # prior capture preserved
    assert len(d) == 4
    return True


def test_bar_hours_charges_true_funding():
    # daily bars must charge 24x the funding drag per held bar — same fee/slip legs
    assert round_trip_cost(8.0, 3 * 24.0) > round_trip_cost(8.0, 3.0)
    uni = {"C": {BASE + i * D: (100.0 * (1 + 0.001 * (i % 5)), 1e6, 1e6) for i in range(300)}}
    hourly_view = run_liquidation_print(uni, {"liq_mult": 99.0})       # never triggers: no spike
    daily_view = rt.run_liq_print_daily(uni, {"liq_mult": 99.0})
    assert hourly_view == [] and daily_view == []                       # both honest no-trade
    return True
