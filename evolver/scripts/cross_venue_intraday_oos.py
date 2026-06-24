"""Intraday inter-venue basis gate for cross-venue dislocation.

    python3 scripts/cross_venue_intraday_oos.py

Recent ~6mo (HL 1h fits one candleSnapshot; also the most forward-relevant period given
the decay). If the edge survives the hourly basis path + 4-leg execution -> first shadow
candidate. If it dies -> killed for free, same as carry.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import pickle
import random
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.data import binance_dumps as bd, hyperliquid as hl  # noqa: E402
from evolver.optimize.cross_venue_intraday import run_cross_venue_intraday  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "AVAX", "ARB", "OP", "LINK", "DOGE", "ATOM", "APT"]
MONTHS = 6
PARAMS = {"entry_ann_diff": 0.10, "exit_ann_diff": 0.02, "max_hold_hours": 336, "fee_bps": 2.0, "slip_bps": 3.0}


def _daily(by_ts):
    out = {}
    for ts, r in by_ts.items():
        out[(ts // 86_400_000) * 86_400_000] = out.get((ts // 86_400_000) * 86_400_000, 0.0) + r
    return out


def _sharpe(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def load_coin(c):
    try:
        start = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=MONTHS * 30)).timestamp() * 1000)
        bnb_p = {t: v[3] for t, v in bd.intraday_ohlc(f"{c}USDT", "futures/um", "1h", MONTHS).items()}
        hl_p = hl.closes(c, "1h", start)
        bnb_f = _daily(bd.funding_history(f"{c}USDT", MONTHS))
        hl_f = _daily(hl.funding_history(c, start, pages=MONTHS * 30 // 18 + 2))
        dd = {d: hl_f[d] - bnb_f[d] for d in set(bnb_f) & set(hl_f)}
        if bnb_p and hl_p and dd and len(set(bnb_p) & set(hl_p)) > 1000:
            return c, (bnb_p, hl_p, dd)
    except Exception:
        pass
    return c, None


def main():
    cache = ROOT / f".xvi_cache_{MONTHS}mo.pkl"
    if cache.exists():
        data = pickle.loads(cache.read_bytes())
        print(f"loaded {len(data)} coins from cache ({cache.name})")
    else:
        print(f"fetching 1h perp paths (Binance + HL) + funding for {len(COINS)} coins ({MONTHS}mo)...")
        data = {}
        with ThreadPoolExecutor(max_workers=2) as ex:
            for c, d in ex.map(load_coin, COINS):
                if d:
                    data[c] = d
        cache.write_bytes(pickle.dumps(data))
    print(f"loaded {len(data)}/{len(COINS)}: {sorted(data)}")
    if len(data) < 5:
        print("too few coins")
        return

    res = run_cross_venue_intraday(data, PARAMS, DEFAULT_LIMITS)
    rets, k = res["returns"], res["kpis"]
    n = len(rets)
    rng = random.Random(7)
    boot = [_sharpe([rng.choice(rets) for _ in rets]) for _ in range(3000)] if n >= 2 else []
    pval = (sum(1 for s in boot if s <= 0) / len(boot)) if boot else 1.0
    pos = sum(1 for c in data
              if (run_cross_venue_intraday({c: data[c]}, PARAMS, DEFAULT_LIMITS)["kpis"].get("total_return") or 0) > 0)

    print(f"\nINTRADAY inter-venue basis gate (entry>=10% ann, 1h paths, 4-leg maker+slip):")
    print(f"  trades             {n}")
    print(f"  sharpe/trade       {_sharpe(rets):+.3f}")
    print(f"  bootstrap p(edge<=0) {pval:.3f}  {'<-- SIGNIFICANT' if pval < 0.05 else '(not significant)'}")
    print(f"  win_rate           {k.get('win_rate')} | PF {k.get('profit_factor')}")
    print(f"  total_return       {k.get('total_return')}  (equity ${res['equity']:,.0f})")
    print(f"  max_dd (realized)  {k.get('max_dd')}")
    print(f"  worst intra-trade MAE {res['worst_intratrade_mae']}   <-- the hourly basis path")
    print(f"  funding collected  ${res['funding_collected_usd']:,.0f} | breadth {pos}/{len(data)}")

    survives = _sharpe(rets) > 0.05 and pval < 0.05 and (k.get("total_return") or 0) > 0 and pos >= 0.6 * len(data)
    print()
    print("VERDICT:", "SURVIVES the intraday gate -> first shadow-mode candidate ✅✅" if survives
          else "does NOT survive intraday inter-venue basis + execution -> killed (free, like carry)")


if __name__ == "__main__":
    main()
