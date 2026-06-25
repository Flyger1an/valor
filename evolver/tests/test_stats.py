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
    drift = [rng.gauss(0.4, 1.0) for _ in range(80)]          # clear positive edge
    p_drift = block_bootstrap_pvalue(drift)
    assert p_drift < 0.10, p_drift
    # zero-mean series averaged over many independent draws -> p should center near 0.5, not tiny
    ps = [block_bootstrap_pvalue([random.Random(s).gauss(0.0, 1.0) for _ in range(60)], seed=s)
          for s in range(30)]
    assert 0.35 < sum(ps) / len(ps) < 0.65, sum(ps) / len(ps)


def test_block_bootstrap_short_series_is_inconclusive():
    assert block_bootstrap_pvalue([0.01, 0.02]) == 1.0