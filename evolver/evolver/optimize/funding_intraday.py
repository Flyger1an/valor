"""Risk-modeled intraday funding-carry backtest.

Walks HOURLY (not daily), so the basis actually moves and we can see what the daily
model hid:
  * intra-trade adverse excursion (the real drawdown)
  * perp-leg LIQUIDATION when the basis blows out past the margin buffer (~1/L)
  * slippage that scales with how hot funding is (hot funding => thin, fast markets)
Delta-neutral short-perp/long-spot; sizing via the real AdaptiveRiskManager.

Data per coin: spot {ts:close}, perp {ts:(o,h,l,c)}, funding {ts:8h_rate}.
"""
from __future__ import annotations

from types import SimpleNamespace

from evolver.config import RiskLimits, DEFAULT_LIMITS
from evolver.core.kpis import compute_kpis
from evolver.core.risk import AdaptiveRiskManager, PaperResult

MAINT = 0.005       # maintenance margin
LIQ_PENALTY = 0.012  # liquidation fee + slippage, fraction of notional
DEFAULT_PARAMS = {
    "entry_ann_funding": 0.10, "exit_ann_funding": 0.03, "max_hold_hours": 168,
    "maker_bps": 2.0, "slip_bps": 4.0, "slip_funding_k": 12.0,  # extra slip ~ funding heat
    "basis_stop": None,  # exit if adverse basis MTM breaches -basis_stop (None = ride it)
}


def _coin_fills(spot, perp, funding, params, rm, limits):
    ts = sorted(set(spot) & set(perp))
    if len(ts) < 48:
        return [], 0, 0, 0.0
    fts = sorted(funding)
    # forward-fill current 8h funding rate per hour
    cur, j, last = {}, 0, 0.0
    for t in ts:
        while j < len(fts) and fts[j] <= t:
            last = funding[fts[j]]
            j += 1
        cur[t] = last

    mh = int(params["max_hold_hours"])
    stop = params.get("basis_stop")
    fills, liqs, stops, worst_mae = [], 0, 0, 0.0
    i = 0
    while i < len(ts) - 1:
        t = ts[i]
        ann = cur[t] * 3 * 365
        if abs(ann) < params["entry_ann_funding"]:
            i += 1
            continue
        side = -1.0 if cur[t] > 0 else 1.0           # short perp to collect +funding
        L = rm.current_leverage
        N = limits.capital * rm.current_pos_pct * L
        pe, se = perp[t][3], spot[t]                 # entry closes
        slip = (params["slip_bps"] + params["slip_funding_k"] * min(abs(ann), 1.5)) / 1e4
        maker = params["maker_bps"] / 1e4
        entry_cost = N * (maker + slip)
        margin_frac = 1.0 / L                         # liquidation buffer on the perp leg

        end = min(i + mh, len(ts) - 1)
        funding_acc, outcome, exit_i, mae = 0.0, "exit", end, 0.0
        prev = t
        for k in range(i + 1, end + 1):
            tk = ts[k]
            funding_acc += sum(funding[ft] for ft in fts if prev < ft <= tk)
            prev = tk
            # adverse intrabar basis (perp HIGH if short, LOW if long)
            adv_perp = perp[tk][1] if side < 0 else perp[tk][2]
            adv_price = side * ((adv_perp / pe - 1) - (spot[tk] / se - 1))   # worst price PnL frac
            mtm = adv_price + (-side * funding_acc)   # adverse mark = basis blowout + funding so far
            mae = min(mae, mtm)
            if stop is not None and mtm <= -stop:    # basis stop — cut the tail
                outcome, exit_i = "stop", k
                break
            if mtm <= -margin_frac:                  # perp leg liquidated
                outcome, exit_i = "liq", k
                break
            if abs(cur[tk] * 3 * 365) < params["exit_ann_funding"]:
                outcome, exit_i = "exit", k
                break

        worst_mae = min(worst_mae, mae)
        funding_pnl = -side * funding_acc
        if outcome == "liq":
            liqs += 1
            gross = -margin_frac - LIQ_PENALTY + funding_pnl   # lose the margin + penalty
            net = N * gross - entry_cost
        elif outcome == "stop":
            stops += 1
            gross = -stop                                      # loss capped at the stop
            net = N * gross - entry_cost - N * (5 / 1e4 + 2 * slip)  # urgent taker exit + whipsaw
        else:
            px, sx = perp[ts[exit_i]][3], spot[ts[exit_i]]
            price_pnl = side * ((px / pe - 1) - (sx / se - 1))
            gross = funding_pnl + price_pnl
            net = N * gross - entry_cost - N * (maker + slip)
        pnl_pct = net / limits.capital
        fills.append(SimpleNamespace(
            direction="carry", net_pnl_usd=round(net, 2), pnl_pct=pnl_pct,
            cost_usd=round(entry_cost, 2), funding_usd=round(N * funding_acc, 2),
            hold_hours=(exit_i - i), converged=(outcome == "exit")))
        rm.update_from_paper_trade(
            PaperResult("fc", pnl_pct, exit_i - i, 0.02, 0.0, outcome == "exit"),
            regime="low_vol", risk_score=0.4)
        i = exit_i
    return fills, liqs, stops, worst_mae


def run_intraday_funding(data, params=None, limits: RiskLimits = DEFAULT_LIMITS) -> dict:
    """data: {coin: (spot{ts:close}, perp{ts:(o,h,l,c)}, funding{ts:rate})}."""
    params = {**DEFAULT_PARAMS, **(params or {})}
    rm = AdaptiveRiskManager(limits)
    all_fills, liqs, stops, worst_mae = [], 0, 0, 0.0
    for coin in data:
        spot, perp, funding = data[coin]
        f, lq, st, mae = _coin_fills(spot, perp, funding, params, rm, limits)
        all_fills += f
        liqs += lq
        stops += st
        worst_mae = min(worst_mae, mae)
    return {"fills": all_fills, "kpis": compute_kpis(all_fills, limits.capital),
            "equity": rm.equity, "returns": [f.pnl_pct for f in all_fills],
            "liquidations": liqs, "stops": stops, "worst_intratrade_mae": round(worst_mae, 4),
            "funding_collected_usd": round(sum(f.funding_usd for f in all_fills), 2)}
