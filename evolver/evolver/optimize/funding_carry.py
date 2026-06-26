"""Cross-sectional funding carry — the crypto analog of FX carry, and a RISK PREMIUM (you're paid to
hold the side others crowd out of).

Funding rate is what perp longs pay shorts each period (negative = shorts pay longs). Persistently
negative funding on a coin = the market PAYS you to be long it; persistently high positive funding =
crowded longs you can collect from by being short. So rank the universe by trailing funding, LONG the
lowest, SHORT the highest, dollar-neutral. The funding you collect is PART OF THE P&L — this backtest
CREDITS it (unlike the other families, where funding is only a drag). Harvests the carry premium plus
the eventual crowding unwind. Distinct from the funding-LEVEL fade in liquidation_funding/funding_session
(those are per-asset event trades; this is a cross-sectional factor).

universe: {coin: {day_ms: (close, daily_funding)}}  (daily_funding = the day's summed 8h rates).
Returns [(rebalance_ts, net_return), ...].
"""
from __future__ import annotations

from evolver.config import RiskLimits, DEFAULT_LIMITS

DEFAULT_PARAMS = {"lookback": 7, "holding": 5, "quantile": 0.3, "skip": 1,
                  "fee_bps": 5.0, "slip_bps": 5.0}


def run_funding_carry(universe, params=None, limits: RiskLimits = DEFAULT_LIMITS, lo=None, hi=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    lb, hold, q, sk = int(p["lookback"]), int(p["holding"]), p["quantile"], int(p["skip"])
    cost_leg = (p["fee_bps"] + p["slip_bps"]) / 1e4
    coins = sorted(universe)
    dates = sorted(set().union(*[set(universe[c]) for c in coins])) if coins else []
    if len(dates) < lb + hold + sk + 4:
        return []
    cl, fu = {}, {}                                # forward-fill (close, funding) onto the date grid
    for c in coins:
        s, last, ac, af = universe[c], None, [], []
        for d in dates:
            if d in s:
                last = s[d]
            ac.append(last[0] if last else None)
            af.append(last[1] if last else None)
        cl[c], fu[c] = ac, af

    out, prev, n = [], {}, len(dates)
    t = lb + sk + 1
    while t + hold < n:
        if lo is not None and dates[t] < lo:
            t += hold
            continue
        if hi is not None and dates[t] >= hi:
            break
        score = {}                                # trailing average funding (the carry signal)
        for c in coins:
            win = [fu[c][i] for i in range(t - sk - lb, t - sk) if fu[c][i] is not None]
            if len(win) >= max(2, lb // 2) and cl[c][t] and cl[c][t + hold]:
                score[c] = sum(win) / len(win)
        if len(score) < 4:
            t += hold
            continue
        ranked = sorted(score, key=score.get)
        k = max(1, int(len(ranked) * q))
        w = {}
        for c in ranked[:k]:                       # long the LOWEST funding (paid to hold)
            w[c] = 0.5 / k
        for c in ranked[-k:]:                       # short the HIGHEST funding (collect their funding)
            w[c] = w.get(c, 0.0) - 0.5 / k
        port = 0.0
        for c, wi in w.items():
            if not (cl[c][t] and cl[c][t + hold] and cl[c][t] > 0):
                continue
            price_pnl = wi * (cl[c][t + hold] / cl[c][t] - 1)
            fund = sum(fu[c][i] for i in range(t, t + hold) if fu[c][i] is not None)
            port += price_pnl - wi * fund          # long pays +funding; short collects it
        turn = sum(abs(w.get(c, 0.0) - prev.get(c, 0.0)) for c in set(w) | set(prev))
        out.append((dates[t], port - turn * cost_leg))
        prev = w
        t += hold
    return out
