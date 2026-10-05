"""Versioned, long-only cash strategies shared by forward workers and historical evaluation.

These are research baselines, not validated alpha. Only these predeclared variants can be promoted;
neither agent can generate executable code or change the risk policy.
"""
from __future__ import annotations

import hashlib
import statistics
from dataclasses import asdict, dataclass
from decimal import ROUND_DOWN

from .contracts import Intent, Policy, decimal, encode


@dataclass(frozen=True)
class Strategy:
    family: str
    fast: int
    slow: int
    stop_fraction: str
    target_fraction: str
    max_hold_bars: int = 144

    @property
    def version(self):
        return self.family + "@" + hashlib.sha256(encode(asdict(self)).encode()).hexdigest()[:12]


CATALOG = tuple(Strategy(family, fast, slow, stop, target)
                for family, windows in (("ema_trend", ((9, 21), (12, 26), (20, 50))),
                                        ("breakout", ((12, 24), (24, 48), (48, 96))),
                                        ("mean_reversion", ((10, 20), (20, 40), (30, 60))))
                for (fast, slow), stop, target in zip(windows, (".01", ".02", ".03"), (".02", ".04", ".06")))
BY_VERSION = {s.version: s for s in CATALOG}


def ema(values, window):
    result = values[0]
    alpha = 2 / (window + 1)
    for value in values[1:]:
        result += alpha * (value - result)
    return result


def entry_signal(spec: Strategy, bars: list[dict]) -> bool:
    if len(bars) < spec.slow * 3 + 1:
        return False
    bars = bars[-spec.slow * 3 - 1:]
    closes = [float(b["close"]) for b in bars]
    if spec.family == "ema_trend":
        return (ema(closes[:-1], spec.fast) <= ema(closes[:-1], spec.slow) and
                ema(closes, spec.fast) > ema(closes, spec.slow))
    if spec.family == "breakout":
        return closes[-1] > max(float(b["high"]) for b in bars[-spec.slow-1:-1])
    mean, sd = statistics.mean(closes[-spec.slow:]), statistics.pstdev(closes[-spec.slow:])
    return sd > 0 and closes[-1] < mean - 2 * sd


def exit_signal(spec: Strategy, bars: list[dict]) -> bool:
    bars = bars[-spec.slow * 3 - 1:]
    closes = [float(b["close"]) for b in bars]
    if len(closes) < spec.slow:
        return False
    if spec.family == "mean_reversion":
        return closes[-1] >= statistics.mean(closes[-spec.slow:])
    return ema(closes, spec.fast) < ema(closes, spec.slow)


def position_budget(policy: Policy, equity, scale=1):
    equity, scale = decimal(equity), decimal(scale)
    multiple = decimal(1)
    while equity >= policy.starting_cash * multiple * 2:
        multiple *= 2
    return min(policy.max_position_notional * multiple,
               max(equity, decimal(0)) * policy.max_position_notional / policy.starting_cash) * scale


def make_intents(spec: Strategy, histories, quotes, snapshot, policy: Policy, now: float, increments=None):
    """Only closed, recent bars may generate a signal. One immutable signal ID per version/bar/side."""
    result = []
    positions = {p["instrument"]: p for p in snapshot["positions"]}
    for symbol, quote in quotes.items():
        bars = histories.get(symbol, [])
        if not bars or not 0 <= now - (bars[-1]["timestamp"] + 300) <= 600:
            continue
        pos = positions.get(symbol)
        active_spec = BY_VERSION.get(pos["strategy"]) if pos else spec
        if active_spec is None:
            continue
        signal_id = str(int(bars[-1]["timestamp"]))
        if pos:
            entry = decimal(pos["cost_basis"]) / decimal(pos["quantity"])
            take_profit = quote.bid >= entry * (1 + decimal(active_spec.target_fraction))
            max_hold = now - pos["opened"] >= active_spec.max_hold_bars * 300
            if take_profit or max_hold or exit_signal(active_spec, bars):
                quantity = decimal(pos["quantity"])
                if increments and symbol in increments:
                    step = decimal(increments[symbol])
                    quantity = (quantity/step).to_integral_value(rounding=ROUND_DOWN)*step
                if quantity > 0:
                    result.append(Intent(signal_id, pos["strategy"], symbol, "sell", quantity,
                                         decimal(0), now, "target_or_strategy_exit"))
        elif entry_signal(spec, bars):
            limit = quote.ask * (1 + policy.slippage_bps / 10000)
            budget = position_budget(policy, snapshot["equity"], snapshot["supervisor"]["scale"])
            step = decimal((increments or {}).get(symbol, "0.00000001"))
            quantity = (budget / limit / step).to_integral_value(rounding=ROUND_DOWN) * step
            if quantity > 0:
                result.append(Intent(signal_id, spec.version, symbol, "buy", quantity,
                                     quote.bid * (1 - decimal(spec.stop_fraction)), now,
                                     f"{spec.family}: closed-bar signal", limit))
    return result


def backtest(spec: Strategy, bars: list[dict], policy: Policy, start=0, end=float("inf")):
    """Next-open fills, adverse gap stops, conservative same-bar stop/target ordering, both-side costs.

    Returns fractional returns on position notional. Open tail positions are excluded, not invented wins.
    This bar model does not establish exchange execution fidelity.
    """
    import datetime as dt
    returns, pos = [], None
    friction = float(policy.fee_bps + policy.slippage_bps) / 10000
    for i in range(spec.slow * 3, len(bars)-1):
        history, bar = bars[max(0, i-spec.slow*3):i+1], bars[i+1]
        timestamp = bar["timestamp"]
        if timestamp < start:
            continue
        if timestamp >= end:
            break
        opening, high, low = (float(bar[k]) for k in ("open", "high", "low"))
        if pos:
            entry, opened, stop, target = pos
            if i - opened >= spec.max_hold_bars or exit_signal(spec, history):
                # This decision is already known at the opening; do not peek at a later target hit.
                price = opening
            elif low <= stop:
                price = min(opening, stop)
            elif high >= target:
                price = target
            else:
                continue
            returns.append((timestamp, (price * (1-friction)) / (entry * (1+friction)) - 1))
            pos = None
            continue
        utc = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc)
        if utc.hour not in policy.trading_hours_utc or utc.weekday() not in policy.trading_weekdays_utc:
            continue
        if entry_signal(spec, history):
            pos = (opening, i, opening * (1-float(spec.stop_fraction)), opening * (1+float(spec.target_fraction)))
            # A stop hit during the entry bar must count; never skip that bar's adverse excursion.
            if low <= pos[2]:
                returns.append((timestamp, (min(opening, pos[2]) * (1-friction)) / (opening * (1+friction)) - 1))
                pos = None
    return returns
