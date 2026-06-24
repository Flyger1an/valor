"""ADF stationarity tests — mirrors Valor TS spread-stationarity.test.ts."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from evolver.data.stats import augmented_dickey_fuller_test


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