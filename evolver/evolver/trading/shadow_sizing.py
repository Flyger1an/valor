"""Deterministic, deliberately conservative research sizing. No broker or model clients.

Inputs are synchronized DAILY sleeve P&L / declared sleeve capital, collected forward
by the virtual baseline. They are not pooled trade returns or calibrated probabilities.
The uncertainty adjustment is a documented heuristic, not a confidence guarantee.
"""
from __future__ import annotations

import hashlib
import math
import statistics

from .contracts import encode


KELLY_RULES = {
    "version": "robust-quarter-kelly-v1",
    "minimum_blocks": 30,
    "minimum_active_blocks": 10,
    "maximum_blocks": 60,
    "mean_shrinkage": 0.5,
    "standard_error_penalty": 2.0,
    "fraction": 0.25,
    "grid_step": 0.05,
    "joint_shock_probability": 0.01,
    "joint_shock_return": -0.20,
    "fraction_order": "fractional raw new allocation, then existing policy caps",
}

# The estimator and all numerical risk assumptions are unchanged. Only the
# definition of an observable daily return changes, at an explicit journal event.
KELLY_RULES_V2 = {**KELLY_RULES, "version": "robust-quarter-kelly-v2",
    "evidence": "continuous observed policy; fresh held-inventory valuations at UTC boundaries; no price interpolation",
    "maximum_observation_gap_seconds": 60,
    "boundary_quote_age_seconds": 30,
    "cohort": "forward full UTC days after activation; never pool v1 or partial days"}

KELLY_RULES_V3 = {**KELLY_RULES_V2, "version": "robust-quarter-kelly-v3",
    "cohort": "synchronized full UTC days after this universe boundary; never fabricate new-asset returns",
    "optimizer": "deterministic joint 5% grid ascent with add/remove/exchange moves; bounded heuristic, not a global optimum guarantee",
    "maximum_optimizer_steps": 400,
    "selection": "lexical eligible symbol order; one joint recommendation per frame, then unchanged shared caps"}


def evidence_rules(version):
    return {r["version"]: r for r in (KELLY_RULES, KELLY_RULES_V2, KELLY_RULES_V3)}[version]


def usable_blocks(blocks, now, version, cohort=None):
    return [b for b in blocks if b["complete"] and b["settled"] and b["available_at"] <= now and b["end"] < now
            and b.get("evidence_version", KELLY_RULES["version"]) == version
            and (cohort is None or b.get("cohort") == cohort)][-evidence_rules(version)["maximum_blocks"]:]


def _score(weights, samples, rules=KELLY_RULES):
    values = [sum(w*r for w, r in zip(weights, row)) for row in samples]
    if any(v <= -1 or not math.isfinite(v) for v in values):
        return -math.inf
    tail = sum(weights)*rules["joint_shock_return"]
    return (1-rules["joint_shock_probability"])*statistics.mean(
        math.log1p(v) for v in values) + rules["joint_shock_probability"]*math.log1p(tail)


