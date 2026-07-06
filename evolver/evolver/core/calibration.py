"""Measured-reality calibration — closes the measurement→model loop.

The shadow-analyst book (scripts/shadow_analyst.py) marks the analyst's real decisions against
LIVE forward prices. This module distils that book into a small calibration document that the
rest of the system CONSUMES, so the fill simulator and position sizing are grounded in measured
reality instead of optimistic priors (first measurement, 2026-07: sim said +10.1%, reality said
−3.07%; stated confidence ~0.75 realized 44% convergence).

Honesty rules (load-bearing):
  * SINGLE WRITER: only the shadow-analyst tick writes the calibration file. Everyone else reads.
  * Learn at the rate information arrives: empirical-Bayes shrinkage toward the uncalibrated
    prior (scale 1.0) with `prior_weight` pseudo-observations, and NO calibration at all below
    `min_n` closed trades — a handful of trades must not steer the system.
  * HUMILITY ONLY: conv_scale is clamped to ≤ 1.0 — measured reality may shrink the system's
    self-assessment, never inflate it. The floor (0.3) stops a bad early sample from silencing
    everything.
  * Staleness guard: a calibration older than `max_age_days` is ignored (if the shadow stops
    writing, consumers revert to raw behavior rather than trusting a fossil).

The denominator matters (review finding): the honest multiplicative fix for the sim's p_converge
is realized ÷ the SIM'S OWN prediction — so new shadow records carry `sim_p` (the sim's
uncalibrated prior at decision time) and compute_calibration PREFERS that basis once enough rows
carry it. Until then it falls back to the z-derived stated confidence, which over-shrinks
somewhat (conservative direction, documented, self-resolving as sim_p rows accrue). The residual
divergence stays measured by the shadow book every tick; a residual on the sim_p basis IS
absorbed on recomputation.

Scope: the measurement population is the shadow's stat_arb_pair book, so consumers apply the
scale ONLY to CALIBRATED_TYPES — other signal types stay uncalibrated until they are measured.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

_ROOT = pathlib.Path(__file__).resolve().parents[2]   # the evolver/ dir (CWD-independent: a host
# cron writer and a container reader must not silently resolve different relative paths)
CALIB_PATH = pathlib.Path(os.getenv("EVOLVER_SIM_CALIB", str(_ROOT / ".evolver/sim_calibration.json")))
MIN_N = 40            # below this many closed shadow trades, no calibration is emitted
PRIOR_WEIGHT = 30     # pseudo-observations of "the priors are right" (shrinkage strength)
SCALE_FLOOR, SCALE_CEIL = 0.3, 1.0   # humility-only band
# hourly writer -> a doc older than this means the writer is dead or the book was reset;
# consumers revert to raw priors rather than obeying a fossil (was 14d: ~300x the cadence)
MAX_AGE_DAYS = 2.0
CALIBRATED_TYPES = {"stat_arb_pair"}   # the population the shadow book actually measures

_cache: dict = {}     # path -> (mtime, doc)
_frozen: list = []    # freeze() stack — long computations pin one calibration for comparability


def stated_confidence(entry_z: float) -> float:
    """The signal confidence implied by |entry z| — same formula the shadow's signal builder uses
    (shadow closes don't persist the decision's confidence, but it's reconstructable from z)."""
    return min(max(abs(entry_z) / 3.0, 0.1), 0.95)


def compute_calibration(closes: list, min_n: int = MIN_N, prior_weight: int = PRIOR_WEIGHT) -> dict | None:
    """Distil closed shadow trades -> calibration doc, or None when the sample is too thin.
    closes: shadow-analyst close records ({entry_z, converged, divergence, sim_p?, ...}).
    Basis preference: rows carrying sim_p (the sim's own prediction at decision time) give the
    correct denominator for scaling p_converge; the z-derived stated confidence is the fallback
    for legacy rows (conservative — it over-shrinks slightly)."""
    sim_rows = [c for c in closes
                if isinstance(c.get("sim_p"), (int, float)) and c.get("sim_p") > 0]
    if len(sim_rows) >= min_n:
        rows, basis = sim_rows, "sim_p"
        denom = sum(c["sim_p"] for c in rows) / len(rows)
    else:
        rows = [c for c in closes if isinstance(c.get("entry_z"), (int, float))]
        if len(rows) < min_n:
            return None
        basis = "stated_confidence"
        denom = sum(stated_confidence(c["entry_z"]) for c in rows) / len(rows)
    n = len(rows)
    if denom <= 0:
        return None
    realized = sum(1 for c in rows if c.get("converged")) / n
    raw = realized / denom
    shrunk = (n * raw + prior_weight * 1.0) / (n + prior_weight)     # shrink toward "priors right"
    scale = min(max(shrunk, SCALE_FLOOR), SCALE_CEIL)                # humility-only
    divs = [c["divergence"] for c in rows if isinstance(c.get("divergence"), (int, float))]
    return {
        "conv_scale": round(scale, 4),
        "n": n,
        "basis": basis,
        "denom_mean": round(denom, 4),
        "realized_conv_rate": round(realized, 4),
        "stated_conf_mean": round(denom, 4) if basis == "stated_confidence" else None,
        "mean_divergence_pct": round(sum(divs) / len(divs) * 100, 4) if divs else None,
        "updated_epoch": int(time.time()),
        "version": f"calib-{int(time.time())}-n{n}",
        "source": "shadow_analyst",
    }


def write_calibration(calib: dict, path: pathlib.Path | None = None) -> None:
    """Atomic write (tmp + replace). ONLY the shadow-analyst tick should call this."""
    p = path or CALIB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(calib))
    os.replace(tmp, p)


def freeze(calib: dict | None = "__current__") -> None:
    """Pin one calibration for the duration of a long computation (e.g. an Optuna study) so all
    its trials are scored under the SAME calibration even if the shadow writer lands mid-run.
    Process-local. Pair with unfreeze() in a finally block."""
    _frozen.append(load_calibration() if calib == "__current__" else calib)


def unfreeze() -> None:
    if _frozen:
        _frozen.pop()


def load_calibration(path: pathlib.Path | None = None, max_age_days: float = MAX_AGE_DAYS) -> dict | None:
    """Cached (by mtime) read; None when absent, unparsable, thin, or stale — callers then use
    their uncalibrated behavior. Never raises. Returns the pinned doc while freeze() is active."""
    if _frozen:
        return _frozen[-1]
    p = path or CALIB_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    key = str(p)
    if key in _cache and _cache[key][0] == mtime:
        doc = _cache[key][1]
    else:
        try:
            doc = json.loads(p.read_text())
        except Exception:
            return None
        _cache[key] = (mtime, doc)
    if not isinstance(doc, dict) or doc.get("n", 0) < MIN_N:
        return None
    if time.time() - doc.get("updated_epoch", 0) > max_age_days * 86400:
        return None
    return doc


def conv_scale(calib: dict | None) -> float:
    """The one number consumers apply. 1.0 (no-op) without a valid calibration."""
    if not calib:
        return 1.0
    return float(min(max(calib.get("conv_scale", 1.0), SCALE_FLOOR), SCALE_CEIL))
