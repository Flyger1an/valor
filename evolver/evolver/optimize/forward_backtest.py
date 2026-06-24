"""Honest forward-outcome backtest engine — parameterized for OOS optimization.

Resolves each RV trade against REAL future prices (did the spread actually revert?).
The optimizer MUST use this, not the live PerpPaperSim (which estimates at decision
time and would let Optuna overfit to sim-optimism). Sizing uses the real
AdaptiveRiskManager; outcome uses real prices. Date bounds enable train/valid splits.
"""
from __future__ import annotations

from types import SimpleNamespace

from evolver.config import RiskLimits, DEFAULT_LIMITS
from evolver.core.kpis import compute_kpis
from evolver.core.risk import AdaptiveRiskManager, PaperResult
from evolver.data.okx import mean, std

FEE_BPS = 5.0  # OKX taker, per leg
DEFAULT_PARAMS = {"window": 30, "z_entry": 1.0, "z_exit": 0.5, "max_hold_days": 14,
                  "vol_filter_max": None}


def _trades_for_pair(ratio, ts, params, lo, hi):
    W = int(params["window"])
    ze, zx, mh = params["z_entry"], params["z_exit"], int(params["max_hold_days"])
    vfm = params.get("vol_filter_max")
    out, i = [], W
    while i < len(ratio) - 1:
        # only OPEN within the [lo, hi) date window (train/valid split)
        if (lo is not None and ts[i] < lo) or (hi is not None and ts[i] >= hi):
            i += 1
            continue
        window = ratio[i - W:i]
        sd = std(window)
        if sd == 0:
            i += 1
            continue
        rets = [window[k] / window[k - 1] - 1 for k in range(1, len(window))]
        vol = std(rets) if rets else 0.02
        if vfm is not None and vol > vfm:  # skip high-vol windows
            i += 1
            continue
        m, entry = mean(window), ratio[i]
        z = (entry - m) / sd
        if abs(z) < ze:
            i += 1
            continue
        sign = 1.0 if z < 0 else -1.0
        exit_j = min(i + mh, len(ratio) - 1)
        for j in range(i + 1, min(i + mh, len(ratio))):
            if abs((ratio[j] - m) / sd) <= zx:
                exit_j = j
                break
        gross = sign * (ratio[exit_j] / entry - 1.0)  # REAL forward move
        out.append({"ts": ts[i], "sign": sign, "gross": gross, "hold_h": (exit_j - i) * 24.0,
                    "converged": abs((ratio[exit_j] - m) / sd) <= zx, "vol": vol,
                    "risk_score": min(0.9, 0.2 + vol * 8)})
        i = exit_j  # non-overlapping per pair
    return out


def run_forward_backtest(closes_by_base, pairs, params=None,
                         limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None) -> dict:
    params = {**DEFAULT_PARAMS, **(params or {})}
    trades = []
    for a, b, _typ in pairs:
        ts = sorted(set(closes_by_base[a]) & set(closes_by_base[b]))
        ratio = [closes_by_base[a][t] / closes_by_base[b][t] for t in ts]
        trades += _trades_for_pair(ratio, ts, params, lo, hi)
    trades.sort(key=lambda x: x["ts"])  # portfolio time order

    rm = AdaptiveRiskManager(limits)
    fills = []
    for tr in trades:
        notional = limits.capital * rm.current_pos_pct * rm.current_leverage
        fees = notional * (2 * FEE_BPS / 1e4)
        net = notional * tr["gross"] - fees
        pnl_pct = net / limits.capital
        fills.append(SimpleNamespace(
            direction="long_spread" if tr["sign"] > 0 else "short_spread",
            net_pnl_usd=round(net, 2), pnl_pct=pnl_pct, cost_usd=round(fees, 2),
            funding_usd=0.0, hold_hours=tr["hold_h"], converged=tr["converged"]))
        rm.update_from_paper_trade(
            PaperResult("bt", pnl_pct, tr["hold_h"], tr["vol"], 0.0, tr["converged"]),
            regime=("high_vol" if tr["vol"] > 0.04 else "low_vol"), risk_score=tr["risk_score"])
    return {"fills": fills, "kpis": compute_kpis(fills, limits.capital),
            "equity": rm.equity, "returns": [f.pnl_pct for f in fills]}
