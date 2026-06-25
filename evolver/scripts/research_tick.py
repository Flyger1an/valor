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

import bisect
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
from evolver.data.okx import (okx_candles_ohlc, okx_funding_history,  # noqa: E402
                              okx_intraday_closes, okx_intraday_ohlc)
from evolver.data.stats import block_bootstrap_pvalue  # noqa: E402
from evolver.evolve import fitness as F  # noqa: E402
from evolver.evolve.engine import evolve  # noqa: E402
from evolver.optimize.cross_sectional import run_cross_sectional as RXS  # noqa: E402
from evolver.optimize.liquidation_reversion import (LIQ_SLIP_BPS,  # noqa: E402
                                                    run_liquidation_reversion as RLR)
from evolver.optimize.trend_following import run_trend as RT  # noqa: E402
from evolver.research import queue as Q  # noqa: E402

# CAVEAT (survivorship): currently-listed symbols only -> biased UP (coins that delisted or blew up
# inside the window are absent). Most inflates the cross-sectional factor results; least affects the
# pooled liquidation event study. A point-in-time universe is the real fix (data effort, not done).
UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "DOT", "LTC", "ADA",
            "NEAR", "ARB", "OP", "INJ", "SUI", "APT", "SEI", "ATOM", "FIL"]
MONTHS = int(os.getenv("EVOLVER_RESEARCH_MONTHS", "15"))
# only surface a candidate after its REGION clears the bar this many separate cycles — makes any
# cadence (weekly/daily/hourly) safe: a single lucky search can't surface, repeated survival can.
CONFIRM = int(os.getenv("EVOLVER_RESEARCH_CONFIRM", "2"))
# a confirmation only counts if the data window advanced this many days since the last one — so the
# CONFIRM passes are on materially DIFFERENT data, not correlated re-tests of one lucky window.
CONFIRM_GAP_MS = int(float(os.getenv("EVOLVER_CONFIRM_GAP_DAYS", "7")) * 86_400_000)
HOURLY = pathlib.Path(os.getenv("EVOLVER_RESEARCH_DATA", str(ROOT / ".okx_hourly_dataset.pkl")))
HOURLY_FUND = pathlib.Path(os.getenv("EVOLVER_RESEARCH_DATA_FUND",
                                     str(ROOT / ".okx_hourly_fund_dataset.pkl")))
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


def refresh_hourly_funding():
    """Rolling OKX hourly OHLC + the prevailing 8h funding rate per bar, for the funding-conditioned
    liquidation family. {coin: {ts: (o,h,l,c, funding)}} — the 5th element rides on each bar so the
    backtest can require the FLUSHED side to have been the crowded one (funding extreme, matching sign)."""
    cache = pickle.loads(HOURLY_FUND.read_bytes()) if HOURLY_FUND.exists() else {}
    target = MONTHS * 30 * 24

    def one(c):
        ex = cache.get(c, {})
        try:
            ohlc = (okx_intraday_ohlc(c, "1H", target) if len(ex) < target * 0.8
                    else okx_candles_ohlc(c, "1H", 300))
            fund = okx_funding_history(f"{c}-USDT-SWAP", days=MONTHS * 30 + 5)
        except Exception:
            return c, ex
        stamps = sorted(fund)
        rates = [fund[s] for s in stamps]
        merged = dict(ex)
        for ts, b in ohlc.items():
            j = bisect.bisect_right(stamps, ts) - 1     # most recent funding stamp at/_before_ this bar
            merged[ts] = (b[0], b[1], b[2], b[3], rates[j] if j >= 0 else 0.0)
        if merged:
            cut = max(merged) - target * 3_600_000
            merged = {t: v for t, v in merged.items() if t >= cut}
        return c, merged

    with ThreadPoolExecutor(max_workers=2) as ex:   # 2 calls/coin (ohlc+funding) — go gentle on limits
        for c, d in ex.map(one, UNIVERSE):
            if d:
                cache[c] = d
    _save(HOURLY_FUND, cache)
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
# funding-conditioned variant: funding_min=0 recovers base liquidation, so the search can only match-
# or-beat it in-sample; the OOS gate + DSR (the extra DOF costs a trial) judge if conditioning earns it.
SPACE_LIQ_FUND = {**SPACE_LIQ, "funding_min": (0.0, 0.003, float)}
SPACE_TREND = {"lookback": (20.0, 200.0, int), "holding": (5.0, 40.0, int), "skip": (0.0, 5.0, int),
               "thr": (0.0, 0.10, float), "vol_window": (10.0, 60.0, int)}
