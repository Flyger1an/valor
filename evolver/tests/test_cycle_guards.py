"""Compute-reclamation guards from the 2026-07-10 audit:
  * zero-information guard — cycle() must skip (not search) when NO genome can put min_n trades in
    the holdout, and must still run full cycles when the space can;
  * chronic-rejecter cooldown — pure pacing logic;
  * used_llm telemetry — the OPRO share of the search must be visible in every cycle summary.
No network, no API key."""
import os
import pathlib
import sys

os.environ.setdefault("EVOLVER_USE_LLM", "0")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_tick as rt  # noqa: E402

BASE, H = 1_700_000_000_000, 3_600_000
DATA = {"C0": {BASE + i * H: 1.0 + 0.001 * (i % 7) for i in range(1000)}}


def _fam(bt):
    return {"name": "guard_test", "refresh": None, "bt": bt, "fee": 5.0, "slip": 5.0,
            "space": {"x": (1.0, 3.0, float), "k": (2.0, 6.0, int)},
            "stab": ("x", "k"), "min_cov": 10, "min_n": 20}


def _sparse_bt(data, params, limits, lo=None, hi=None):
    """5 trades total, spread over the full range — no genome can fill a min_n=20 holdout."""
    out = [(BASE + i * 200 * H, 0.001) for i in range(5)]
    return [(t, v) for t, v in out if (lo is None or t >= lo) and (hi is None or t < hi)]


def _dense_bt(data, params, limits, lo=None, hi=None):
    """A trade every 3 hours with mixed small P&L — plenty of holdout n for any genome."""
    out = [(BASE + i * 3 * H, 0.0006 if i % 3 else -0.0005) for i in range(330)]
    return [(t, v) for t, v in out if (lo is None or t >= lo) and (hi is None or t < hi)]


def test_zero_information_guard():
    summ, cand = rt.cycle(_fam(_sparse_bt), DATA)
    assert cand is None and "holdout-starved" in summ and "min_n 20" in summ
    summ2, _ = rt.cycle(_fam(_dense_bt), DATA)               # dense space -> the guard lets it through
    assert "holdout-starved" not in summ2 and "OOS" in summ2
    return True


def test_used_llm_is_surfaced():
    summ, _ = rt.cycle(_fam(_dense_bt), DATA)
    assert "llm 0/" in summ                                   # EVOLVER_USE_LLM=0 -> 0 of N evaluated
    return True


def test_cooldown_pacing():
    A, E = rt.COOLDOWN_AFTER, rt.COOLDOWN_EVERY               # defaults 24 / 4
    assert not rt._cooldown_active(A - 1, 1)                  # below streak: never
    assert rt._cooldown_active(A, 1) and rt._cooldown_active(A + 50, E - 1)
    assert not rt._cooldown_active(A, 0) and not rt._cooldown_active(A, E)   # every Kth slot retests
    assert not rt._cooldown_active(A, 3 * E)
    saved = rt.COOLDOWN_AFTER
    rt.COOLDOWN_AFTER = 0                                     # 0 disables entirely
    try:
        assert not rt._cooldown_active(10_000, 1)
    finally:
        rt.COOLDOWN_AFTER = saved
    return True
