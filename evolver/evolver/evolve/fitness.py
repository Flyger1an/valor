"""Overfitting-resistant fitness — the heart of the engine.

An evolutionary search is a multiple-testing machine: run it against raw backtest Sharpe
and it WILL find spectacular fake edges (it reward-hacks by construction). So fitness here
is deliberately hostile to overfitting:

  * walk-forward OOS      — score on folds the params don't see; reward consistency
  * Deflated Sharpe Ratio — Bailey & López de Prado: haircut for the number of trials and
                            non-normality; DSR = P(true Sharpe > 0) after the haircut
  * recency gate          — is the edge ALIVE in the most recent fold? (tonight's lesson:
                            the cross-venue edge was real for 18mo yet dead in the last 3)
  * CSCV / PBO            — Probability of Backtest Overfitting, computed across the whole
                            population (see cscv_pbo); the search's self-diagnostic

backtest(params, lo, hi) must return a list of (ts_ms, return_fraction) trades.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

EULER = 0.5772156649


def sharpe(rets):
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    sd = (sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _nppf(p):
    """Inverse normal CDF (Acklam's rational approximation)."""
    if p <= 0:
        return -1e9
    if p >= 1:
        return 1e9
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _moments(rets):
    n = len(rets)
    if n < 3:
        return 0.0, 3.0
    m = sum(rets) / n
    sd = (sum((r - m) ** 2 for r in rets) / n) ** 0.5
    if sd == 0:
        return 0.0, 3.0
    skew = sum(((r - m) / sd) ** 3 for r in rets) / n
    kurt = sum(((r - m) / sd) ** 4 for r in rets) / n
    return skew, kurt


def deflated_sharpe(observed_sr, n_trades, n_trials, var_trials_sr, skew, kurt):
    """DSR = P(true Sharpe > 0) after haircutting for selection across n_trials.

    SR0 (the haircut) = expected max Sharpe of n_trials zero-edge strategies, via the
    'false strategy theorem'. Then deflate the observed SR by SR0 with a non-normality
    correction. Returns a probability in [0,1]; promotion wants > ~0.95.
    """
    if n_trades < 3 or n_trials < 1:
        return 0.0
    sd_trials = math.sqrt(max(var_trials_sr, 1e-9))
    nt = max(n_trials, 2)
    e_max = (1 - EULER) * _nppf(1 - 1.0 / nt) + EULER * _nppf(1 - 1.0 / (nt * math.e))
    sr0 = sd_trials * e_max                                  # expected best-of-n under no edge
    denom = math.sqrt(max(1 - skew * observed_sr + ((kurt - 1) / 4) * observed_sr ** 2, 1e-9))
    z = (observed_sr - sr0) * math.sqrt(n_trades - 1) / denom
    return _ncdf(z)


def cscv_pbo(matrix, s_blocks=10):
    """Probability of Backtest Overfitting via Combinatorially Symmetric Cross-Validation.

    matrix: rows = aligned time periods, cols = strategy configs. Splits periods into
    S blocks; for every S/2-train / S/2-test split, takes the in-sample-best config and
    asks where it ranks out-of-sample. PBO = P(IS-best lands below the OOS median).
    """
    from itertools import combinations
    rows, cols = len(matrix), len(matrix[0]) if matrix else 0
    if cols < 2 or rows < s_blocks or s_blocks % 2:
        return None
    bsz = rows // s_blocks
    blocks = [list(range(b * bsz, (b + 1) * bsz)) for b in range(s_blocks)]
    idx = set(range(s_blocks))
    below = tot = 0
    for train in combinations(range(s_blocks), s_blocks // 2):
        test = [b for b in idx if b not in train]
        tr_rows = [r for b in train for r in blocks[b]]
        te_rows = [r for b in test for r in blocks[b]]
        is_sr = [sharpe([matrix[r][c] for r in tr_rows]) for c in range(cols)]
        oos_sr = [sharpe([matrix[r][c] for r in te_rows]) for c in range(cols)]
        best = max(range(cols), key=lambda c: is_sr[c])
        rank = sorted(range(cols), key=lambda c: oos_sr[c]).index(best)  # 0 = worst OOS
        w = (rank + 1) / (cols + 1)
        if math.log(w / (1 - w)) <= 0:    # IS-best is at/below OOS median
            below += 1
        tot += 1
    return below / tot if tot else None


def _median(xs):
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _folds(trades, k):
    if not trades:
        return []
    trades = sorted(trades, key=lambda t: t[0])
    ts = [t[0] for t in trades]
    lo, hi = ts[0], ts[-1] + 1
    edges = [lo + (hi - lo) * i // k for i in range(k + 1)]
    out = []
    for i in range(k):
        seg = [r for (t, r) in trades if edges[i] <= t < edges[i + 1]]
        out.append(seg)
    return out


def monthly_vector(trades, month_keys):
    """Aggregate (ts,ret) trades into a return per calendar month (≈30d bucket)."""
    buckets = {}
    for t, r in trades:
        mk = t // (30 * 86_400_000)
        buckets.setdefault(mk, []).append(r)
    return [sum(buckets.get(mk, [0.0])) for mk in month_keys]


def evaluate(params, backtest, k_folds=5, n_trials=1, var_trials_sr=0.25):
    """Score one genome. Returns a Scorecard (fitness + the honest gate fields)."""
    trades = backtest(params, None, None)            # full-period (ts, ret) list
    n = len(trades)
    rets = [r for _, r in trades]
    full_sr = sharpe(rets)
    folds = _folds(trades, k_folds)                  # each fold = list of return floats
    fold_srs = [sharpe(f) for f in folds if len(f) >= 2]
    # MEDIAN fold Sharpe, not mean — one freak regime window (e.g. a small-cap relief bounce)
    # must not inflate the score and make the search chase a survivorship/regime artifact.
    oos_sr = _median(fold_srs)
    consistency = sum(1 for s in fold_srs if s > 0) / len(fold_srs) if fold_srs else 0.0
    recent_sr = sharpe(folds[-1]) if folds and len(folds[-1]) >= 2 else 0.0
    skew, kurt = _moments(rets)
    dsr = deflated_sharpe(full_sr, n, n_trials, var_trials_sr, skew, kurt)
    # fitness rewards CONSISTENT OOS edge, not peak in-sample Sharpe
    fitness = oos_sr * consistency - 0.5 * (max(0.0, full_sr - oos_sr))
    vol = (sum((r - sum(rets)/n) ** 2 for r in rets) / n) ** 0.5 if n else 0.0
    behavior = (min(n // 25, 7), min(int(vol * 200), 7))   # (turnover bin, risk bin)
    return SimpleNamespace(
        params=dict(params), n_trades=n, full_sharpe=round(full_sr, 3),
        oos_sharpe=round(oos_sr, 3), consistency=round(consistency, 2),
        recent_sharpe=round(recent_sr, 3), dsr=round(dsr, 3), fitness=round(fitness, 4),
        fold_sharpes=[round(s, 2) for s in fold_srs], behavior=behavior, trades=trades)
