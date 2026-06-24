"""Signal memory — recall analogous past signals + their realized outcomes to give
the Analyst context ("last 5 times we saw a cointegration_spread at |z|>2 in
low_vol, convergence accuracy was 0.8").

MVP: in-process cosine over a tiny feature vector. PROD: pgvector (you already run
Postgres) or Chroma. Embeddings can be a cheap feature vector (no LLM needed):
[zscore, risk_score, confidence, expected_convergence_hours, type_onehot...].
"""
from __future__ import annotations

import math

from evolver.core.signal import Signal

_STORE: list[tuple[list[float], dict]] = []  # (vector, record)


def _vec(sig: Signal) -> list[float]:
    return [sig.zscore, sig.risk_score, sig.confidence,
            math.log1p(sig.expected_convergence_hours)]


def add(sig: Signal, outcome: dict) -> None:
    _STORE.append((_vec(sig), {"signal_id": sig.signal_id, "type": sig.type, **outcome}))


def recall(sig: Signal, k: int = 5) -> list[dict]:
    if not _STORE:
        return []
    q = _vec(sig)

    def cos(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1e-9
        nb = math.sqrt(sum(y * y for y in b)) or 1e-9
        return dot / (na * nb)

    ranked = sorted(_STORE, key=lambda vr: cos(q, vr[0]), reverse=True)
    return [r for _v, r in ranked[:k]]
