"""Learning-loop closures — functional proof. The rules under test are load-bearing:
calibration is humility-only (never inflates), learns at the rate information arrives
(shrinkage + min-n + staleness guards), sizing shrinks but gates don't move, provenance is
honest (a silent LLM fallback is VISIBLE), and prompt variants can't break the analyst.
No API key required; all paths are temp files."""
import os
import tempfile
import pathlib
import time

import evolver.core.calibration as C
from evolver.core.calibration import (compute_calibration, write_calibration,
                                      load_calibration, conv_scale)
from evolver.core.signal import Signal
from evolver.core.sim import PerpPaperSim
from evolver.agents.analyst import decide, decide_with_meta
from evolver.agents import prompts as P
from evolver.agents.critic import reflect
from evolver.config import DEFAULT_LIMITS, DEFAULT_STRATEGY
from evolver.obs.decisions import record_decision

RISK = {"new_pos_pct": 0.08, "new_leverage": 2.0}


def _tmp(name):
    return pathlib.Path(tempfile.mkdtemp()) / name


def _no_calib():
    """Point the module default at a path that doesn't exist -> consumers run uncalibrated."""
    C.CALIB_PATH = _tmp("absent.json")


def _closes(n, conv_rate, z=2.2):
    """Synthetic shadow closes: entry_z=2.2 -> stated confidence ~0.733."""
    k = int(round(n * conv_rate))
    return ([{"entry_z": z, "converged": True, "divergence": -0.001}] * k
            + [{"entry_z": z, "converged": False, "divergence": -0.003}] * (n - k))


def _sig(sid="T1", z=2.4, conf=0.8, risk=0.3, regime="low_vol"):
    return Signal.from_dict({
        "signal_id": sid, "timestamp": "2026-07-06 00:00", "type": "stat_arb_pair",
        "assets": ("SOL", "ETH"), "zscore": z, "spread_value": 0.02,
        "expected_convergence_hours": 12.0, "risk_score": risk, "confidence": conf,
        "regime": regime, "metadata": {},
    })


def test_calibration_math_humility_and_guards():
    # measured reality (44% realized vs ~73% stated) -> scale < 1, shrunk, in band
    c = compute_calibration(_closes(64, 0.44))
    assert c is not None and 0.3 <= c["conv_scale"] < 1.0
    assert abs(c["realized_conv_rate"] - 0.44) < 0.02
    # HUMILITY-ONLY: even absurdly good measured reality never inflates past 1.0
    good = compute_calibration(_closes(200, 0.99, z=0.9))   # stated ~0.3, realized ~0.99
    assert good["conv_scale"] == 1.0
    # thin sample -> refuses to calibrate at all
    assert compute_calibration(_closes(10, 0.0)) is None
    # shrinkage: same rate at small vs large n -> small n stays closer to 1.0 (the prior)
    small, big = compute_calibration(_closes(41, 0.44)), compute_calibration(_closes(400, 0.44))
    assert small["conv_scale"] > big["conv_scale"]
    return True


def test_calibration_roundtrip_and_staleness():
    p = _tmp("calib.json")
    c = compute_calibration(_closes(64, 0.44))
    write_calibration(c, p)
    got = load_calibration(p)
    assert got and got["conv_scale"] == c["conv_scale"]
    assert conv_scale(got) == c["conv_scale"] and conv_scale(None) == 1.0
    # a fossil calibration is ignored (shadow stopped writing -> revert to raw behavior)
    stale = {**c, "updated_epoch": int(time.time()) - 30 * 86400}
    write_calibration(stale, p)
    assert load_calibration(p) is None
    return True


def test_sim_applies_calibration_deterministically():
    sim = PerpPaperSim(100_000)
    dec = {"action": "long", "direction": "long_spread", "size_usd": 5000.0, "leverage": 2.0}
    _no_calib()
    raw = [sim.execute(_sig(f"S{i}"), dec) for i in range(300)]
    p = _tmp("calib.json")
    write_calibration({**compute_calibration(_closes(400, 0.20)), "conv_scale": 0.3}, p)
    C.CALIB_PATH = p
    cal = [sim.execute(_sig(f"S{i}"), dec) for i in range(300)]
    r_raw = sum(1 for f in raw if f.converged) / 300
    r_cal = sum(1 for f in cal if f.converged) / 300
    assert r_cal < r_raw * 0.6, (r_raw, r_cal)          # measured humility bites hard
    assert cal[0].calib_version and not raw[0].calib_version
    again = sim.execute(_sig("S0"), dec)                 # deterministic under a fixed calibration
    assert again.net_pnl_usd == cal[0].net_pnl_usd and again.converged == cal[0].converged
    _no_calib()
    return True


