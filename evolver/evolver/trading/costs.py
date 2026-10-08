"""Study economics: distinguish broker P&L, cash flows, and estimated external bills."""
from __future__ import annotations

import calendar
import datetime as dt

from .contracts import decimal as D


def hosting_estimate(start, end):
    """Approved Droplet: $0.018/hour, capped at $12 per UTC calendar month.

    This is a runtime-period accrual, not an invoice; pre-runtime provisioning,
    taxes and future billing adjustments are explicitly outside the estimate.
    """
    if start is None or end <= start:
        return D(0)
    total, cursor = D(0), start
    while cursor < end:
        date = dt.datetime.fromtimestamp(cursor, dt.timezone.utc)
        days = calendar.monthrange(date.year, date.month)[1]
        boundary = dt.datetime(date.year, date.month, days, tzinfo=dt.timezone.utc).timestamp() + 86400
        until = min(end, boundary)
        total += min(D("12"), D(str(until-cursor))/3600*D("0.018"))
        cursor = until
    return total


def cost_summary(snapshot, usage, now):
    model = D(usage.get("estimated_cost_usd", "0"))
    hosting = hosting_estimate(snapshot.get("runtime_started_at"), now)
    flows = D(snapshot.get("accounting", {}).get("net_cash_flows", "0"))
    net = D(snapshot["equity"])-D(snapshot["starting_cash"])-flows
    return {"basis": "broker equity less external cash flows; operating costs are estimates, not invoices",
            "net_cash_flows_usd": str(flows), "net_trading_pnl_usd": str(net),
            "model_estimated_usd": str(model), "hosting_estimated_usd": str(hosting),
            "data_subscription_usd": "0", "total_operating_estimated_usd": str(model+hosting),
            "experiment_pnl_estimated_usd": str(net-model-hosting),
            "model_usage_available": "estimated_cost_usd" in usage,
            "fees_provisional": snapshot.get("accounting", {}).get("fees_provisional", snapshot["mode"] == "paper"),
            "unverified_costs": ["provider invoices and taxes", "hosting before runtime initialization"],
            "spread_and_slippage": "already reflected in execution prices; do not subtract twice"}
