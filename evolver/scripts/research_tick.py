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
import re
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
from evolver.data.fx import fx_candles_history, fx_closes_history, oanda_candles  # noqa: E402
from evolver.data import deribit as DRB  # noqa: E402  (vol-premium: Deribit options/DVOL)
from evolver.data import venue as V  # noqa: E402  (crypto connector: EVOLVER_VENUE=okx|gate)
from evolver.data.stats import block_bootstrap_pvalue  # noqa: E402
from evolver.evolve import allocate as AL  # noqa: E402
from evolver.evolve import feedback as FB  # noqa: E402
from evolver.evolve import fitness as F  # noqa: E402
from evolver.evolve.engine import evolve  # noqa: E402
from evolver.optimize.cross_sectional import run_cross_sectional as RXS  # noqa: E402
from evolver.optimize.funding_carry import run_funding_carry as RFCY  # noqa: E402
from evolver.optimize.funding_session import run_funding_session as RFS  # noqa: E402
from evolver.optimize.fx_carry import run_fx_carry as RFC  # noqa: E402
from evolver.optimize.fx_session import run_fx_session as RFXS  # noqa: E402
from evolver.optimize.liquidation_print import run_liquidation_print as RLP  # noqa: E402
from evolver.optimize.intraday_reversion import run_intraday_reversion as RIR  # noqa: E402
from evolver.optimize.liquidation_reversion import (LIQ_SLIP_BPS,  # noqa: E402
                                                    run_liquidation_reversion as RLR)
from evolver.optimize.oi_reversion import run_oi_reversion as ROI  # noqa: E402
from evolver.optimize.trend_following import run_trend as RT  # noqa: E402
from evolver.optimize.vol_premium import run_vol_premium as RVP  # noqa: E402
from evolver.optimize.options_flow import run_options_pin as ROP, max_pain as _maxpain, oi_wall as _oiwall  # noqa: E402,E501
from evolver.research import queue as Q  # noqa: E402

# CAVEAT (survivorship): currently-listed symbols only -> biased UP (coins that delisted or blew up
# inside the window are absent). Most inflates the cross-sectional factor results; least affects the
# pooled liquidation event study. A point-in-time universe is the real fix (data effort, not done).
UNIVERSE = V.UNIVERSE   # the crypto coin list comes from the selected venue (okx/gate)
# FX majors + crosses (OANDA instrument form). No survivorship issue — permanent pairs.
FX_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF", "NZD_USD",
            "EUR_JPY", "GBP_JPY", "EUR_GBP", "AUD_JPY", "EUR_AUD"]
MONTHS = int(os.getenv("EVOLVER_RESEARCH_MONTHS", "15"))
# only surface a candidate after its REGION clears the bar this many separate cycles — makes any
# cadence (weekly/daily/hourly) safe: a single lucky search can't surface, repeated survival can.
CONFIRM = int(os.getenv("EVOLVER_RESEARCH_CONFIRM", "2"))
# a confirmation only counts if the data window advanced this many days since the last one — so the
# CONFIRM passes are on materially DIFFERENT data, not correlated re-tests of one lucky window.
CONFIRM_GAP_MS = int(float(os.getenv("EVOLVER_CONFIRM_GAP_DAYS", "7")) * 86_400_000)
# SHADOW BAR (docs/scope-gate-calibration.md) — a calibration MEASUREMENT, not a relaxation. Each cycle we
# recompute the verdict with ONLY the two decision-theoretic α knobs relaxed; the "marginal band" (passes
# relaxed, fails strict) is logged so forward CONFIRM can later adjudicate whether the strict α is
# over-rejecting real edges. It NEVER creates a candidate / alert / trade — production `passed` is untouched.
SHADOW_P = float(os.getenv("EVOLVER_SHADOW_P", "0.15"))       # relaxed bootstrap α (strict 0.05)
SHADOW_DSR = float(os.getenv("EVOLVER_SHADOW_DSR", "0.80"))   # relaxed deflated-Sharpe conf (strict 0.95)
SHADOW_LEDGER = pathlib.Path(os.getenv("EVOLVER_CALIB_LEDGER", str(ROOT / ".shadow_calibration.jsonl")))
# ^ EVOLVER_CALIB_LEDGER, NOT EVOLVER_SHADOW_LEDGER — the latter is the liquidation-basket paper book
# (shadow_runner.py). Distinct name so a calibration record can never land in the forward paper ledger.
ALLOCATE = os.getenv("EVOLVER_ALLOCATE", "1") != "0"   # bandit family selection (vs round-robin)
USE_LLM = os.getenv("EVOLVER_USE_LLM", "1") != "0"     # LLM-as-optimizer in the search (needs OPENAI_API_KEY)
HOURLY = pathlib.Path(os.getenv("EVOLVER_RESEARCH_DATA", str(ROOT / ".okx_hourly_dataset.pkl")))
HOURLY_FUND = pathlib.Path(os.getenv("EVOLVER_RESEARCH_DATA_FUND",
                                     str(ROOT / ".okx_hourly_fund_dataset.pkl")))
