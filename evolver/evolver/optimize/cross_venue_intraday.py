"""Intraday inter-venue basis test — the decisive gate for cross-venue dislocation.

The daily screen validated the edge but hid the HOURLY inter-venue basis path. This is
the exact test that turned single-venue carry's +0.89 daily Sharpe into -0.30: walk the
position hour by hour, mark the inter-venue basis every hour, and see whether the
~+0.18/trade forward edge survives the noise + 4-leg execution.

Position (diff>0, HL funding > Binance): SHORT HL perp + LONG Binance perp. Delta-neutral;
the only risk is the HL-vs-Binance basis, which is structurally tiny (same coin) — but
"tiny" must be measured, not assumed.

data: {coin: (bnb_perp_1h{ts:close}, hl_perp_1h{ts:close}, daily_diff{utc_day_ms:diff})}
"""
from __future__ import annotations

from types import SimpleNamespace

from evolver.config import RiskLimits, DEFAULT_LIMITS
from evolver.core.kpis import compute_kpis
from evolver.core.risk import AdaptiveRiskManager, PaperResult

DEFAULT_PARAMS = {"entry_ann_diff": 0.10, "exit_ann_diff": 0.02, "max_hold_hours": 336,
                  "fee_bps": 2.0, "slip_bps": 3.0, "legs_per_side": 2}


def _day(t):
    return (t // 86_400_000) * 86_400_000


def _coin_fills(bnb, hl, dd, params, rm, limits):
    ts = sorted(set(bnb) & set(hl))
    if len(ts) < 48:
        return [], 0.0
    leg = params["legs_per_side"] * (params["fee_bps"] + params["slip_bps"]) / 1e4  # cost per side
    fills, worst_mae, i, mh = [], 0.0, 0, int(params["max_hold_hours"])
    while i < len(ts) - 1:
        t = ts[i]
        d = dd.get(_day(t))
        if d is None or abs(d * 365) < params["entry_ann_diff"]:
            i += 1
            continue
        side = 1.0 if d > 0 else -1.0          # diff>0: short HL / long Binance
        N = limits.capital * rm.current_pos_pct * rm.current_leverage
        be, he = bnb[t], hl[t]
        entry_cost = N * leg
        end = min(i + mh, len(ts) - 1)
        funding_acc, exit_i, mae, outcome = 0.0, end, 0.0, "hold"
        for k in range(i + 1, end + 1):
            tk = ts[k]
            dk = dd.get(_day(tk), d)
            funding_acc += dk / 24.0                                  # amortized daily diff per hour
            basis = (bnb[tk] / be - 1) - (hl[tk] / he - 1)            # long BNB / short HL price move
            mtm = side * basis + side * funding_acc                   # signed PnL fraction so far
            mae = min(mae, mtm)
            if abs(dk * 365) < params["exit_ann_diff"]:
                outcome, exit_i = "converged", k
                break
        worst_mae = min(worst_mae, mae)
        basis_f = (bnb[ts[exit_i]] / be - 1) - (hl[ts[exit_i]] / he - 1)
        gross = side * basis_f + side * funding_acc
        net = N * gross - entry_cost - N * leg                        # exit cost too
        fills.append(SimpleNamespace(
            direction="xvi", net_pnl_usd=round(net, 2), pnl_pct=net / limits.capital,
            cost_usd=round(2 * N * leg, 2), funding_usd=round(N * side * funding_acc, 2),
            hold_hours=(exit_i - i), converged=(outcome == "converged")))
        rm.update_from_paper_trade(
            PaperResult("xvi", net / limits.capital, exit_i - i, 0.01, 0.0, outcome == "converged"),
            regime="low_vol", risk_score=0.3)
        i = exit_i
    return fills, worst_mae


def run_cross_venue_intraday(data, params=None, limits: RiskLimits = DEFAULT_LIMITS) -> dict:
    params = {**DEFAULT_PARAMS, **(params or {})}
    rm = AdaptiveRiskManager(limits)
    all_fills, worst_mae = [], 0.0
    for c in data:
        f, mae = _coin_fills(*data[c], params, rm, limits)
        all_fills += f
        worst_mae = min(worst_mae, mae)
    return {"fills": all_fills, "kpis": compute_kpis(all_fills, limits.capital),
            "equity": rm.equity, "returns": [f.pnl_pct for f in all_fills],
            "worst_intratrade_mae": round(worst_mae, 4),
            "funding_collected_usd": round(sum(f.funding_usd for f in all_fills), 2)}