def test_sizing_shrinks_but_gates_do_not():
    _no_calib()
    d0 = decide(_sig(), RISK, DEFAULT_LIMITS)
    p = _tmp("calib.json")
    write_calibration({**compute_calibration(_closes(400, 0.20)), "conv_scale": 0.5}, p)
    C.CALIB_PATH = p
    d1 = decide(_sig(), RISK, DEFAULT_LIMITS)
    assert abs(d1["size_usd"] - d0["size_usd"] * 0.5) < 0.01     # humility on size
    assert d1["action"] == d0["action"] == "short"               # direction untouched
    assert d1["confidence"] == d0["confidence"]                  # stated units preserved for gates
    assert d1["confidence_calibrated"] < d1["confidence"]
    gated = decide(_sig(conf=0.5), RISK, DEFAULT_LIMITS)         # gate thresholds unmoved
    assert gated["action"] == "neutral" and "calib_version" not in gated
    _no_calib()
    return True


class _FakeLLM:
    def __init__(self, content=None, raise_=False):
        self.content, self.raise_ = content, raise_

    def invoke(self, _msgs):
        if self.raise_:
            raise RuntimeError("model down")
        return type("R", (), {"content": self.content})


def test_provenance_is_honest():
    _no_calib()
    d, m = decide_with_meta(_sig(), {}, RISK, DEFAULT_LIMITS, None)   # no LLM configured
    assert m["source"] == "deterministic" and m["model"] == "deterministic"
    ok = _FakeLLM('{"action":"short","direction":"short_spread","size_usd":4000,'
                  '"leverage":9.0,"entry":{},"exit":{},"confidence":0.9,"rationale":"r"}')
    d, m = decide_with_meta(_sig(), {}, RISK, DEFAULT_LIMITS, ok)
    assert m["source"] == "llm" and len(m["prompt_sha"]) == 8 and m["prompt_variant"] == "v1"
    assert d["leverage"] <= DEFAULT_LIMITS.max_leverage              # clamp still bites
    broken = _FakeLLM(raise_=True)                                    # silent fallback made VISIBLE
    d, m = decide_with_meta(_sig(), {}, RISK, DEFAULT_LIMITS, broken)
    assert m["source"] == "deterministic" and d["action"] in {"long", "short", "neutral"}
    return True


def test_prompt_registry_and_fallback():
    text, name, sha = P.get_analyst_prompt()
    assert name == "v1" and text == P.ANALYST_V1 and len(sha) == 8
    P.VARIANTS_PATH = _tmp("variants.json")
    sha2 = P.register_variant("v2-test", "Challenger prompt {max_leverage}")
    t2, n2, s2 = P.get_analyst_prompt("v2-test")
    assert n2 == "v2-test" and s2 == sha2 and "Challenger" in t2
    t3, n3, _ = P.get_analyst_prompt("no-such-variant")              # unknown -> v1, never fails
    assert n3 == "v1" and t3 == P.ANALYST_V1
    try:
        P.register_variant("v1", "hijack the incumbent")
        assert False, "reserved name must raise"
    except ValueError:
        pass
    return True


def test_hostile_llm_output_is_sanitized():
    """Review finding: NaN passes min(), negatives sign-invert P&L, a string leverage crashed the
    tick. All must now sanitize or fall back — never crash, never escape the envelope."""
    _no_calib()
    nan = _FakeLLM('{"action":"long","direction":"long_spread","size_usd":NaN,'
                   '"leverage":NaN,"entry":{},"exit":{},"confidence":NaN,"rationale":"r"}')
    d, m = decide_with_meta(_sig(z=-2.4), {}, RISK, DEFAULT_LIMITS, nan)
    assert m["source"] == "llm"
    import math as _m
    assert _m.isfinite(d["size_usd"]) and _m.isfinite(d["leverage"]) and _m.isfinite(d["confidence"])
    neg = _FakeLLM('{"action":"long","direction":"long_spread","size_usd":-5000,'
                   '"leverage":-3,"entry":{},"exit":{},"confidence":0.5,"rationale":"r"}')
    d, _ = decide_with_meta(_sig(z=-2.4), {}, RISK, DEFAULT_LIMITS, neg)
    assert d["size_usd"] >= 0 and d["leverage"] >= 0.1        # no sign inversion possible
    bad = _FakeLLM('{"action":"long","direction":"long_spread","size_usd":"2x",'
                   '"leverage":"heaps","entry":{},"exit":{},"confidence":0.5,"rationale":"r"}')
    d, m = decide_with_meta(_sig(z=-2.4), {}, RISK, DEFAULT_LIMITS, bad)   # must not raise
    assert d["action"] in {"long", "short", "neutral"}
    return True


