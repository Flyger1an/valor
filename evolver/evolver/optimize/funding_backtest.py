"""Honest funding-carry backtest.

Delta-neutral: short perp + long spot to collect positive funding (flip for negative).
P&L per unit notional over the hold:
    funding_pnl = -side_perp * Σ funding(τ)          (real OKX 8h funding, daily-summed)
    price_pnl   =  side_perp * (perp_ret - spot_ret) (real basis move = the carry's risk)
    minus 4 taker legs (enter perp+spot, exit perp+spot).
Forward-resolved against real prices; date bounds enable train/valid OOS splits.

This tests whether funding carry survives fees + basis risk OUT OF SAMPLE — a more
durable RV edge than spread reversion, IF the regime offers funding above the fee drag.
"""
from __future__ import annotations

from types import SimpleNamespace

from evolver.config import RiskLimits, DEFAULT_LIMITS
from evolver.core.kpis import compute_kpis
from evolver.core.risk import AdaptiveRiskManager, PaperResult
from evolver.data.okx import daily_funding, okx_daily_closes, okx_funding_history

FEE_BPS = 5.0  # OKX taker, per leg (override per-run via params["fee_bps"]; maker ≈ 2)
DEFAULT_PARAMS = {"entry_ann_funding": 0.10, "exit_ann_funding": 0.03, "max_hold_days": 7,
                  "fee_bps": FEE_BPS, "legs": 4}


def load(bases) -> dict:
    """{base: (spot_closes, perp_closes, daily_funding)} — one network pull per base."""
    out = {}
    for b in bases:
        out[b] = (
            okx_daily_closes(b, 300),
            okx_daily_closes(b, 300, inst=f"{b}-USDT-SWAP"),
            daily_funding(okx_funding_history(f"{b}-USDT-SWAP", 300)),
        )
    return out


def _trades(base, data, params, lo, hi):
    spot, perp, fund = data[base]
    days = sorted(set(spot) & set(perp) & set(fund))
    out, i = [], 0
    while i < len(days) - 1:
        d = days[i]
        if (lo is not None and d < lo) or (hi is not None and d >= hi):
            i += 1
            continue
        ann = fund[d] * 365
        if abs(ann) < params["entry_ann_funding"]:
            i += 1
            continue
        side_perp = -1.0 if fund[d] > 0 else 1.0  # short perp to collect +funding
        exit_i = min(i + int(params["max_hold_days"]), len(days) - 1)
        for j in range(i + 1, min(i + int(params["max_hold_days"]), len(days))):
            if abs(fund[days[j]] * 365) < params["exit_ann_funding"]:
                exit_i = j
                break
        fsum = sum(fund[days[k]] for k in range(i + 1, exit_i + 1))
        funding_pnl = -side_perp * fsum
        perp_ret = perp[days[exit_i]] / perp[d] - 1
        spot_ret = spot[days[exit_i]] / spot[d] - 1
        price_pnl = side_perp * (perp_ret - spot_ret)
        out.append({
            "ts": d, "gross": funding_pnl + price_pnl, "hold_h": (exit_i - i) * 24.0,
            "converged": abs(fund[days[exit_i]] * 365) < params["exit_ann_funding"],
            "funding_frac": funding_pnl,
        })
        i = exit_i
    return out


def run_funding_backtest(data, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None) -> dict:
    params = {**DEFAULT_PARAMS, **(params or {})}
    trades = []
    for base in data:
        trades += _trades(base, data, params, lo, hi)
    trades.sort(key=lambda x: x["ts"])

    rm = AdaptiveRiskManager(limits)
    fills, funding_total = [], 0.0
    for tr in trades:
        notional = limits.capital * rm.current_pos_pct * rm.current_leverage
        fees = notional * (params["legs"] * params["fee_bps"] / 1e4)
        net = notional * tr["gross"] - fees
        pnl_pct = net / limits.capital
        funding_total += notional * tr["funding_frac"]
        fills.append(SimpleNamespace(
            direction="carry", net_pnl_usd=round(net, 2), pnl_pct=pnl_pct,
            cost_usd=round(fees, 2), funding_usd=round(notional * tr["funding_frac"], 2),
            hold_hours=tr["hold_h"], converged=tr["converged"]))
        rm.update_from_paper_trade(
            PaperResult("fc", pnl_pct, tr["hold_h"], 0.02, 0.0, tr["converged"]),
            regime="low_vol", risk_score=0.4)
    return {"fills": fills, "kpis": compute_kpis(fills, limits.capital),
            "equity": rm.equity, "returns": [f.pnl_pct for f in fills],
            "funding_collected_usd": round(funding_total, 2)}
