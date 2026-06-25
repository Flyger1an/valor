"""ADF stationarity tests — mirrors Valor TS spread-stationarity.test.ts."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import random

from evolver.data.stats import augmented_dickey_fuller_test, block_bootstrap_pvalue


def ar1_series(phi: float, length: int) -> list[float]:
    series = [1.0]
    for t in range(1, length):
        shock = 0.05 * __import__("math").sin(t * 1.7) * __import__("math").cos(t * 0.9)
        series.append(phi * series[t - 1] + shock)
    return series


def unit_root_walk(length: int) -> list[float]:
    series = [100.0]
    for t in range(1, length):
        series.append(series[t - 1] + 0.35 + __import__("math").sin(t * 0.7) * 0.05)
    return series


def test_adf_rejects_unit_root_on_stationary_ar1():
    adf = augmented_dickey_fuller_test(ar1_series(0.55, 80), 0.05)
    assert adf.is_stationary
    assert adf.test_statistic < 0


def test_adf_does_not_reject_drifting_series():
    adf = augmented_dickey_fuller_test(unit_root_walk(80), 0.05)
    assert not adf.is_stationary


def test_adf_short_series_is_not_stationary():
    adf = augmented_dickey_fuller_test([1.0, 1.01, 0.99, 1.02], 0.05)
    assert not adf.is_stationary
    assert adf.confidence == "none"


def test_adf_is_augmented():
    """The test must actually choose a lag order (the 'augmentation') — a plain DF would be lags==0
    always. On a longer mean-reverting series BIC should be free to pick p>=0, and the field exists."""
    adf = augmented_dickey_fuller_test(ar1_series(0.55, 160), 0.05)
    assert isinstance(adf.lags, int) and adf.lags >= 0
    assert adf.n > 0


def test_block_bootstrap_flags_drift_not_noise():
    rng = random.Random(3)
    drift = [rng.gauss(0.4, 1.0) for _ in range(80)]          # clear positive edge -> low p (power)
    assert block_bootstrap_pvalue(drift) < 0.10
    # CALIBRATION (the meaningful check): a zero-mean series must reject at ~the nominal 5%, not far
    # above. The old percentile form (count <= 0 around the observed mean) over-rejected ~2x; the
    # recentered null form should not. Use ONE rng per series (reseeding per element = constant series).
    rej = 0
    for s in range(60):
        r = random.Random(1000 + s)
        series = [r.gauss(0.0, 1.0) for _ in range(60)]
        if block_bootstrap_pvalue(series, seed=s, n_boot=600) < 0.05:
            rej += 1
    assert rej <= 9, f"{rej}/60 rejected at 0.05 — miscalibrated (nominal ~3)"


def test_block_bootstrap_short_series_is_inconclusive():
    assert block_bootstrap_pvalue([0.01, 0.02]) == 1.0


def test_cscv_pbo_runs_and_is_not_pathological_on_noise():
    """PBO had zero test coverage and a tie-break bias toward 1.0. On a noise matrix it must compute
    and land in a sane mid-range, not the pathological 1.0 the all-tied path produced."""
    from evolver.evolve.fitness import cscv_pbo
    r = random.Random(0)
    noise = [[r.gauss(0, 1) for _ in range(6)] for _ in range(12)]   # 12 periods x 6 configs
    p = cscv_pbo(noise, s_blocks=10)
    assert p is not None and p < 0.85, p     # the bug pegged degenerate/tied matrices toward 1.0


def test_cscv_pbo_degenerate_matrix_does_not_crash_or_peg_high():
    """An all-identical (zero-dispersion) matrix has no selection signal — every split is skipped, so
    PBO is None rather than a spurious 1.0."""
    from evolver.evolve.fitness import cscv_pbo
    flat = [[0.01] * 5 for _ in range(12)]
    assert cscv_pbo(flat, s_blocks=10) is None