def test_calibration_prefers_sim_p_denominator():
    """Review finding: realized ÷ stated over-shrinks (denominator mismatch). With sim_p recorded
    on the closes, the sim's own prediction becomes the denominator."""
    closes = [{"entry_z": 2.2, "converged": i < 28, "divergence": -0.002, "sim_p": 0.58}
              for i in range(64)]
    c = compute_calibration(closes)
    assert c["basis"] == "sim_p" and abs(c["denom_mean"] - 0.58) < 1e-9
    raw = (28 / 64) / 0.58
    expect = (64 * raw + 30) / 94
    assert abs(c["conv_scale"] - min(max(expect, 0.3), 1.0)) < 1e-3
    legacy = compute_calibration(_closes(64, 0.44))            # no sim_p -> stated fallback
    assert legacy["basis"] == "stated_confidence"
    assert c["conv_scale"] > legacy["conv_scale"]              # correct denominator shrinks LESS
    return True


def test_calibration_scope_and_freeze():
    """Review findings: the scale is measured on stat_arb_pair only -> other signal types stay
    uncalibrated; freeze() pins one calibration across a long computation."""
    p = _tmp("calib.json")
    write_calibration({**compute_calibration(_closes(400, 0.20)), "conv_scale": 0.3}, p)
    C.CALIB_PATH = p
    sim = PerpPaperSim(100_000)
    dec = {"action": "long", "direction": "long_spread", "size_usd": 5000.0, "leverage": 2.0}
    other = [sim.execute(Signal.from_dict({**_sig(f"F{i}").__dict__, "type": "funding_arb"}), dec)
             for i in range(200)]
    assert all(not f.calib_version for f in other)             # not the measured population
    d = decide(Signal.from_dict({**_sig().__dict__, "type": "funding_arb"}), RISK, DEFAULT_LIMITS)
    assert "calib_version" not in d                            # sizing untouched off-scope too
    C.freeze(None)                                             # pin "no calibration"
    try:
        assert load_calibration() is None
        d2 = decide(_sig(), RISK, DEFAULT_LIMITS)
        assert "calib_version" not in d2
    finally:
        C.unfreeze()
    assert load_calibration() is not None                      # unfrozen -> file visible again
    _no_calib()
    return True


def test_registry_refuses_corrupt_overwrite_and_apply_whitelist():
    """Review findings: a corrupt registry must not be silently destroyed by register_variant;
    apply_pending must only apply whitelisted params even on human approve."""
    P.VARIANTS_PATH = _tmp("variants.json")
    P.register_variant("keeper", "first challenger {max_leverage}")
    P.VARIANTS_PATH.write_text("{corrupt json!!")
    try:
        P.register_variant("clobber", "second")
        assert False, "must refuse to overwrite an unreadable registry"
    except ValueError:
        pass
    import evolver.graph.runtime as rt
    rt.STATE_PATH = _tmp("state.json")
    rt.register_pending("t1", {"proposals": [
        {"param": "min_confidence", "from": 0.7, "to": 0.75},
        {"param": "capital", "from": 100000, "to": 10_000_000},          # smuggled hard limit
        {"param": "analyst_prompt_variant", "from": "v1", "to": "v2-x"},  # allowed selector
    ], "version": "v2"})
    rt.apply_pending("t1", approved=True)
    strat = rt.load_state()[2]
    assert strat["min_confidence"] == 0.75 and strat["analyst_prompt_variant"] == "v2-x"
    assert "capital" not in strat                               # the smuggle did NOT apply
    return True


def test_decisions_ledger_and_critic_context():
    p = _tmp("decisions.jsonl")
    d, m = decide_with_meta(_sig(), {}, RISK, DEFAULT_LIMITS, None)
    record_decision("shadow", {"signal_id": "T1", "zscore": 2.4}, d, m, path=p)
    import json
    row = json.loads(p.read_text().splitlines()[0])
    assert row["service"] == "shadow" and row["source"] == "deterministic"
    assert row["decision"]["rationale"] and row["signal"]["signal_id"] == "T1"
    # critic sees measured calibration in context; deterministic fallback still behaves
    out = reflect({"win_rate": 0.4, "rv_convergence_accuracy": 0.44,
                   "measured_calibration": {"conv_scale": 0.62}}, [], dict(DEFAULT_STRATEGY))
    assert out["proposals"] and all(p_["param"] in ("min_confidence", "min_abs_zscore")
                                    for p_ in out["proposals"])
    return True
