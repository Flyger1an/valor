"""Shared forward-track computation for the candidate shadows (FX + crypto). Given the approved
candidates, each family's (data_kind, backtest, fee), and the live data per kind, re-runs each
candidate's backtest and returns the FORWARD-ONLY slice (trades dated AFTER promotion). Zero orders.

Both scripts/fx_shadow.py and scripts/crypto_shadow.py call compute_forward() so the forward-feedback
loop (evolve/feedback.py) gets the same per-candidate {fwd_sharpe, fwd_n} shape for both asset classes.
"""
from __future__ import annotations

import datetime as dt


def sharpe(r):
    if len(r) < 2:
        return 0.0
    m = sum(r) / len(r)
    sd = (sum((x - m) ** 2 for x in r) / (len(r) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def to_ms(s):
    try:
        return int(dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    except Exception:
        return 0


def compute_forward(approved, fam_map, data_by_kind):
    """[{id, family, since, fwd_n, fwd_sharpe, fwd_ret}] for each approved candidate whose family is in
    fam_map and whose data_kind is present. fam_map: {family: (kind, backtest, fee)}. data_by_kind:
    {kind: {symbol: data}}. Forward = trades dated >= the candidate's promotion timestamp."""
    snap = []
    for c in approved or []:
        fam = c.get("family")
        if fam not in fam_map:
            continue
        kind, bt, fee = fam_map[fam]
        data = data_by_kind.get(kind)
        if not data:
            continue
        since = to_ms(c.get("approved_at") or c.get("found") or "")
        try:
            trades = bt(data, {**c["genome"], "fee_bps": fee})
        except Exception:
            continue
        fwd = [r for ts, r in trades if ts >= since]          # forward-only = data it never trained on
        snap.append({"id": c.get("id"), "family": fam, "since": c.get("approved_at"),
                     "fwd_n": len(fwd), "fwd_sharpe": round(sharpe(fwd), 3), "fwd_ret": round(sum(fwd), 4)})
    return snap
