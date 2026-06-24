"""Autonomous gated research loop — perpetual DISCOVERY across a ROSTER of families, on FRESH
data, with human-gated PROMOTION.

Each cycle round-robins to the next strategy family (liquidation / trend / cross-sectional),
refreshes that family's data from LIVE OKX, runs the evolutionary search on train, walk-forwards
onto the never-seen holdout, and applies the SAME family-agnostic honest gate (OOS significance
+ 2x cost + parameter STABILITY). Gate-passers go to the shared queue + Telegram for your
approval. Promotes NOTHING by itself. Adding a family = one registry entry.

    python3 scripts/research_tick.py             # one cycle (cron)
    python3 scripts/research_tick.py --loop 604800
    python3 scripts/research_tick.py --pending / --approve <id>
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import pickle
import random
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _l in ((ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []):
    if "=" in _l and not _l.strip().startswith("#"):
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.data.okx import okx_candles_ohlc, okx_intraday_closes, okx_intraday_ohlc  # noqa: E402
from evolver.evolve.engine import evolve  # noqa: E402
from evolver.optimize.cross_sectional import run_cross_sectional as RXS  # noqa: E402
from evolver.optimize.liquidation_reversion import run_liquidation_reversion as RLR  # noqa: E402
from evolver.optimize.trend_following import run_trend as RT  # noqa: E402
from evolver.research import queue as Q  # noqa: E402

UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "DOT", "LTC", "ADA",
            "NEAR", "ARB", "OP", "INJ", "SUI", "APT", "SEI", "ATOM", "FIL"]
MONTHS = int(os.getenv("EVOLVER_RESEARCH_MONTHS", "15"))
# only surface a candidate after its REGION clears the bar this many separate cycles — makes any
# cadence (weekly/daily/hourly) safe: a single lucky search can't surface, repeated survival can.
CONFIRM = int(os.getenv("EVOLVER_RESEARCH_CONFIRM", "2"))
HOURLY = pathlib.Path(os.getenv("EVOLVER_RESEARCH_DATA", str(ROOT / ".okx_hourly_dataset.pkl")))
DAILY = pathlib.Path(os.getenv("EVOLVER_RESEARCH_DAILY", str(ROOT / ".okx_daily_dataset.pkl")))
LEDGER = pathlib.Path(os.getenv("EVOLVER_RESEARCH_LEDGER", str(ROOT / "research_ledger.jsonl")))


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _sh(x):
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    sd = (sum((v - m) ** 2 for v in x) / (len(x) - 1)) ** 0.5
    return m / sd if sd > 0 else 0.0


def notify(msg):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_ADMIN_CHAT_IDS", "").split(",")[0]
    if not (tok and chat):
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10)
    except Exception:
        pass


def _save(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(obj))
    os.replace(tmp, path)


def refresh_hourly():
    """Rolling LIVE OKX hourly OHLC for the liquidation family. {coin:{ts:(o,h,l,c)}}."""
    cache = pickle.loads(HOURLY.read_bytes()) if HOURLY.exists() else {}
    target = MONTHS * 30 * 24

    def one(c):
        ex = cache.get(c, {})
        try:
            new = okx_intraday_ohlc(c, "1H", target) if len(ex) < target * 0.8 else okx_candles_ohlc(c, "1H", 300)
        except Exception:
            return c, ex
        merged = {**ex, **new}
        if merged:
            cut = max(merged) - target * 3_600_000
            merged = {t: v for t, v in merged.items() if t >= cut}
        return c, merged

    with ThreadPoolExecutor(max_workers=2) as ex:   # OKX history is rate-limited; go gentle
        for c, d in ex.map(one, UNIVERSE):
            if d:
                cache[c] = d
    _save(HOURLY, cache)
    return cache


def refresh_daily():
    """LIVE OKX daily closes for trend / cross-sectional families. {coin:{ts:close}}."""
    cache = pickle.loads(DAILY.read_bytes()) if DAILY.exists() else {}

    def one(c):
        try:   # paginate ~3yr of daily closes so trend/x-sectional have a real holdout
            return c, {**cache.get(c, {}), **okx_intraday_closes(c, "1Dutc", 1000, inst=f"{c}-USDT-SWAP")}
        except Exception:
            return c, cache.get(c, {})

    with ThreadPoolExecutor(max_workers=2) as ex:
        for c, d in ex.map(one, UNIVERSE):
            if d:
                cache[c] = d
    _save(DAILY, cache)
    return cache


SPACE_LIQ = {"wick_atr": (2.5, 4.5, float), "hold_hours": (3.0, 18.0, int), "body_max": (0.35, 0.7, float),
             "cooldown_h": (2.0, 18.0, int), "atr_window": (36.0, 96.0, int)}
SPACE_TREND = {"lookback": (20.0, 200.0, int), "holding": (5.0, 40.0, int), "skip": (0.0, 5.0, int),
               "thr": (0.0, 0.10, float), "vol_window": (10.0, 60.0, int)}
SPACE_XS = {"w_mom": (-2.0, 2.0, float), "w_rev": (-2.0, 2.0, float), "w_vol": (-2.0, 2.0, float),
            "lookback": (5.0, 90.0, int), "holding": (2.0, 30.0, int), "quantile": (0.1, 0.4, float),
            "skip": (0.0, 5.0, int)}

# the roster — add a family = add a row. Each: data refresh, backtest, space, fee, stability keys.
FAMILIES = [
    {"name": "liquidation", "refresh": refresh_hourly, "bt": RLR, "space": SPACE_LIQ, "fee": 8.0,
     "stab": ("wick_atr", "hold_hours", "atr_window"), "min_cov": 24 * 60},
    {"name": "trend", "refresh": refresh_daily, "bt": RT, "space": SPACE_TREND, "fee": 5.0,
     "stab": ("lookback", "holding", "vol_window"), "min_cov": 150},
    {"name": "cross_sectional", "refresh": refresh_daily, "bt": RXS, "space": SPACE_XS, "fee": 4.0,
     "stab": ("lookback", "holding", "quantile"), "min_cov": 150},
]


def _region(fam, g):
    """Coarse bucket of a genome's stability params, so 'the same edge found again' counts toward
    confirmation even if the stochastic search lands on a slightly different nearby genome."""
    parts = []
    for k in fam["stab"]:
        lo, hi, _ = fam["space"][k]
        parts.append(round(g[k] / (((hi - lo) / 3) or 1)))   # 3 coarse zones/param -> near-twins match
    return f"{fam['name']}:" + ":".join(map(str, parts))


def _neighbors(g, space, keys):
    out = []
    for k in keys:
        lo, hi, typ = space[k]
        for f in (0.85, 1.15):
            q = dict(g)
            v = max(lo, min(hi, (g[k] if g[k] else (lo + hi) / 2) * f))
            q[k] = int(round(v)) if typ is int else round(v, 3)
            if q != g:
                out.append(q)
    return out


def cycle(fam, data):
    allts = sorted({t for s in data.values() for t in s})
    split = allts[int(len(allts) * 0.72)]
    rng = random.Random(int(time.time()) % 100000)

    def run(p, lo=None):
        return [v for _, v in fam["bt"](data, {**p, "fee_bps": fam["fee"]}, DEFAULT_LIMITS, lo=lo)]

    r = evolve(lambda p, lo, hi: fam["bt"](data, {**p, "fee_bps": fam["fee"]}, DEFAULT_LIMITS, lo=allts[0], hi=split),
               fam["space"], fam["name"], generations=4, pop=8, seed=rng.randint(1, 99999),
               use_llm=False, log=lambda *_: None)
    g = r.elites[0].params

    def boot_p(x):
        if len(x) < 2:
            return 1.0
        b = [_sh([rng.choice(x) for _ in x]) for _ in range(3000)]
        return sum(1 for s in b if s <= 0) / len(b)

    ho = run(g, lo=split)
    ho2 = [v for _, v in fam["bt"](data, {**g, "fee_bps": 2 * fam["fee"]}, DEFAULT_LIMITS, lo=split)]
    nb = _neighbors(g, fam["space"], fam["stab"])
    nb_sr = [_sh(run(q, lo=split)) for q in nb]
    npos = sum(1 for s in nb_sr if s > 0)
    osr, on, op, o2 = _sh(ho), len(ho), boot_p(ho), _sh(ho2)
    passed = osr > 0.05 and op < 0.05 and on >= 20 and o2 > 0 and (npos >= 0.75 * len(nb) if nb else False)
    summ = (f"{fam['name']}: OOS {osr:+.2f} (p {op:.3f}, n {on}) | 2x-cost {o2:+.2f} | "
            f"stable {npos}/{len(nb)} | {'PASS' if passed else 'below bar'}")
    cand = None
    if passed:
        cand = {"id": f"{fam['name']}-{int(time.time())}", "family": fam["name"], "genome": g,
                "oos_sharpe": round(osr, 3), "oos_p": round(op, 3), "oos_n": on,
                "twox_cost_sharpe": round(o2, 3), "stable": f"{npos}/{len(nb)}", "found": _now()}
    return summ, cand


def tick():
    s = Q.load()
    fam = FAMILIES[s.get("rotation", 0) % len(FAMILIES)]
    data = fam["refresh"]()
    cov = min((len(v) for v in data.values()), default=0)
    if len(data) < 10 or cov < fam["min_cov"]:
        s["rotation"] = s.get("rotation", 0) + 1
        Q.save(s)
        return f"[{_now()}] {fam['name']}: data thin ({len(data)} coins, min {cov} bars) — skip + rotate"
    s["cycles"] = s.get("cycles", 0) + 1
    summ, cand = cycle(fam, data)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"cycle": s["cycles"], "family": fam["name"], "ts": _now(),
                            "summary": summ, "surfaced": bool(cand)}) + "\n")
    known = {Q._sig(p) for p in s["pending"] + s["approved"]}
    if cand:
        rk = _region(fam, cand["genome"])
        s.setdefault("streaks", {})
        s["streaks"][rk] = s["streaks"].get(rk, 0) + 1
        nconf = s["streaks"][rk]
        if nconf >= CONFIRM and Q._sig(cand) not in known:
            cand["confirmations"] = nconf
            s["pending"].append(cand)
            notify(f"🔬 RESEARCH CANDIDATE (survived {nconf} separate cycles) [{cand['id']}]\n"
                   f"{cand['genome']}\n{summ}\nApprove on phone: /candidates  (or CLI --approve {cand['id']})")
            msg = f"[{_now()}] cycle {s['cycles']}: SURFACED {fam['name']} (confirmed {nconf}x) — {summ}"
        else:
            msg = (f"[{_now()}] cycle {s['cycles']}: {fam['name']} cleared gate "
                   f"({nconf}/{CONFIRM} confirmations, holding) — {summ}")
    else:
        msg = f"[{_now()}] cycle {s['cycles']}: nothing cleared the bar — {summ}"
        if s["cycles"] % 6 == 0:
            notify(f"🔬 research heartbeat — cycle {s['cycles']} ({fam['name']}), 0 candidates. {summ}")
    s["rotation"] = s.get("rotation", 0) + 1
    Q.save(s)
    return msg


def main():
    a = sys.argv[1:]
    if a and a[0] == "--pending":
        pend = Q.list_pending()
        if not pend:
            print("no candidates awaiting approval.")
        for c in pend:
            print(f"  [{c['id']}] {c['family']} {c['genome']}")
            print(f"      OOS {c['oos_sharpe']} (p {c['oos_p']}, n {c['oos_n']}) | 2x-cost "
                  f"{c['twox_cost_sharpe']} | stable {c['stable']} | found {c['found']}")
        print("\n  approve: python3 scripts/research_tick.py --approve <id>  (or /candidates on Telegram)")
    elif a and a[0] == "--approve":
        hit = Q.approve(a[1] if len(a) > 1 else "", actor="cli")
        print(f"approved {hit['id']} — record it into the shadow basket next." if hit else "no such candidate")
        if hit:
            notify(f"✅ APPROVED {hit['id']} (CLI). {hit['genome']} recorded for shadow.")
    elif a and a[0] == "--loop":
        every = int(a[1]) if len(a) > 1 else 604800
        print(f"research loop: cycle every {every}s, rotating {[f['name'] for f in FAMILIES]}")
        while True:
            try:
                print(tick())
            except Exception as e:
                print(f"[{_now()}] cycle error: {e}")
            time.sleep(every)
    else:
        print(tick())


if __name__ == "__main__":
    main()
