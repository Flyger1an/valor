"""Single source of truth for the stat-arb pair signal.

This logic previously lived in TWO drifted copies: scripts/signal_feed.py `gen` (ADF-stationarity
gated) and scripts/shadow_analyst.py `_signal` (NOT gated) — so the shadow book that writes the sim
calibration was measuring a DIFFERENT signal population than the live loop trades (audited
2026-07-10). Both now build from here with the SAME stationarity gate, and the |z|/3 stated-
confidence formula lives here too (calibration.stated_confidence imports it), so the emitter, the
measurement instrument, and the calibration denominator can never drift apart again.

build_pair_signal returns (signal_dict_or_None, z_or_None):
  * z is returned whenever the data suffices — even when the signal is GATED — because the shadow
    book needs z for EXIT/convergence checks on already-open positions regardless of whether the
    pair still qualifies for entry.
  * the dict is None when data is short/degenerate or (require_stationary and not stationary).
"""
from __future__ import annotations

import datetime as dt

from evolver.data.okx import half_life_hours, mean, std
from evolver.data.stats import augmented_dickey_fuller_test

WINDOW = 168               # hours of ratio history the z-score/vol/ADF are computed over


def stated_confidence(z: float) -> float:
    """Signal confidence implied by |z| — THE formula (emitters, shadow, calibration all share it)."""
    return min(max(abs(z) / 3.0, 0.1), 0.95)


def build_pair_signal(a: str, b: str, closes: dict, window: int = WINDOW,
                      adf_significance: float = 0.05, require_stationary: bool = True):
    """Locked-contract stat_arb_pair signal dict from the A/B close-ratio. See module docstring."""
    ts = sorted(set(closes.get(a, {})) & set(closes.get(b, {})))
    if len(ts) < window + 2:
        return None, None
    ratio = [closes[a][t] / closes[b][t] for t in ts if closes[b][t]]
    win = ratio[-window:]
    m, sd = mean(win), std(win)
    if sd <= 0 or m <= 0:
        return None, None
    z = (ratio[-1] - m) / sd
    rr = [win[i] / win[i - 1] - 1 for i in range(1, len(win)) if win[i - 1]]
    vol = std(rr)
    adf = augmented_dickey_fuller_test(win, adf_significance)
    if require_stationary and not adf.is_stationary:
        return None, z                     # gated for entry; z still visible for exit checks
    return {
        "signal_id": f"{a}{b}-{ts[-1]}",
        "timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": "stat_arb_pair",
        "assets": [a, b],
        "zscore": round(z, 3),
        "spread_value": round(ratio[-1] / m - 1, 5),
        "expected_convergence_hours": round(half_life_hours(win, 1.0, 48.0), 1),
        "risk_score": round(min(max(vol * 25, 0.05), 0.95), 3),
        "confidence": round(stated_confidence(z), 3),
        "regime": "high_vol" if vol > 0.03 else "low_vol",
        "metadata": {
            "spread_stationary": adf.is_stationary,
            "adf_test_statistic": adf.test_statistic,
            "adf_confidence": adf.confidence,
        },
    }, z
