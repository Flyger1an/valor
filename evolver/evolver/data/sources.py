"""Hybrid data router — picks the best source per NEED, with fallback + lineage.

  prefer="depth"        deep history for backtest/validation   binance -> hyperliquid -> okx
  prefer="granularity"  fine-grained, spiky funding (live)     hyperliquid -> okx
  prefer="clean"        spot+perp on ONE venue (delta hedge)   okx -> binance

All funding is normalized to {utc_day_ms: summed_daily_rate} regardless of the
source's native cadence (Binance/OKX 8h, Hyperliquid 1h). Coinglass/Laevitas are
left as documented key-gated providers to add when a key exists.
"""
from __future__ import annotations

import datetime as dt

from evolver.data import binance_dumps, hyperliquid, okx

LINEAGE: list = []  # (need, kind, source, n) actually served — honest provenance


def _to_daily(by_ts: dict) -> dict:
    daily = {}
    for ts, rate in by_ts.items():
        day = (ts // 86_400_000) * 86_400_000
        daily[day] = daily.get(day, 0.0) + rate
    return daily


def _start_ms(days: int) -> int:
    return int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).timestamp() * 1000)


_FUNDING_ORDER = {"depth": ["binance", "hyperliquid", "okx"],
                  "granularity": ["hyperliquid", "okx"],
                  "clean": ["okx", "binance"]}


def funding_daily(asset: str, lookback_days: int = 540, prefer: str = "depth"):
    """Returns ({utc_day_ms: daily_rate}, source_name)."""
    for src in _FUNDING_ORDER.get(prefer, _FUNDING_ORDER["depth"]):
        try:
            if src == "binance":
                raw = binance_dumps.funding_history(f"{asset}USDT", months=max(2, lookback_days // 30))
                daily = _to_daily(raw)
            elif src == "hyperliquid":
                raw = hyperliquid.funding_history(asset, _start_ms(lookback_days), pages=lookback_days // 18 + 2)
                daily = _to_daily(raw)
            else:  # okx (already 8h -> daily here)
                daily = _to_daily(okx.okx_funding_history(f"{asset}-USDT-SWAP", lookback_days))
            if daily:
                LINEAGE.append((prefer, "funding", src, len(daily)))
                return daily, src
        except Exception:
            continue
    return {}, None


def closes_daily(asset: str, kind: str = "spot", lookback_days: int = 540, prefer: str = "depth"):
    """Returns ({utc_day_ms: close}, source_name). kind: 'spot' | 'perp'."""
    order = ["binance", "okx"] if prefer == "depth" else ["okx", "binance"]
    for src in order:
        try:
            if src == "binance":
                market = "spot" if kind == "spot" else "futures/um"
                d = binance_dumps.daily_closes(f"{asset}USDT", market, months=max(2, lookback_days // 30))
            else:
                inst = f"{asset}-USDT" if kind == "spot" else f"{asset}-USDT-SWAP"
                d = okx.okx_daily_closes(asset, 300, inst=inst)
            if d:
                LINEAGE.append((prefer, f"closes:{kind}", src, len(d)))
                return d, src
        except Exception:
            continue
    return {}, None
