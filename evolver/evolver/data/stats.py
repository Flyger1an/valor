"""Small-sample time-series tests (pure stdlib). Mirrors Valor TS math.ts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ADF_MIN_OBS = 12
ADF_CRITICAL: list[tuple[int, float, float, float]] = [
    (25, -2.66, -3.0, -3.75),
    (50, -2.59, -2.93, -3.58),
    (100, -2.57, -2.89, -3.51),
    (250, -2.57, -2.88, -3.48),
]

AdfConfidence = Literal["90%", "95%", "99%", "none"]
Significance = Literal[0.1, 0.05, 0.01]


@dataclass(frozen=True)
class AugmentedDickeyFullerResult:
    test_statistic: float
    beta: float
    n: int
    confidence: AdfConfidence
    is_stationary: bool


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _adf_critical(sample_size: int, significance: Significance) -> float:
    col = {0.1: 1, 0.05: 2, 0.01: 3}[significance]
    n = max(sample_size, ADF_MIN_OBS)
    if n <= ADF_CRITICAL[0][0]:
        return ADF_CRITICAL[0][col]
    if n >= ADF_CRITICAL[-1][0]:
        return ADF_CRITICAL[-1][col]
    for i in range(len(ADF_CRITICAL) - 1):
        n0 = ADF_CRITICAL[i][0]
        n1, *rest = ADF_CRITICAL[i + 1]
        if n0 <= n <= n1:
            cv0 = ADF_CRITICAL[i][col]
            cv1 = rest[col - 1]
            t = (n - n0) / (n1 - n0)
            return cv0 + t * (cv1 - cv0)
    return ADF_CRITICAL[1][col]


def augmented_dickey_fuller_test(
    series: list[float],
    significance: Significance = 0.05,
) -> AugmentedDickeyFullerResult:
    """ADF with constant, no trend: Δy_t = α + β·y_{t-1} + ε_t."""
    if len(series) < ADF_MIN_OBS:
        return AugmentedDickeyFullerResult(0.0, 0.0, max(0, len(series) - 1), "none", False)

    y_lag = [series[t - 1] for t in range(1, len(series))]
    dy = [series[t] - series[t - 1] for t in range(1, len(series))]
    n = len(dy)
    y_mean = _mean(y_lag)
    dy_mean = _mean(dy)
    x_var = sum((y - y_mean) ** 2 for y in y_lag)
    if x_var == 0:
        return AugmentedDickeyFullerResult(0.0, 0.0, n, "none", False)

    cov = sum((y_lag[i] - y_mean) * (dy[i] - dy_mean) for i in range(n))
    beta = cov / x_var
    alpha = dy_mean - beta * y_mean
    sse = sum((dy[i] - alpha - beta * y_lag[i]) ** 2 for i in range(n))
    mse = sse / max(1, n - 2)
    se_beta = (mse / x_var) ** 0.5
    test_stat = 0.0 if se_beta == 0 else round(beta / se_beta, 4)
    critical = _adf_critical(n, significance)
    is_stationary = test_stat < critical
    confidence: AdfConfidence
    if is_stationary:
        confidence = "99%" if significance == 0.01 else "95%" if significance == 0.05 else "90%"
    else:
        confidence = "none"
    return AugmentedDickeyFullerResult(test_stat, beta, n, confidence, is_stationary)