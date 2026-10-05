"""Bounded research with a frozen challenger and a forward-only evaluation interval.

Train selects a challenger. Further observations can extend its evaluation, but cannot change its
parameters. After adjudication, the evaluation watermark advances; used observations are never
relabelled as unseen evidence. Existing Evolver research remains independent of order authority.
"""
from __future__ import annotations

import hashlib
import math
import statistics

from .contracts import Policy, encode
from .strategies import BY_VERSION, backtest


def metrics(trades):
    ordered = sorted(trades)
    values = [r for _, r in ordered]
    gains, losses = sum(max(0, r) for r in values), -sum(min(0, r) for r in values)
    equity = peak = 1.0
    drawdown = 0.0
    for r in values:
        equity *= 1 + r
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak-equity)/peak)
    return {"trades": len(values), "net_return": equity-1,
            "profit_factor": gains/losses if losses else (100.0 if gains else 0.0),
            "drawdown": drawdown, "mean": statistics.mean(values) if values else 0,
            "sd": statistics.stdev(values) if len(values) > 1 else 0}


def evaluate(policy: Policy, histories: dict, state: dict, incumbent: str):
    specs = [BY_VERSION[v] for v in policy.approved_strategies if v in BY_VERSION]
    timestamps = sorted({b["timestamp"] for bars in histories.values() for b in bars})
    if len(timestamps) < 600:
        return {**state, "status": "collecting_data", "bars": len(timestamps)}
    # Freeze selection at the most recent CLOSED candle; only subsequently arriving bars
    # count as prospective evidence. The initial historical sample is training, not validation.
    cut = state.get("cutoff") or timestamps[-1] + 300
    challenger = state.get("challenger")
    if challenger not in policy.approved_strategies:
        training = []
        for spec in specs:
            rows = [trade for bars in histories.values() for trade in backtest(spec, bars, policy, end=cut)]
            score = metrics(rows)
            training.append((score["net_return"] if score["trades"] >= 10 else -math.inf, spec.version))
        best = max(training, default=(-math.inf, incumbent))
        if best[0] == -math.inf:
            return {"status": "insufficient_training_trades", "bars": len(timestamps)}
        challenger = best[1]
    def forward(version):
        return sorted(t for bars in histories.values() for t in backtest(BY_VERSION[version], bars, policy, start=cut))
    candidate_rows, base_rows = forward(challenger), forward(incumbent)
    candidate, base = metrics(candidate_rows), metrics(base_rows)
    result = {"status": "collecting_forward_evidence", "challenger": challenger, "incumbent": incumbent,
              "cutoff": cut, "end": timestamps[-1], "candidate": candidate, "baseline": base,
              "variants_tested": len(specs), "data_source": "market_bars",
              "return_basis": "screening returns on position notional, not account performance"}
    if candidate["trades"] < 30:
        return result
    # Conservative screening, not a claim of statistical proof or guaranteed returns.
    penalty = math.sqrt(2 * math.log(max(len(specs), 2))) * candidate["sd"] / math.sqrt(candidate["trades"])
    blocks = [candidate_rows[len(candidate_rows)*i//3:len(candidate_rows)*(i+1)//3] for i in range(3)]
    beats_baseline = candidate["net_return"] >= base["net_return"] if challenger == incumbent else candidate["net_return"] > base["net_return"]
    passed = (candidate["net_return"] > 0 and beats_baseline and candidate["profit_factor"] >= 1.2
              and candidate["mean"] > penalty and candidate["drawdown"] <= 0.10
              and all(sum(r for _, r in block) > 0 for block in blocks))
    result["status"] = "review_required" if passed else "rejected"
    result["evidence_hash"] = hashlib.sha256(encode({"policy": policy.fingerprint,
                                                  "result": result, "trades": candidate_rows}).encode()).hexdigest()
    return result
