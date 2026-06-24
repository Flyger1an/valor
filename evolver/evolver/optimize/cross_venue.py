"""Cross-venue funding-dislocation backtest (Binance perp vs Hyperliquid perp).

Same coin, two venues, different funding. Long the low-funding venue's perp + short
the high-funding venue's perp (same notional) => delta-neutral on the coin, capturing
the funding DIFFERENTIAL. The only residual risk is the inter-venue perp basis, which
is far smaller/stabler than the spot-perp basis that killed single-venue carry.

data: {coin: (bnb_fund_daily, bnb_perp_daily, hl_fund_daily, hl_perp_daily)}, all
keyed by utc_day_ms (funding = summed daily rate per venue).

CAVEAT: daily granularity understates the *intraday* inter-venue basis. This is a
SCREEN — if it fails on daily (fee-dominated), intraday won't save it; if it passes,
validate intraday next (as we did for single-venue).
"""
from __future__ import annotations

from types import SimpleNamespace

from evolver.config import RiskLimits, DEFAULT_LIMITS
from evolver.core.kpis import compute_kpis
from evolver.core.risk import AdaptiveRiskManager, PaperResult

DEFAULT_PARAMS = {"entry_ann_diff": 0.05, "exit_ann_diff": 0.01, "max_hold_days": 14,
                  "fee_bps": 2.0, "legs": 4}  # maker; 2 perp legs each side


def _coin_trades(bnb_f, bnb_p, hl_f, hl_p, params, lo, hi):
    days = sorted(set(bnb_f) & set(bnb_p) & set(hl_f) & set(hl_p))
    out, i, mh = [], 0, int(params["max_hold_days"])
    while i < len(days) - 1:
        d = days[i]
        if (lo is not None and d < lo) or (hi is not None and d >= hi):
            i += 1
            continue
        diff = hl_f[d] - bnb_f[d]
        if abs(diff * 365) < params["entry_ann_diff"]:
            i += 1
            continue
        side = 1.0 if diff > 0 else -1.0  # diff>0: short HL / long Binance to collect +diff
        exit_i = min(i + mh, len(days) - 1)
        for j in range(i + 1, min(i + mh, len(days))):
            if abs((hl_f[days[j]] - bnb_f[days[j]]) * 365) < params["exit_ann_diff"]:
                exit_i = j
                break
        fsum = sum(hl_f[days[k]] - bnb_f[days[k]] for k in range(i + 1, exit_i + 1))
        funding_pnl = side * fsum
        bnb_ret = bnb_p[days[exit_i]] / bnb_p[d] - 1
        hl_ret = hl_p[days[exit_i]] / hl_p[d] - 1
        price_pnl = side * (bnb_ret - hl_ret)   # long BNB/short HL when side>0
        out.append({"ts": d, "gross": funding_pnl + price_pnl, "hold_h": (exit_i - i) * 24,
                    "entry_ann": round(abs(diff * 365), 4),
                    "converged": abs((hl_f[days[exit_i]] - bnb_f[days[exit_i]]) * 365) < params["exit_ann_diff"],
                    "funding_frac": funding_pnl})
        i = exit_i
    return out


def run_cross_venue(data, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None) -> dict:
    params = {**DEFAULT_PARAMS, **(params or {})}
    trades = []
    for c in data:
        trades += _coin_trades(*data[c], params, lo, hi)
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
            direction="xfund", net_pnl_usd=round(net, 2), pnl_pct=pnl_pct, ts=tr["ts"],
            entry_ann=tr["entry_ann"], cost_usd=round(fees, 2),
            funding_usd=round(notional * tr["funding_frac"], 2),
            hold_hours=tr["hold_h"], converged=tr["converged"]))
        rm.update_from_paper_trade(
            PaperResult("xf", pnl_pct, tr["hold_h"], 0.01, 0.0, tr["converged"]),
            regime="low_vol", risk_score=0.3)
    return {"fills": fills, "kpis": compute_kpis(fills, limits.capital),
            "equity": rm.equity, "returns": [f.pnl_pct for f in fills],
            "funding_collected_usd": round(funding_total, 2)}
