"""Honest forward-outcome backtest: resolve each RV signal against ACTUAL future OKX
prices (did the spread really revert within the horizon?) — not a probabilistic guess.

This is the trustworthy track record. The live PerpPaperSim necessarily ESTIMATES at
decision time (no future data); the gap between the two is what shadow mode (Phase 3)
measures. Sizing uses the real AdaptiveRiskManager; outcome uses real prices.

    python3 scripts/backtest.py

Caveats (be honest): daily granularity, in-sample (no train/valid split here — the
optimizer does that), simple time-based/threshold exit, no funding/borrow modeled,
slippage = taker fees only. Treat as a sane baseline, not a promise.
"""
from __future__ import annotations

import json
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).parent))  # for backfill_signals helpers

from backfill_signals import PAIRS, WINDOW, Z_THRESHOLD, _mean, _std, okx_daily_closes  # noqa: E402
from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.core.kpis import compute_kpis  # noqa: E402
from evolver.core.risk import AdaptiveRiskManager, PaperResult  # noqa: E402

FEE_BPS = 5.0          # OKX taker, per leg
MAX_HOLD_DAYS = 14
EXIT_Z = 0.5           # close when the spread has reverted to within 0.5σ


def _collect_trades():
    bases = sorted({b for p in PAIRS for b in p[:2]})
    closes = {b: okx_daily_closes(b) for b in bases}
    trades = []
    for a, b, _typ in PAIRS:
        ts = sorted(set(closes[a]) & set(closes[b]))
        ratio = [closes[a][t] / closes[b][t] for t in ts]
        i = WINDOW
        while i < len(ratio) - 1:
            window = ratio[i - WINDOW:i]
            sd = _std(window)
            if sd == 0:
                i += 1
                continue
            m = _mean(window)
            entry = ratio[i]
            z = (entry - m) / sd
            if abs(z) < Z_THRESHOLD:
                i += 1
                continue
            sign = 1.0 if z < 0 else -1.0  # long the spread if below mean, short if above
            # resolve FORWARD against real prices: exit on revert-to-mean or max hold
            exit_j = min(i + MAX_HOLD_DAYS, len(ratio) - 1)
            for j in range(i + 1, min(i + MAX_HOLD_DAYS, len(ratio))):
                if abs((ratio[j] - m) / sd) <= EXIT_Z:
                    exit_j = j
                    break
            gross = sign * (ratio[exit_j] / entry - 1.0)   # REAL forward move
            rets = [window[k] / window[k - 1] - 1 for k in range(1, len(window))]
            vol = _std(rets) if rets else 0.02
            trades.append({
                "ts": ts[i], "sign": sign, "gross": gross, "hold_h": (exit_j - i) * 24.0,
                "converged": abs((ratio[exit_j] - m) / sd) <= EXIT_Z, "vol": vol,
                "risk_score": min(0.9, 0.2 + vol * 8),
            })
            i = exit_j  # non-overlapping per pair
    trades.sort(key=lambda x: x["ts"])  # portfolio time order
    return trades


def run():
    lim = DEFAULT_LIMITS
    rm = AdaptiveRiskManager(lim)
    fills = []
    for tr in _collect_trades():
        notional = lim.capital * rm.current_pos_pct * rm.current_leverage
        fees = notional * (2 * FEE_BPS / 1e4)
        net = notional * tr["gross"] - fees
        pnl_pct = net / lim.capital
        fills.append(SimpleNamespace(
            direction="long_spread" if tr["sign"] > 0 else "short_spread",
            net_pnl_usd=round(net, 2), pnl_pct=pnl_pct, cost_usd=round(fees, 2),
            funding_usd=0.0, hold_hours=tr["hold_h"], converged=tr["converged"],
        ))
        rm.update_from_paper_trade(
            PaperResult("bt", pnl_pct, tr["hold_h"], tr["vol"], 0.0, tr["converged"]),
            regime=("high_vol" if tr["vol"] > 0.04 else "low_vol"), risk_score=tr["risk_score"],
        )
    return fills, rm


if __name__ == "__main__":
    fills, rm = run()
    print(f"REAL forward-outcome backtest — {len(fills)} trades")
    print(json.dumps(compute_kpis(fills, DEFAULT_LIMITS.capital), indent=2))
    print(f"\nfinal equity: ${rm.equity:,.2f} | max drawdown tracked by risk mgr: {rm.drawdown:.1%}")
