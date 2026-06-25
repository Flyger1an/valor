"""Small-sample time-series tests (pure stdlib). Mirrors Valor TS math.ts."""
from __future__ import annotations

import math
import random
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
    lags: int
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


def _matinv(a: list[list[float]]) -> list[list[float]] | None:
    """Invert a small square matrix via Gauss-Jordan with partial pivoting. None if singular."""
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        m[col] = [x / pv for x in m[col]]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col]
                m[r] = [x - f * m[col][k] for k, x in enumerate(m[r])]
    return [row[n:] for row in m]


def _ols(X: list[list[float]], y: list[float]):
    """OLS via normal equations. Returns (beta, sse, xtx_inv) or (None, None, None) if singular."""
    n, k = len(X), len(X[0])
    xtx = [[sum(X[r][i] * X[r][j] for r in range(n)) for j in range(k)] for i in range(k)]
    xty = [sum(X[r][i] * y[r] for r in range(n)) for i in range(k)]
    inv = _matinv(xtx)
    if inv is None:
        return None, None, None
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    sse = 0.0
    for r in range(n):
        pred = sum(beta[i] * X[r][i] for i in range(k))
        sse += (y[r] - pred) ** 2
    return beta, sse, inv


def augmented_dickey_fuller_test(
    series: list[float],
    significance: Significance = 0.05,
    max_lag: int | None = None,
) -> AugmentedDickeyFullerResult:
    """ADF with constant, no trend: Δy_t = α + β·y_{t-1} + Σ_{i=1..p} γ_i·Δy_{t-i} + ε_t.

    The lagged-difference terms (the *augmentation*) absorb serial correlation in ε so that the
    β/SE(β) statistic actually follows the Dickey-Fuller distribution on autocorrelated data. WITHOUT
    them (a plain DF regression) the statistic is biased and OVER-rejects — it falsely declares
    autocorrelated series 'stationary', which is exactly the failure mode on hourly crypto ratios.
    Lag order p is chosen by BIC over 0..max_lag on a common sub-sample. test_stat below the DF
    critical value => reject a unit root => stationary.
    """
    N = len(series)
    if N < ADF_MIN_OBS:
        return AugmentedDickeyFullerResult(0.0, 0.0, 0, max(0, N - 1), "none", False)
    dy = [series[t] - series[t - 1] for t in range(1, N)]      # dy[i] uses series[i+1]-series[i]
    ylag = [series[t - 1] for t in range(1, N)]                # aligned with dy
    nd = len(dy)
    if max_lag is None:
        max_lag = int(12 * (nd / 100.0) ** 0.25)               # Schwert rule of thumb
    max_lag = max(0, min(max_lag, (nd - 5) // 3))              # cap to keep degrees of freedom
    start = max_lag                                            # common sub-sample across all p
    if nd - start < ADF_MIN_OBS:
        start, max_lag = 0, 0
    m = nd - start
    best = None
    for p in range(0, max_lag + 1):
        X = [[1.0, ylag[i]] + [dy[i - j] for j in range(1, p + 1)] for i in range(start, nd)]
        Y = [dy[i] for i in range(start, nd)]
        beta, sse, inv = _ols(X, Y)
        if beta is None or sse <= 0:
            continue
        k = p + 2
        bic = m * math.log(sse / m) + k * math.log(m)         # select lag order by BIC
        if best is None or bic < best[0]:
            best = (bic, p, beta, sse, inv, k)
    if best is None:
        return AugmentedDickeyFullerResult(0.0, 0.0, 0, m, "none", False)
    _, p, beta, sse, inv, k = best
    sigma2 = sse / max(1, m - k)
    var_beta = sigma2 * inv[1][1]                              # index 1 == y_{t-1} coefficient
    if var_beta <= 0:
        return AugmentedDickeyFullerResult(0.0, round(beta[1], 6), p, m, "none", False)
    test_stat = round(beta[1] / var_beta ** 0.5, 4)
    critical = _adf_critical(m, significance)
    is_stationary = test_stat < critical
    conf: AdfConfidence = (("99%" if significance == 0.01 else "95%" if significance == 0.05
                            else "90%") if is_stationary else "none")
    return AugmentedDickeyFullerResult(test_stat, round(beta[1], 6), p, m, conf, is_stationary)


def _sharpe_local(rets: list[float]) -> float:
    if len(rets) < 2:
        return 0.0
    mu = sum(rets) / len(rets)
    sd = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
    return mu / sd if sd > 0 else 0.0


def block_bootstrap_pvalue(returns: list[float], n_boot: int = 2000,
                           expected_block: float | None = None, seed: int = 7) -> float:
    """Stationary (Politis–Romano) bootstrap estimate of P(Sharpe <= 0), PRESERVING autocorrelation.

    Resamples geometric-length blocks (mean length `expected_block`) with wrap-around, so serial
    dependence in the return stream survives the resampling. An IID bootstrap (sampling single
    returns) destroys that dependence and understates the Sharpe's sampling variance for
    autocorrelated returns — making edges look more significant than they are. Drop-in replacement
    for the old per-trade `boot_p`: returns the fraction of resampled Sharpes that are <= 0.
    """
    n = len(returns)
    if n < 4:
        return 1.0
    if expected_block is None:
        expected_block = max(2.0, n ** (1.0 / 3.0))           # block length grows slowly with n
    p_geom = 1.0 / expected_block
    rng = random.Random(seed)
    le = 0
    for _ in range(n_boot):
        sample = []
        while len(sample) < n:
            k = rng.randrange(n)
            sample.append(returns[k])
            while len(sample) < n and rng.random() > p_geom:  # extend the block
                k = (k + 1) % n
                sample.append(returns[k])
        if _sharpe_local(sample) <= 0:
            le += 1
    return le / n_boot