def recommend(blocks, symbols, eligible, held_weights, now, version=KELLY_RULES["version"], cohort=None):
    """Recommend *new* notional weights; held allocations are fixed, never resized.

    Settlement and availability gates prevent learning from as-yet unknown fees.
    A second, comonotonic scenario removes the benefit of an estimated low correlation.
    Legacy cohorts retain exact two-asset enumeration. V3 uses a bounded joint search.
    """
    dynamic = version == KELLY_RULES_V3["version"]
    if dynamic:
        if not 1 <= len(symbols) <= 64 or len(set(symbols)) != len(symbols) or not cohort:
            raise ValueError("dynamic Kelly requires a bounded unique universe and explicit cohort")
        symbols = sorted(symbols)
    elif len(symbols) != 2:
        raise ValueError("Kelly v1 supports exactly two synchronized assets")
    rules = evidence_rules(version)
    usable = usable_blocks(blocks, now, version, cohort)
    digest = hashlib.sha256(encode(usable).encode()).hexdigest()
    result = {"policy_version": rules["version"], "evidence_hash": digest,
              "blocks": len(usable), "evidence_cutoff": max((b["end"] for b in usable), default=None),
              "raw_fraction": dict.fromkeys(symbols, 0.0),
              "fractional_fraction": dict.fromkeys(symbols, 0.0),
              "reason": "insufficient_forward_evidence", "assumptions": rules}
    if len(usable) < rules["minimum_blocks"]:
        return result
    if any(set(b["pnl"]) != set(symbols) for b in usable):
        return {**result, "reason": "missing_synchronized_asset_evidence"}
    if any(float(b["reference_notional"]) <= 0 for b in usable):
        return {**result, "reason": "unusable_return_distribution"}
    columns = [[float(b["pnl"][s])/float(b["reference_notional"]) for b in usable] for s in symbols]
    if any(not math.isfinite(x) or x <= -1 for col in columns for x in col):
        return {**result, "reason": "unusable_return_distribution"}
    margins, lower, allowed = [], {}, []
    for s, col in zip(symbols, columns):
        mean = statistics.mean(col)
        # Positive lag-1 dependence increases uncertainty; negative estimates never reduce it.
        centered = [x-mean for x in col]
        variance = statistics.variance(col)
        lag = sum(a*b for a, b in zip(centered[:-1], centered[1:]))/len(col)
        se = math.sqrt(max(variance, variance+max(0.0, lag))/len(col))
        margin = (1-rules["mean_shrinkage"])*mean + rules["standard_error_penalty"]*se
        margins.append(margin)
        lower[s] = mean-margin
        active = sum(bool(b["active"].get(s)) for b in usable)
        allowed.append(s in eligible and active >= rules["minimum_active_blocks"] and lower[s] > 0)
    result["adjusted_mean"] = lower
    if not any(allowed):
        return {**result, "reason": "no_supported_positive_net_edge"}
    count = len(symbols)
    joint = [tuple(row[j]-margins[j] for j in range(count)) for row in zip(*columns)]
    comonotonic = list(zip(*(sorted(x-margins[j] for x in columns[j]) for j in range(count))))
    held = tuple(float(held_weights.get(s, 0)) for s in symbols)
    if any(not math.isfinite(w) or w < 0 for w in held) or sum(held) > 1:
        return {**result, "reason": "invalid_held_exposure"}
    objective = lambda w: min(_score(w, joint, rules), _score(w, comonotonic, rules))
    baseline, best, new = objective(held), objective(held), (0.0,)*count
    if dynamic:
        new, best = _joint_grid(objective, held, allowed, rules)
        fractional = tuple(w*rules["fraction"] for w in new)
        if objective(tuple(held[j]+fractional[j] for j in range(count))) <= baseline + 1e-12:
            return {**result, "reason": "cash_has_better_robust_growth"}
        return {**result, "reason": "positive_stress_adjusted_growth",
                "raw_fraction": dict(zip(symbols, new)), "fractional_fraction": dict(zip(symbols, fractional)),
                "raw_incremental_log_growth": best-baseline}
    choices = [range(21) if ok and held[j] == 0 else (0,) for j, ok in enumerate(allowed)]
    for a in choices[0]:
        for b in choices[1]:
            proposal = (a/20, b/20)
            weights = tuple(held[j]+proposal[j] for j in range(2))
            if sum(weights) > 1 + 1e-12:
                continue
            score = objective(weights)
            # Ties retain the smaller, lexicographically enumerated allocation.
            if score > best + 1e-12:
                best, new = score, proposal
    fractional = tuple(w*rules["fraction"] for w in new)
    if objective(tuple(held[j]+fractional[j] for j in range(2))) <= baseline + 1e-12:
        return {**result, "reason": "cash_has_better_robust_growth"}
    return {**result, "reason": "positive_stress_adjusted_growth",
            "raw_fraction": dict(zip(symbols, new)),
            "fractional_fraction": dict(zip(symbols, fractional)),
            "raw_incremental_log_growth": best-baseline}


def _joint_grid(objective, held, allowed, rules):
    """Optimize a single joint objective; held exposures are fixed and never sold here."""
    n, units = len(held), [0]*len(held)
    best = objective(held)
    destinations = [i for i in range(n) if allowed[i] and held[i] == 0]
    step = rules["grid_step"]
    for _ in range(rules["maximum_optimizer_steps"]):
        winner, score = None, best
        # None represents cash. Moves can add, remove, or exchange one grid unit.
        for source in [None]+[i for i in range(n) if units[i]]:
            for target in [None]+destinations:
                if source == target:
                    continue
                candidate = units.copy()
                if source is not None:
                    candidate[source] -= 1
                if target is not None:
                    candidate[target] += 1
                weights = tuple(held[i]+candidate[i]*step for i in range(n))
                if sum(weights) > 1+1e-12:
                    continue
                value = objective(weights)
                if value > score+1e-12:
                    winner, score = candidate, value
        if winner is None:
            break
        units, best = winner, score
    return tuple(u*step for u in units), best
