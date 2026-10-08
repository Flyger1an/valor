"""Replay Henry v2's live rules over the feed's stored 5m history (history.json), in memory.

Read-only and offline: no journal, no network, nothing in the live run is touched.
Each closed bar becomes a decision frame at the bar close, then a fill frame priced at the
NEXT bar's open (orders never fill at the price that triggered them). Symbols without a bar
in a given five minutes have no fresh quote that frame, just as they would live.

  python -m evolver.trading.henry_v2_replay --history /runtime/market/history.json
  python -m evolver.trading.henry_v2_replay --history ... --sweep      # small parameter grid

Caveats printed with every result: quotes are modeled from bar prices with an assumed spread,
and a week of data is one market episode. Treat a sweep as a sanity check, not an optimizer.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
from decimal import Decimal as D
from pathlib import Path

from . import henry_v2 as hv
from .contracts import encode

DEFAULT_SPREAD_BPS = {"BTC-USD": "4", "ETH-USD": "4"}
OTHER_SPREAD_BPS = "20"
SWEEP = {
    ("cost_gate", "edge_multiple"): ["2.0", "2.5", "3.5"],
    ("cost_gate", "momentum_target_hourly_atr"): ["2", "3"],
    ("stop_atr",): ["2.0", "2.5"],
}


def _book(symbols, epoch, fee_bps, slippage_bps):
    book = object.__new__(hv.HenryV2)  # in-memory: the reducer only needs identity
    book.identity = {"rules": hv.HENRY_V2_RULES, "epoch": epoch, "symbols": sorted(symbols),
                     "fee_bps": str(D(fee_bps)), "slippage_bps": str(D(slippage_bps)),
                     "strategies": [s.version for s in hv.CATALOG]}
    return book


def _quote(price, spread_bps, ts, bar, increment="0.00000001"):
    half = D(spread_bps)/20000
    p = D(str(price))
    return {"bid": str(p*(1-half)), "ask": str(p*(1+half)), "timestamp": ts,
            "capacity": str(D(bar["volume"])*D(".01")), "capacity_bucket": bar["timestamp"], "increment": increment}


def replay(histories, *, fee_bps="25", slippage_bps="5", spreads=None):
    spreads = spreads or {}
    symbols = sorted(s for s, bars in histories.items() if bars)
    by_time, invalid = {}, 0
    for s in symbols:
        for b in histories[s]:
            try:
                lo, hi, op, cl = (D(str(b[k])) for k in ("low", "high", "open", "close"))
                valid = float(b["timestamp"]) % 300 == 0 and 0 < lo <= min(op, cl) <= max(op, cl) <= hi
            except (KeyError, ArithmeticError, ValueError):
                valid = False
            if not valid:
                invalid += 1  # a malformed stored bar is skipped and counted, never trusted
                continue
            by_time.setdefault(float(b["timestamp"]), {})[s] = b
    times = sorted(by_time)
    if not times:
        raise ValueError("history has no bars")
    book = _book(symbols, times[0], fee_bps, slippage_bps)
    state = book.initial_state()
    frames = in_market = 0
    first_live = None
    for i, t in enumerate(times):
        bars = by_time[t]
        decide_at = t+300+5
        quotes = {s: _quote(b["close"], spreads.get(s, DEFAULT_SPREAD_BPS.get(s, OTHER_SPREAD_BPS)), decide_at-1, b)
                  for s, b in bars.items()}
        book._reduce(state, {"type": "frame", "quotes": quotes, "bars": {s: [b] for s, b in bars.items()}}, decide_at)
        state["last_at"] = decide_at
        nxt = by_time.get(times[i+1]) if i+1 < len(times) and times[i+1] == t+300 else {}
        fill_at = decide_at+10
        fills = {s: _quote(nxt[s]["open"] if s in nxt else b["close"],
                           spreads.get(s, DEFAULT_SPREAD_BPS.get(s, OTHER_SPREAD_BPS)), fill_at-1, b)
                 for s, b in bars.items()}
        book._reduce(state, {"type": "frame", "quotes": fills, "bars": {}}, fill_at)
        state["last_at"] = fill_at
        frames += 2
        in_market += state["position"] is not None
        if first_live is None and any(v.get("regime") not in (None, "warming_up") for v in state["regime_view"].values()):
            first_live = t
    report = book.report(state)
    start = first_live or times[0]
    hold = {}
    for s in symbols:
        live = [b for b in histories[s] if float(b["timestamp"]) >= start]
        if live:
            hold[s] = str(((D(live[-1]["close"])/D(live[0]["open"])-1)*100).quantize(D(".01")))
    trades = state["trades"]
    def by(key):
        groups = {}
        for t in trades:
            groups.setdefault(str(t.get(key)), []).append(D(t["pnl_usd"]))
        return {k: {"trades": len(v), "wins": sum(1 for x in v if x > 0), "pnl_usd": str(sum(v, D(0)))}
                for k, v in sorted(groups.items())}
    return {"rules_version": hv.HENRY_V2_RULES["version"], "symbols": symbols,
            "from": times[0], "to": times[-1], "days": round((times[-1]-times[0])/86400, 2),
            "trading_from": start, "warmup_hours": round((start-times[0])/3600, 1),
            "equity_usd": report["equity_usd"], "return_pct": report["return_pct"],
            "max_drawdown_pct": report["max_drawdown_pct"], "closed_trades": report["closed_trades"],
            "win_rate_pct": report["win_rate_pct"], "payoff_ratio": report["payoff_ratio"],
            "avg_win_usd": report["avg_win_usd"], "avg_loss_usd": report["avg_loss_usd"],
            "fees_paid_usd": report["fees_paid_usd"], "friction_usd": report["friction_usd"],
            "gross_pnl_before_friction_usd": report["gross_pnl_before_friction_usd"], "time_in_market_pct": round(in_market/(frames/2)*100, 1),
            "buy_and_hold_pct": hold, "by_entry_regime": report["by_entry_regime"],
            "by_family": by("family"), "by_exit_reason": by("reason"), "by_symbol": by("symbol"),
            "skips": report["skips"], "invalid_bars_skipped": invalid, "final_regimes": report["regimes"], "open_position": report["position"],
            "trades": trades,
            "caveats": ["quotes modeled from bar prices with an assumed spread; fills at next bar open",
                        "one market episode; a sweep is a sanity check, not an optimizer"]}


def sweep(histories, **kwargs):
    original = copy.deepcopy(hv.HENRY_V2_RULES)
    rows = []
    try:
        keys = list(SWEEP)
        for values in itertools.product(*(SWEEP[k] for k in keys)):
            rules = copy.deepcopy(original)
            for path, value in zip(keys, values):
                target = rules
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value
            hv.HENRY_V2_RULES.clear()
            hv.HENRY_V2_RULES.update(rules)
            r = replay(histories, **kwargs)
            rows.append({"params": {".".join(k): v for k, v in zip(keys, values)},
                         **{k: r[k] for k in ("return_pct", "max_drawdown_pct", "closed_trades", "win_rate_pct",
                                              "payoff_ratio", "time_in_market_pct")}})
    finally:
        hv.HENRY_V2_RULES.clear()
        hv.HENRY_V2_RULES.update(original)
    return sorted(rows, key=lambda r: D(r["return_pct"]), reverse=True)


def _print(r):
    print(f"Henry {r['rules_version']} replay: {r['days']} days of history, {len(r['symbols'])} symbols")
    print(f"  warmup {r['warmup_hours']}h, then trading. time in market {r['time_in_market_pct']}%")
    print(f"  equity ${r['equity_usd']}  return {r['return_pct']}%  max drawdown {r['max_drawdown_pct']}%")
    print(f"  friction ${r['friction_usd']} (fees ${r['fees_paid_usd']} + spread/slippage)  "
          f"gross before friction ${r['gross_pnl_before_friction_usd']}")
    print("  verdict:", "BEATS CASH" if D(r["return_pct"]) > 0 else "does not beat cash")
    print(f"  trades {r['closed_trades']}  win rate {r['win_rate_pct']}%  avg win ${r['avg_win_usd']}  "
          f"avg loss ${r['avg_loss_usd']}  payoff {r['payoff_ratio']}")
    print("  buy and hold over the same window:", ", ".join(f"{s} {v}%" for s, v in r["buy_and_hold_pct"].items()))
    for title, key in (("entry regime", "by_entry_regime"), ("family", "by_family"),
                       ("exit reason", "by_exit_reason"), ("symbol", "by_symbol")):
        print(f"  by {title}:", json.dumps(r[key]))
    print("  skips:", json.dumps(r["skips"]), " invalid stored bars skipped:", r["invalid_bars_skipped"])
    print("  regimes at the end:", json.dumps(r["final_regimes"]))
    for t in r["trades"][-12:]:
        print(f"    {t['symbol']:9} {t['family']:15} {t['entry_regime']:13} {t['pnl_usd']:>8}  {t['reason']:16} "
              f"{t['hold_minutes']}m{'  pressed' if t['pressed'] else ''}")
    for c in r["caveats"]:
        print("  note:", c)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--history", required=True, help="history.json written by the market feed")
    p.add_argument("--symbols", help="comma-separated subset")
    p.add_argument("--fee-bps", default="25")
    p.add_argument("--slippage-bps", default="5")
    p.add_argument("--sweep", action="store_true", help="also run a small parameter grid")
    p.add_argument("--json", help="write the full result here")
    args = p.parse_args(argv)
    data = json.loads(Path(args.history).read_text())
    histories = {s: [b for b in bars] for s, bars in data.get("histories", {}).items()}
    if args.symbols:
        keep = set(args.symbols.split(","))
        histories = {s: b for s, b in histories.items() if s in keep}
    kw = {"fee_bps": args.fee_bps, "slippage_bps": args.slippage_bps}
    result = replay(histories, **kw)
    _print(result)
    if args.sweep:
        result["sweep"] = sweep(histories, **kw)
        print("\n  sweep (sorted by return):")
        for row in result["sweep"]:
            print(f"    {row['return_pct']:>7}%  dd {row['max_drawdown_pct']:>5}%  trades {row['closed_trades']:>3}  "
                  f"win {row['win_rate_pct']:>5}%  payoff {row['payoff_ratio']}  {row['params']}")
    if args.json:
        Path(args.json).write_text(encode(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