DAILY = pathlib.Path(os.getenv("EVOLVER_RESEARCH_DAILY", str(ROOT / ".okx_daily_dataset.pkl")))
OI_DATA = pathlib.Path(os.getenv("EVOLVER_RESEARCH_OI", str(ROOT / ".okx_oi_dataset.pkl")))
FUND_CARRY = pathlib.Path(os.getenv("EVOLVER_RESEARCH_FUND_CARRY", str(ROOT / ".okx_fund_carry_dataset.pkl")))
LIQ_PRINT = pathlib.Path(os.getenv("EVOLVER_RESEARCH_LIQ_PRINT", str(ROOT / ".okx_liq_print_dataset.pkl")))
VOL_DATA = pathlib.Path(os.getenv("EVOLVER_RESEARCH_VOL", str(ROOT / ".deribit_vol_dataset.pkl")))
OPT_FLOW = pathlib.Path(os.getenv("EVOLVER_OPT_FLOW", str(ROOT / ".deribit_optflow_dataset.pkl")))
FX_HOURLY = pathlib.Path(os.getenv("EVOLVER_FX_HOURLY", str(ROOT / ".fx_hourly_dataset.pkl")))
FX_DAILY = pathlib.Path(os.getenv("EVOLVER_FX_DAILY", str(ROOT / ".fx_daily_dataset.pkl")))
# forward-feedback: a shadow's per-candidate forward track (id -> fwd_sharpe). Empty = loop is a no-op.
FWD_SNAPSHOT = pathlib.Path(os.getenv("EVOLVER_FWD_SNAPSHOT", "")) if os.getenv("EVOLVER_FWD_SNAPSHOT") else None
LEDGER = pathlib.Path(os.getenv("EVOLVER_RESEARCH_LEDGER", str(ROOT / "research_ledger.jsonl")))


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _log_shadow(rec):
    """Append a marginal-band record (passes relaxed α, fails strict) to the calibration ledger. Pure
    telemetry — no candidate, no alert, no trade; forward CONFIRM later adjudicates if these recur."""
    try:
        with open(SHADOW_LEDGER, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


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


def _digest(cyc, fam, summ):
    """~Weekly Telegram digest (replaces the old 6-hourly heartbeat) — self-contained on the box, no
    babysitting: venue progress, candidate status, funding_carry accrual, and the latest cycle result."""
    s = Q.load()
    try:
        d0 = dt.datetime.strptime(s.get("started", ""), "%Y-%m-%d %H:%M")
        up = f"{(dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - d0).days}d"
    except Exception:
        up = "?"
    appr, pend = len(s.get("approved", [])), len(s.get("pending", []))
    fc = ""
    try:
        if FUND_CARRY.exists():
            mn = min((len(v) for v in pickle.loads(FUND_CARRY.read_bytes()).values()), default=0)
            fc = f"\nfunding_carry accrual: {mn}/150 bars" if mn else ""
    except Exception:
        fc = ""
    fams = os.getenv("EVOLVER_FAMILIES", "crypto")
    label = V.NAME if fams in ("crypto", "all") else fams   # okx/gate for crypto, "fx" for the FX hunt
    return (f"📊 Valor WEEKLY [{label}] — cycle {cyc}, up {up}\n"
            f"candidates: {appr} approved / {pend} pending{fc}\nlatest: {summ}")


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
            new = V.hourly_ohlc(c, target) if len(ex) < target * 0.8 else V.recent_ohlc(c, 300)
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
            ohlc = (V.hourly_ohlc(c, target) if len(ex) < target * 0.8
                    else V.recent_ohlc(c, 300))
            fund = V.funding(c, days=MONTHS * 30 + 5)
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
            return c, {**cache.get(c, {}), **V.daily_closes(c, 1000)}
        except Exception:
            return c, cache.get(c, {})

    with ThreadPoolExecutor(max_workers=2) as ex:
        for c, d in ex.map(one, UNIVERSE):
            if d:
                cache[c] = d
    _save(DAILY, cache)
    return cache


def refresh_oi():
    """LIVE OKX daily (close, open_interest) for the OI-reversion family. {coin:{ts:(close,oi)}}.
    OKX's OI feed has a SHORT lookback, so this ACCUMULATES across cycles: each call merges any new
    aligned (close, oi) days into the cache, building real OI history over time. Until ~2 months
    accrue the family is honestly data-thin and skips."""
    cache = pickle.loads(OI_DATA.read_bytes()) if OI_DATA.exists() else {}

    def one(c):
        try:
            cl = V.daily_closes(c, 400)
            oi = V.oi_daily(c)
        except Exception:
            return c, cache.get(c, {})
        oi_day = {}                             # OKX stamps the 1D OI bar at 16:00 UTC, NOT midnight —
        for t, v in oi.items():                 # snap to the UTC day or it never aligns with the closes
            oi_day[(t // 86_400_000) * 86_400_000] = v
        merged = dict(cache.get(c, {}))
        for ts in set(cl) & set(oi_day):        # align by UTC day (price ∩ open interest)
            merged[ts] = (cl[ts], oi_day[ts])
        return c, merged

    with ThreadPoolExecutor(max_workers=2) as ex:
        for c, d in ex.map(one, UNIVERSE):
            if d:
                cache[c] = d
    _save(OI_DATA, cache)
    return cache


def refresh_funding_carry():
    """8h-grid (close, 8h funding) for cross-sectional funding carry. {coin:{funding_ts:(close, rate)}}.
    Rebalancing on funding's NATURAL 8h cadence (3x the points of daily) is what makes OKX's ~95 days
    of funding ENOUGH to clear min_n — daily was n-starved (n=2 on 95d, 6 on 150d). Accumulates more."""
    cache = pickle.loads(FUND_CARRY.read_bytes()) if FUND_CARRY.exists() else {}

    def one(c):
        try:
            rate = V.funding(c, days=MONTHS * 30)        # {funding_ts: 8h rate}
            cl = V.hourly_closes(c, 2400)
        except Exception:
            return c, cache.get(c, {})
        rate = {(t // 3_600_000) * 3_600_000: v for t, v in rate.items()}   # snap funding ts to the hour
        merged = dict(cache.get(c, {}))                                     # (Gate stamps +1s; OKX on-hour)
        for ft in set(rate) & set(cl):          # now aligns with the on-the-hour close
            merged[ft] = (cl[ft], rate[ft])
        return c, merged

    with ThreadPoolExecutor(max_workers=2) as ex:
        for c, d in ex.map(one, UNIVERSE):
            if d:
                cache[c] = d
    _save(FUND_CARRY, cache)
    return cache


def refresh_liq_print():
    """LIVE OKX hourly (close, long_liq, short_liq) for the liquidation-PRINT family. The liquidation
    feed is recent-only, so this ACCUMULATES it: each cycle keeps prior hours' captured liquidation
    notional and adds the fresh window. Honestly data-thin until enough accrues (or a vendor feed)."""
    cache = pickle.loads(LIQ_PRINT.read_bytes()) if LIQ_PRINT.exists() else {}

    def one(c):
        try:
            cl = V.hourly_closes(c, 2000)
            lq = V.liquidations(c)                       # contract_stats by-side (gate) / instFamily (okx)
        except Exception:
            return c, cache.get(c, {})
        prev, merged = cache.get(c, {}), {}
        for ts, close in cl.items():
            if ts in lq:
                ll, sl = lq[ts]                          # fresh liquidation data this cycle
            elif ts in prev and len(prev[ts]) >= 3:
                ll, sl = prev[ts][1], prev[ts][2]        # preserve previously-captured liq (don't lose it)
            else:
                ll, sl = 0.0, 0.0
            merged[ts] = (close, ll, sl)
        return c, merged

    with ThreadPoolExecutor(max_workers=2) as ex:
        for c, d in ex.map(one, UNIVERSE):
            if d:
                cache[c] = d
    _save(LIQ_PRINT, cache)
    return cache


def refresh_vol_premium():
    """LIVE Deribit daily (close, DVOL) for the vol-premium family (BTC + ETH). {coin:{day:(close,dvol)}}.
    Deribit gives ~2.7yr of DVOL + price, so this is data-rich immediately. Accumulates across cycles."""
    cache = pickle.loads(VOL_DATA.read_bytes()) if VOL_DATA.exists() else {}

    def one(coin):
        try:
            dv = DRB.dvol_history(coin, days=1000)
            px = DRB.price_history(coin, days=1000)
        except Exception:
            return coin, cache.get(coin, {})
        dvol = {t // 86_400_000: v for t, v in dv.items()}
        price = {t // 86_400_000: v for t, v in px.items()}
        merged = dict(cache.get(coin, {}))
        for day in set(dvol) & set(price):           # align by UTC day (price ∩ DVOL)
            merged[day * 86_400_000] = (price[day], dvol[day])
        return coin, merged

    with ThreadPoolExecutor(max_workers=2) as ex:
        for c, d in ex.map(one, DRB.UNIVERSE):
            if d:
                cache[c] = d
    _save(VOL_DATA, cache)
    return cache


def refresh_options_flow():
    """Daily snapshot of Deribit options POSITIONING (BTC+ETH) for the max-pain pin family. Picks the
    DOMINANT expiry (most OI) in a 2–35d window and stores its pin/wall + spot:
    {coin: {day_ms: (spot, {"max_pain","oi_wall","total_oi","pcr","dte"})}}. Deribit serves NO historical
    OI-by-strike, so this ACCUMULATES forward — one snapshot per UTC day; backtestable once weeks accrue."""
    cache = pickle.loads(OPT_FLOW.read_bytes()) if OPT_FLOW.exists() else {}
    day = int(time.time() // 86400) * 86400 * 1000          # UTC-day bucket (one snapshot/day, overwrites)
    for coin in DRB.UNIVERSE:
        try:
            snap = DRB.oi_by_strike(coin)
        except Exception:
            continue
        spot = snap.get("spot") or 0.0
        cand = [e for e in snap.get("by_expiry", {}).values() if 2.0 <= e["dte"] <= 35.0 and e["strikes"]]
        if spot <= 0 or not cand:
            continue
        dom = max(cand, key=lambda e: e["oi"])              # the dominant near-term expiry's book
        ks = dom["strikes"]
        mp, wall = _maxpain(ks), _oiwall(ks)
        if not mp:
            continue
        c_oi = sum(c for c, _ in ks.values())
        p_oi = sum(p for _, p in ks.values())
        cache.setdefault(coin, {})[day] = (spot, {"max_pain": mp, "oi_wall": wall, "total_oi": dom["oi"],
                                                  "pcr": p_oi / max(c_oi, 1e-9), "dte": dom["dte"]})
    _save(OPT_FLOW, cache)
    return cache


def refresh_xs_hourly():
    """Hourly CLOSES for the short-term cross-sectional reversal family. Reuses the base hourly OHLC
    dataset (no extra OKX fetch) when present; cold-starts with a direct closes fetch otherwise."""
    for src in (HOURLY, HOURLY_FUND):
        if src.exists():
            cache = pickle.loads(src.read_bytes())
            return {c: {t: v[3] for t, v in s.items()} for c, s in cache.items()}   # v[3]=close (4/5-tuple)
    out = {}

    def one(c):
        try:
            return c, V.hourly_closes(c, MONTHS * 30 * 24)
        except Exception:
            return c, {}

    with ThreadPoolExecutor(max_workers=2) as ex:
        for c, d in ex.map(one, UNIVERSE):
            if d:
                out[c] = d
    return out


def refresh_fx_daily():
    """Rolling OANDA daily CLOSES for FX trend + cross-sectional. {pair: {ts: close}}."""
    cache = pickle.loads(FX_DAILY.read_bytes()) if FX_DAILY.exists() else {}

    def one(pr):
        try:
            return pr, {**cache.get(pr, {}), **fx_closes_history(pr, "D", 900)}
        except Exception:
            return pr, cache.get(pr, {})

    with ThreadPoolExecutor(max_workers=3) as ex:
        for pr, d in ex.map(one, FX_PAIRS):
            if d:
                cache[pr] = d
    _save(FX_DAILY, cache)
    return cache


def refresh_fx_hourly():
    """Rolling OANDA hourly OHLC for the FX session family. {pair: {ts: (o,h,l,c)}}."""
    cache = pickle.loads(FX_HOURLY.read_bytes()) if FX_HOURLY.exists() else {}
    target = MONTHS * 30 * 24

    def one(pr):
        ex_ = cache.get(pr, {})
        try:
            new = (fx_candles_history(pr, "H1", target) if len(ex_) < target * 0.8
                   else oanda_candles(pr, "H1", 500))
        except Exception:
            return pr, ex_
        merged = {**ex_, **new}
        if merged:
            cut = max(merged) - target * 3_600_000
            merged = {t: v for t, v in merged.items() if t >= cut}
        return pr, merged

    with ThreadPoolExecutor(max_workers=3) as ex:
        for pr, d in ex.map(one, FX_PAIRS):
            if d:
                cache[pr] = d
    _save(FX_HOURLY, cache)
    return cache


def refresh_fx_carry():
    """Refresh REAL rates from FRED -> the rates cache for fx_carry, then return FX daily closes. Any
    currency FRED doesn't cover falls back to fx_carry's embedded table. No-op without FRED_API_KEY."""
    try:
        from evolver.data import fred
        import evolver.optimize.fx_carry as fc
        fred.refresh_to_pkl()       # writes FX_RATES_PKL (the same path fx_carry reads)
        fc.reload_rates()           # pick up the freshly-fetched rates
    except Exception:
        pass
    return refresh_fx_daily()


SPACE_LIQ = {"wick_atr": (2.5, 4.5, float), "hold_hours": (3.0, 18.0, int), "body_max": (0.35, 0.7, float),
             "cooldown_h": (2.0, 18.0, int), "atr_window": (36.0, 96.0, int)}
# funding-conditioned variant: funding_min=0 recovers base liquidation, so the search can only match-
# or-beat it in-sample; the OOS gate + DSR (the extra DOF costs a trial) judge if conditioning earns it.
SPACE_LIQ_FUND = {**SPACE_LIQ, "funding_min": (0.0, 0.003, float)}
# intraday reversion: fade a coin's own lb-hour move on hourly bars. Higher-frequency than the daily
# families, so the 2x-cost stress is the binding test (the intraday fee wall). entry_z CAPPED at 1.6
# (like SPACE_OI's threshold cap): a 2sigma+ fade is too RARE to fill the 28% holdout (n<min_n), so the
# family must fade MODERATE moves to be gate-testable — which also correctly maximizes fee exposure.
SPACE_IR = {"lookback": (3.0, 8.0, int), "entry_z": (1.0, 1.6, float),
            "hold_hours": (2.0, 6.0, int), "vol_window": (72.0, 240.0, int)}
# holding capped at 15: on ~3yr of DAILY data a longer hold leaves too few OOS rebalances to validate
# (the search otherwise picks a 36-day hold for low turnover -> n=5 holdout -> meaningless). Faster
# trend only, but that's the honest limit of what daily history can confirm.
SPACE_TREND = {"lookback": (20.0, 200.0, int), "holding": (5.0, 15.0, int), "skip": (0.0, 5.0, int),
               "thr": (0.0, 0.10, float), "vol_window": (10.0, 60.0, int)}
SPACE_XS = {"w_mom": (-2.0, 2.0, float), "w_rev": (-2.0, 2.0, float), "w_vol": (-2.0, 2.0, float),
            "lookback": (5.0, 90.0, int), "holding": (2.0, 30.0, int), "quantile": (0.1, 0.4, float),
            "skip": (0.0, 5.0, int)}
# SHORT-horizon (hourly) cross-sectional reversal: same engine, short lookback/holding (in BARS=hours),
# free weights so the search finds reversal (long the losers, short the winners) if it's there.
SPACE_XS_HR = {"w_mom": (-2.0, 2.0, float), "w_rev": (-2.0, 2.0, float), "w_vol": (-1.0, 1.0, float),
               "lookback": (4.0, 48.0, int), "holding": (1.0, 12.0, int), "quantile": (0.1, 0.35, float),
               "skip": (0.0, 3.0, int)}
# OI-conditioned reversion (DAILY): fade a move only when open interest SURGED with it (fresh leverage).
# oi_thresh = min OI growth over the lookback to qualify; trade_dir flips for the robustness check.
# Thresholds + holding capped (0.20/0.10/8, not 0.40/0.20/12): a real reversion lives at MODERATE
# surges, and high thresholds make trades so rare they starve the holdout (n<min_n) -> the search would
# keep picking clean-but-untestable genomes. Capping keeps enough holdout events to actually validate.
SPACE_OI = {"lookback": (2.0, 20.0, int), "holding": (2.0, 8.0, int), "oi_thresh": (0.03, 0.20, float),
            "ret_thresh": (0.02, 0.10, float), "trade_dir": (-1.0, 1.0, float)}
# cross-sectional funding carry on the 8h funding grid: rank by trailing funding, long lowest / short
# highest. lookback/holding are in 8H PERIODS (holding 3-6 = 1-2 days); the short cap forces enough
# holdout rebalances on OKX's ~95 days (285 8h-bars) to clear min_n=12 (carry tolerates frequent rebal).
SPACE_FUND_CARRY = {"lookback": (3.0, 30.0, int), "holding": (3.0, 6.0, int),
                    "quantile": (0.2, 0.4, float), "skip": (0.0, 3.0, int)}
# liquidation-PRINT reversion: fade an actual liquidation cascade (notional spike >= liq_mult x the
# trailing baseline) in the direction the forced flow overshot. trade_dir flips for the robustness check.
SPACE_LIQ_PRINT = {"liq_mult": (3.0, 12.0, float), "lookback": (24.0, 168.0, int),
                   "hold_hours": (2.0, 24.0, int), "cooldown_h": (2.0, 24.0, int),
                   "trade_dir": (-1.0, 1.0, float)}
# vol-premium (Deribit): delta-hedged short straddles. tenor<=14 + iv_rank<=0.5 keep enough holdout
# trades to clear min_n (n-starvation guard, same as the daily spot families).
SPACE_VOL = {"tenor_days": (5.0, 14.0, int), "iv_rank_min": (0.0, 0.5, float), "lookback": (30.0, 90.0, int)}
# options pin: trade toward a near expiry's max-pain. gap_min / dte_max / hold / trade_dir (search picks
# the sign — the gate decides if the pin pulls toward, repels, or neither).
SPACE_PIN = {"gap_min": (0.01, 0.05, float), "dte_max": (5.0, 21.0, float),
             "hold_days": (2.0, 10.0, int), "trade_dir": (-1.0, 1.0, float)}
# funding-settlement seasonality: which phase-hour of the 8h cycle, how long, with/against funding sign
SPACE_FSESS = {"entry_phase": (0.0, 7.0, int), "hold_hours": (1.0, 8.0, int),
               "trade_dir": (-1.0, 1.0, float), "funding_min": (0.0, 0.003, float)}
# FX session seasonality: which SESSION (index into fx_session.SESSION_HOURS), hold, pre-window, dir
SPACE_FX_SESSION = {"session_idx": (0.0, 7.0, int), "hold_hours": (2.0, 12.0, int),
                    "lookback": (3.0, 24.0, int), "trade_dir": (-1.0, 1.0, float)}
# FX carry: cross-sectional long-high-yield/short-low-yield (rate differential), daily rebalance.
# holding capped at 15 so a ~3yr holdout keeps enough rebalances (>=~17) to clear min_n (same as trend).
SPACE_FX_CARRY = {"holding": (5.0, 15.0, int), "quantile": (0.2, 0.5, float), "skip": (0.0, 3.0, int)}

# the roster — add a family = add a row. Each: data refresh, backtest, space, fee, stability keys.
CRYPTO_FAMILIES = [
    {"name": "liquidation", "refresh": refresh_hourly, "bt": RLR, "space": SPACE_LIQ, "fee": 8.0,
     "slip": LIQ_SLIP_BPS, "stab": ("wick_atr", "hold_hours", "atr_window"), "min_cov": 24 * 60},
    {"name": "liquidation_funding", "refresh": refresh_hourly_funding, "bt": RLR, "space": SPACE_LIQ_FUND,
     "fee": 8.0, "slip": LIQ_SLIP_BPS, "stab": ("wick_atr", "hold_hours", "funding_min"), "min_cov": 24 * 60},
    # intraday_reversion (day-trading-lite, #6): fade the coin's own sharp N-hour move on hourly OHLC.
    # Trades hours not days -> min_n is easy, but the 2x-cost stress is brutal — the honest fee-wall test
    # for whether an intraday retail edge survives frictions. Reuses refresh_hourly (OKX/Gate hourly).
    {"name": "intraday_reversion", "refresh": refresh_hourly, "bt": RIR, "space": SPACE_IR, "fee": 5.0,
     "slip": 6.0, "stab": ("lookback", "entry_z", "hold_hours"), "min_cov": 24 * 60, "min_n": 30},
    # stability tests only CONTINUOUS params — entry_phase is a categorical selector (a seasonality
    # edge legitimately lives at one phase), so perturbing it would wrongly fail the robustness check.
    {"name": "funding_session", "refresh": refresh_hourly_funding, "bt": RFS, "space": SPACE_FSESS,
     "fee": 6.0, "slip": 6.0, "stab": ("hold_hours", "funding_min"), "min_cov": 24 * 30},
    # DAILY families are low-frequency: a ~3yr holdout yields only ~15-25 rebalances, so min_n=20 was
    # near-impossible. Lower it to 12 (the DSR's sqrt(n-1) + bootstrap still demand a real t-stat).
    {"name": "trend", "refresh": refresh_daily, "bt": RT, "space": SPACE_TREND, "fee": 5.0,
     "stab": ("lookback", "holding", "vol_window"), "min_cov": 150, "min_n": 12},
    {"name": "cross_sectional", "refresh": refresh_daily, "bt": RXS, "space": SPACE_XS, "fee": 4.0,
     "stab": ("lookback", "holding", "quantile"), "min_cov": 150, "min_n": 12},
    {"name": "xs_reversal", "refresh": refresh_xs_hourly, "bt": RXS, "space": SPACE_XS_HR, "fee": 5.0,
     "stab": ("lookback", "holding", "w_rev"), "min_cov": 24 * 30},
    # OI-reversion: open interest is NEW data (positioning/flow), not a price pattern. OKX's 1D OI feed
    # returns ~180 days (verified live) so this validates NOW; OI is stamped 16:00 UTC -> snapped to the
    # day in refresh_oi (else it never aligns with the closes). min_cov=60, min_n=12 (daily).
    {"name": "oi_reversion", "refresh": refresh_oi, "bt": ROI, "space": SPACE_OI, "fee": 5.0, "slip": 5.0,
     "stab": ("lookback", "holding", "oi_thresh"), "min_cov": 60, "min_n": 12},
    # funding carry: a cross-sectional RISK PREMIUM (funding credited as P&L), on the 8h funding grid.
    # OKX funding history is ~95 days = 285 8h-bars (verified live); min_cov=150 (8h bars ~50d), accrues.
    # LOW-SHARPE/dollar-neutral, so the PBO test keeps it conservative (its flat optimum + price noise
    # read ~0.5): it surfaces only STRONG-carry regimes over repeated cycles (correct for a crash-prone
    # premium), never on noise.
    {"name": "funding_carry", "refresh": refresh_funding_carry, "bt": RFCY, "space": SPACE_FUND_CARRY,
     "fee": 5.0, "slip": 5.0, "stab": ("lookback", "holding", "quantile"), "min_cov": 150, "min_n": 12},
    # liquidation PRINTS: real forced-flow data (not wick proxies), with the liquidation SIDE. OKX's liq
    # feed works via instFamily (verified live) but is recent-only, so refresh_liq_print ACCUMULATES it
    # across cycles -> honestly data-thin until ~30 days of hourly liq accrue. Hourly.
    {"name": "liquidation_print", "refresh": refresh_liq_print, "bt": RLP, "space": SPACE_LIQ_PRINT,
     "fee": 8.0, "slip": LIQ_SLIP_BPS, "stab": ("liq_mult", "hold_hours", "lookback"), "min_cov": 24 * 30},
]

# FX families — thin-but-many edges, so lower the per-period Sharpe floor (min_osr) and let the
# scale-free DSR/bootstrap (t-stat based, require significance) do the work. fx_trend/fx_xsection
# REUSE the crypto backtests on FX data; fx_session is the FX twin of funding_session.
FX_FAMILIES = [
    {"name": "fx_trend", "refresh": refresh_fx_daily, "bt": RT, "space": SPACE_TREND, "fee": 1.0,
     "stab": ("lookback", "holding", "vol_window"), "min_cov": 150, "min_osr": 0.0, "min_n": 12},
    {"name": "fx_xsection", "refresh": refresh_fx_daily, "bt": RXS, "space": SPACE_XS, "fee": 1.0,
     "stab": ("lookback", "holding", "quantile"), "min_cov": 150, "min_osr": 0.0, "min_n": 12},
    {"name": "fx_session", "refresh": refresh_fx_hourly, "bt": RFXS, "space": SPACE_FX_SESSION, "fee": 0.5,
     "slip": 1.0, "stab": ("hold_hours", "lookback"), "min_cov": 24 * 30, "min_osr": 0.0},
    {"name": "fx_carry", "refresh": refresh_fx_carry, "bt": RFC, "space": SPACE_FX_CARRY, "fee": 1.0,
     "stab": ("holding", "quantile"), "min_cov": 150, "min_osr": 0.0, "min_n": 12},
]

# ONE engine, SEPARATE hunts: pick the registry by asset class so each class's cross-family DSR
# multiplicity is counted on its own (pooling crypto+FX would wrongly inflate both bars). Default
# crypto = legacy behavior; the fx-research-runner sets EVOLVER_FAMILIES=fx.
# Deribit options hunt — its own asset class (separate state / queue / multiplicity). Two families:
#  • vol_premium (#4): variance risk premium. REJECTED on 2.7yr history; soaking forward.
#  • options_pin (#5): max-pain forced-flow. Deribit serves no historical OI-by-strike, so it
#    FORWARD-ACCUMULATES daily snapshots (data-thin/skip until ~min_cov days accrue, then the gate judges).
# Both Telegram-alert on a CONFIRM'd hit.
VOL_FAMILIES = [
    {"name": "vol_premium", "refresh": refresh_vol_premium, "bt": RVP, "space": SPACE_VOL, "fee": 5.0,
     "slip": 10.0, "stab": ("tenor_days", "iv_rank_min", "lookback"), "min_cov": 200, "min_n": 20,
     "min_coins": 2},   # Deribit liquid DVOL = BTC + ETH only (not the ≥10-coin spot floor)
    {"name": "options_pin", "refresh": refresh_options_flow, "bt": ROP, "space": SPACE_PIN, "fee": 5.0,
     "slip": 8.0, "stab": ("gap_min", "dte_max", "hold_days"), "min_cov": 60, "min_n": 15,
     "min_coins": 2},   # forward-accumulating; thin until ~2mo of snapshots
]
_FAMILY_SETS = {"crypto": CRYPTO_FAMILIES, "fx": FX_FAMILIES, "vol": VOL_FAMILIES,
                "all": CRYPTO_FAMILIES + FX_FAMILIES}
FAMILIES = _FAMILY_SETS.get(os.getenv("EVOLVER_FAMILIES", "crypto"), CRYPTO_FAMILIES)


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


def cycle(fam, data, fwd_decay=1.0):
    allts = sorted({t for s in data.values() for t in s})
    split = allts[int(len(allts) * 0.72)]
    rng = random.Random(int(time.time()) % 100000)

    def run(p, lo=None):
        return [v for _, v in fam["bt"](data, {**p, "fee_bps": fam["fee"]}, DEFAULT_LIMITS, lo=lo)]

    r = evolve(lambda p, lo, hi: fam["bt"](data, {**p, "fee_bps": fam["fee"]}, DEFAULT_LIMITS, lo=allts[0], hi=split),
               fam["space"], fam["name"], generations=4, pop=8, seed=rng.randint(1, 99999),
               use_llm=USE_LLM, log=lambda *_: None)
    g = r.elites[0].params

    # stationary block bootstrap -> preserves autocorrelation. IID per-trade resampling understates
    # the p-value on serially-correlated trade streams (overlapping holds), inflating significance.
    def boot_p(x):
        return block_bootstrap_pvalue(x, n_boot=3000)

    ho = run(g, lo=split)
    # 2x-cost stress doubles fee AND the family's OWN per-side slippage (NOT a hardcoded crypto value —
    # applying 24bps liquidation slip to a 1bp FX strategy would wrongly kill it). Families that don't
    # model slippage carry slip=0, so this just doubles their fee.
    ho2 = [v for _, v in fam["bt"](data, {**g, "fee_bps": 2 * fam["fee"], "slip_bps": 2 * fam.get("slip", 0.0)},
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
    # FORWARD-FEEDBACK: discount the Sharpe fed to the DSR by this family's LEARNED backtest->forward
    # decay (from the shadows' OOS track). A family whose promoted edges decayed forward must clear a
    # proportionally higher confidence bar. fwd_decay=1.0 until real forward data exists -> no change.
    dho = F.deflated_sharpe(elite.full_sharpe * fwd_decay, elite.n_trades, r.n_trials * len(FAMILIES),
                            r.var_trials_sr, tr_sk, tr_ku)
    # per-family economic floors: the scale-free stats (DSR/bootstrap/PBO) are untouched, but the
    # minimum per-period Sharpe (min_osr) and trade count (min_n) adapt to the family's frequency —
    # FX's thin-but-many edges set min_osr=0 and lean on the t-stat-based DSR + bootstrap.
    min_osr, min_n = fam.get("min_osr", 0.05), fam.get("min_n", 20)
    passed = (osr > min_osr and op < 0.05 and on >= min_n and o2 > 0 and dho > 0.95
              and (npos >= 0.75 * len(nb) if nb else False)
              and (r.pbo is None or r.pbo < 0.5))
    # SHADOW BAR (docs/scope-gate-calibration.md) — MEASUREMENT ONLY: identical clauses, ONLY the two α
    # knobs relaxed (op, dho); every overfitting/robustness clause frozen. Log the marginal band
    # (relaxed-pass / strict-fail) for forward adjudication. Does NOT touch `passed`, `cand`, or alerts.
    shadow_passed = (osr > min_osr and op < SHADOW_P and on >= min_n and o2 > 0 and dho > SHADOW_DSR
                     and (npos >= 0.75 * len(nb) if nb else False)
                     and (r.pbo is None or r.pbo < 0.5))
    marginal = shadow_passed and not passed
    if marginal:
        _log_shadow({"family": fam["name"], "genome": g, "oos_sharpe": round(osr, 3),
                     "oos_p": round(op, 3), "oos_n": on, "dsr": round(dho, 3), "twox_cost": round(o2, 3),
                     "stable": f"{npos}/{len(nb)}", "pbo": (None if r.pbo is None else round(r.pbo, 3)),
                     "band": "relaxed-pass/strict-fail", "ts": int(time.time()), "at": _now()})
    fwd_tag = "" if fwd_decay >= 0.999 else f" [fwd-decay ×{fwd_decay} on DSR]"
    summ = (f"{fam['name']}: OOS {osr:+.2f}{fwd_tag} (p {op:.3f}, DSR {dho:.2f}, n {on}) | "
            f"2x-cost {o2:+.2f} | stable {npos}/{len(nb)} | "
            f"pbo {'-' if r.pbo is None else round(r.pbo, 2)} | "
            f"{'PASS' if passed else 'below bar'}{'  ·shadow-marginal·' if marginal else ''}")
    cand = None
    if passed:
        cand = {"id": f"{fam['name']}-{int(time.time())}", "family": fam["name"], "genome": g,
                "oos_sharpe": round(osr, 3), "oos_p": round(op, 3), "oos_n": on, "dsr": round(dho, 3),
                "twox_cost_sharpe": round(o2, 3), "stable": f"{npos}/{len(nb)}",
                "pbo": (None if r.pbo is None else round(r.pbo, 3)),
                "fwd_decay": round(fwd_decay, 3), "found": _now()}
    return summ, cand


def tick():
    s0 = Q.load()                                  # read-only snapshot to pick the family + cycle no.
    names = [f["name"] for f in FAMILIES]
    if ALLOCATE and len(FAMILIES) > 1:             # bandit: spend search where there's promise
        idx = AL.pick(len(FAMILIES), s0, s0.get("regime", "mid"), names,
                      random.Random(int(time.time() * 1000) % (2 ** 31)))
    else:
        idx = s0.get("rotation", 0) % len(FAMILIES)
    fam = FAMILIES[idx]
    data = fam["refresh"]()
    cov = min((len(v) for v in data.values()), default=0)
    if len(data) < fam.get("min_coins", 10) or cov < fam["min_cov"]:    # vol_premium: only BTC+ETH exist
        Q.update(lambda s: s.update(rotation=s.get("rotation", 0) + 1))   # atomic rotate
        return f"[{_now()}] {fam['name']}: data thin ({len(data)} coins, min {cov} bars) — skip + rotate"
    rgm, vol_ref = AL.regime(AL.universe_vol(data), s0.get("vol_ref"))    # self-calibrated vol regime
    fwd_decay = 1.0                                 # forward-feedback: this family's learned decay
    if FWD_SNAPSHOT and FWD_SNAPSHOT.exists():
        try:
            snap = json.loads(FWD_SNAPSHOT.read_text()).get("snapshot", [])
            fwd_decay = FB.family_decays(s0.get("approved", []), snap).get(fam["name"], 1.0)
        except Exception:
            fwd_decay = 1.0
    summ, cand = cycle(fam, data, fwd_decay=fwd_decay)   # the long part runs OUTSIDE the lock
    m = re.search(r"OOS ([+\-][\d.]+)", summ)            # this cycle's best holdout Sharpe (for promise)
    osr = float(m.group(1)) if m else 0.0
    data_end = max((max(v) for v in data.values()), default=0)
    out = {}

    def commit(s):
        """Fast, UNDER the queue lock: re-reads the LATEST state, so any /approve or /reject the human
        did during the (seconds-long) cycle is preserved instead of being clobbered by a stale save."""
        s["cycles"] = s.get("cycles", 0) + 1
        out["cyc"] = s["cycles"]
        s["regime"], s["vol_ref"] = rgm, vol_ref         # store regime + self-calibrating vol ref
        AL.update(s, fam["name"], rgm, osr)              # learn this family's promise in this regime
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
    if cyc % 168 == 0:                       # ~weekly digest (was a 6-hourly heartbeat)
        notify(_digest(cyc, fam, summ))
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