SPACE_XS = {"w_mom": (-2.0, 2.0, float), "w_rev": (-2.0, 2.0, float), "w_vol": (-2.0, 2.0, float),
            "lookback": (5.0, 90.0, int), "holding": (2.0, 30.0, int), "quantile": (0.1, 0.4, float),
            "skip": (0.0, 5.0, int)}

# the roster — add a family = add a row. Each: data refresh, backtest, space, fee, stability keys.
FAMILIES = [
    {"name": "liquidation", "refresh": refresh_hourly, "bt": RLR, "space": SPACE_LIQ, "fee": 8.0,
     "stab": ("wick_atr", "hold_hours", "atr_window"), "min_cov": 24 * 60},
    {"name": "liquidation_funding", "refresh": refresh_hourly_funding, "bt": RLR, "space": SPACE_LIQ_FUND,
     "fee": 8.0, "stab": ("wick_atr", "hold_hours", "funding_min"), "min_cov": 24 * 60},
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

    # stationary block bootstrap -> preserves autocorrelation. IID per-trade resampling understates
    # the p-value on serially-correlated trade streams (overlapping holds), inflating significance.
    def boot_p(x):
        return block_bootstrap_pvalue(x, n_boot=3000)

    ho = run(g, lo=split)
    # 2x-cost stress must exceed the LIVE shadow baseline, so double slippage too (not just fee) —
    # otherwise "survives 2x cost" was still cheaper than what the paper book actually charges.
    ho2 = [v for _, v in fam["bt"](data, {**g, "fee_bps": 2 * fam["fee"], "slip_bps": 2 * LIQ_SLIP_BPS},
                                   DEFAULT_LIMITS, lo=split)]
    nb = _neighbors(g, fam["space"], fam["stab"])
    nb_sr = [_sh(run(q, lo=split)) for q in nb]
    npos = sum(1 for s in nb_sr if s > 0)
    osr, on, op, o2 = _sh(ho), len(ho), boot_p(ho), _sh(ho2)
    # Trials-aware Deflated Sharpe on the HOLDOUT: the multiple-testing correction the gate was
    # missing. The bootstrap p asks "is THIS series' Sharpe > 0"; DSR asks "...given we picked the
    # BEST of r.n_trials genomes" — using the search's true trial count + Sharpe spread. PBO (now
    # actually computed, not a vacuous None) adds the population overfitting check.
    # Deflated Sharpe belongs on the IN-SAMPLE best, where the selection happened — deflating the
    # clean OOS holdout Sharpe double-counts the selection penalty. So DSR answers "was the best of
    # (n_trials x families) genomes luck?"; the OOS holdout (osr/op) then confirms it GENERALIZES.
    # Multiplicity = within-search trials x families (a real cross-family false-discovery axis); the
    # TEMPORAL axis (re-mining each cycle) is handled by the non-overlapping CONFIRM rule in tick().
    elite = r.elites[0]
    tr_sk, tr_ku = F._moments([v for _, v in elite.trades])
    dho = F.deflated_sharpe(elite.full_sharpe, elite.n_trades, r.n_trials * len(FAMILIES),
                            r.var_trials_sr, tr_sk, tr_ku)
    passed = (osr > 0.05 and op < 0.05 and on >= 20 and o2 > 0 and dho > 0.95
              and (npos >= 0.75 * len(nb) if nb else False)
              and (r.pbo is None or r.pbo < 0.5))
    summ = (f"{fam['name']}: OOS {osr:+.2f} (p {op:.3f}, DSR {dho:.2f}, n {on}) | 2x-cost {o2:+.2f} | "
            f"stable {npos}/{len(nb)} | pbo {'-' if r.pbo is None else round(r.pbo, 2)} | "
            f"{'PASS' if passed else 'below bar'}")
    cand = None
    if passed:
        cand = {"id": f"{fam['name']}-{int(time.time())}", "family": fam["name"], "genome": g,
                "oos_sharpe": round(osr, 3), "oos_p": round(op, 3), "oos_n": on, "dsr": round(dho, 3),
                "twox_cost_sharpe": round(o2, 3), "stable": f"{npos}/{len(nb)}",
                "pbo": (None if r.pbo is None else round(r.pbo, 3)), "found": _now()}
    return summ, cand


def tick():
    s0 = Q.load()                                  # read-only snapshot to pick the family + cycle no.
    fam = FAMILIES[s0.get("rotation", 0) % len(FAMILIES)]
    data = fam["refresh"]()
    cov = min((len(v) for v in data.values()), default=0)
    if len(data) < 10 or cov < fam["min_cov"]:
        Q.update(lambda s: s.update(rotation=s.get("rotation", 0) + 1))   # atomic rotate
        return f"[{_now()}] {fam['name']}: data thin ({len(data)} coins, min {cov} bars) — skip + rotate"
    summ, cand = cycle(fam, data)                  # the long part runs OUTSIDE the lock
    data_end = max((max(v) for v in data.values()), default=0)
    out = {}

    def commit(s):
        """Fast, UNDER the queue lock: re-reads the LATEST state, so any /approve or /reject the human
        did during the (seconds-long) cycle is preserved instead of being clobbered by a stale save."""
        s["cycles"] = s.get("cycles", 0) + 1
        out["cyc"] = s["cycles"]
        with LEDGER.open("a") as f:
            f.write(json.dumps({"cycle": s["cycles"], "family": fam["name"], "ts": _now(),
                                "summary": summ, "surfaced": bool(cand)}) + "\n")
        if not cand:
            s["rotation"] = s.get("rotation", 0) + 1
            return
        known = {Q._sig(p) for p in s["pending"] + s["approved"]}
        rec = s.setdefault("streaks", {}).get(rk := _region(fam, cand["genome"]))
        if not isinstance(rec, dict):              # migrate the old monotonic-int streak format
            rec = {"n": 0, "last_end": 0}
            s["streaks"][rk] = rec
        # count a confirmation ONLY if the data window advanced by CONFIRM_GAP since the last one — two
        # passes on ~99%-identical rolling data are correlated re-tests, not independent evidence
        if data_end - rec["last_end"] >= CONFIRM_GAP_MS:
            rec["n"] += 1
            rec["last_end"] = data_end
        out["nconf"] = rec["n"]
        if rec["n"] >= CONFIRM and Q._sig(cand) not in known:
            cand["confirmations"] = rec["n"]
            s["pending"].append(cand)
            out["surfaced"] = True
        s["rotation"] = s.get("rotation", 0) + 1

    Q.update(commit)

    cyc = out["cyc"]
    if out.get("surfaced"):
        notify(f"🔬 RESEARCH CANDIDATE (survived {out['nconf']} independent windows) [{cand['id']}]\n"
               f"{cand['genome']}\n{summ}\nApprove on phone: /candidates  (or CLI --approve {cand['id']})")
        return f"[{_now()}] cycle {cyc}: SURFACED {fam['name']} (confirmed {out['nconf']}x) — {summ}"
    if cand:
        return (f"[{_now()}] cycle {cyc}: {fam['name']} cleared gate "
                f"({out['nconf']}/{CONFIRM} independent confirmations, holding) — {summ}")
    if cyc % 6 == 0:
        notify(f"🔬 research heartbeat — cycle {cyc} ({fam['name']}), 0 candidates. {summ}")
    return f"[{_now()}] cycle {cyc}: nothing cleared the bar — {summ}"


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
