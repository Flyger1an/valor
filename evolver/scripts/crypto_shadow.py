"""Crypto shadow — forward track record of PROMOTED crypto candidates. ZERO orders.

The crypto twin of fx_shadow: it forward-papers the candidates you /candidates -> Promote (the queue's
`approved`) by re-running each one's family backtest on the research-runner's cached datasets (no extra
OKX calls) and reporting the FORWARD-ONLY slice (trades dated after promotion). This produces the
per-candidate {fwd_sharpe, fwd_n} the forward-feedback loop (evolve/feedback.py) needs, so crypto gets
the same gate-learning FX has. shadow_runner still papers the liquidation basket separately; this is the
per-candidate forward track that closes the learning loop.

    python3 scripts/crypto_shadow.py            # one tick (cron)
    python3 scripts/crypto_shadow.py --loop 3600
    python3 scripts/crypto_shadow.py --status
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import pickle
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _l in ((ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []):
    if "=" in _l and not _l.strip().startswith("#"):
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from evolver.evolve import candidate_shadow as CS  # noqa: E402
from evolver.optimize.cross_sectional import run_cross_sectional as RXS  # noqa: E402
from evolver.optimize.funding_carry import run_funding_carry as RFCY  # noqa: E402
from evolver.optimize.funding_session import run_funding_session as RFS  # noqa: E402
from evolver.optimize.liquidation_reversion import run_liquidation_reversion as RLR  # noqa: E402
from evolver.optimize.oi_reversion import run_oi_reversion as ROI  # noqa: E402
from evolver.optimize.trend_following import run_trend as RT  # noqa: E402
from evolver.research import queue as Q  # noqa: E402

# family -> (data kind, backtest, fee_bps) — must match research_tick's CRYPTO families
FAM = {"liquidation": ("hourly_ohlc", RLR, 8.0), "liquidation_funding": ("hourly_fund", RLR, 8.0),
       "funding_session": ("hourly_fund", RFS, 6.0), "trend": ("daily", RT, 5.0),
       "cross_sectional": ("daily", RXS, 4.0), "xs_reversal": ("hourly_closes", RXS, 5.0),
       "oi_reversion": ("oi", ROI, 5.0), "funding_carry": ("funding_carry", RFCY, 5.0)}
QUEUE = os.getenv("EVOLVER_RESEARCH", str(ROOT / ".research_state.json"))
HOURLY = pathlib.Path(os.getenv("EVOLVER_RESEARCH_DATA", str(ROOT / ".okx_hourly_dataset.pkl")))
HOURLY_FUND = pathlib.Path(os.getenv("EVOLVER_RESEARCH_DATA_FUND", str(ROOT / ".okx_hourly_fund_dataset.pkl")))
DAILY = pathlib.Path(os.getenv("EVOLVER_RESEARCH_DAILY", str(ROOT / ".okx_daily_dataset.pkl")))
OI_DATA = pathlib.Path(os.getenv("EVOLVER_RESEARCH_OI", str(ROOT / ".okx_oi_dataset.pkl")))
FUND_CARRY = pathlib.Path(os.getenv("EVOLVER_RESEARCH_FUND_CARRY", str(ROOT / ".okx_fund_carry_dataset.pkl")))
STATE = pathlib.Path(os.getenv("EVOLVER_CRYPTO_SHADOW", str(ROOT / ".crypto_shadow_state.json")))
HEARTBEAT_URL = os.getenv("EVOLVER_CRYPTO_HEARTBEAT_URL") or os.getenv("EVOLVER_HEARTBEAT_URL")


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"ticks": 0, "started": _now(), "snapshot": []}


def save_state(s):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s))
    os.replace(tmp, STATE)


def notify(msg):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_ADMIN_CHAT_IDS", "").split(",")[0]
    if not (tok and chat):
        return
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage",
                               data=urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode(), timeout=10)
    except Exception:
        pass


def _ping():
    if HEARTBEAT_URL:
        try:
            urllib.request.urlopen(HEARTBEAT_URL, timeout=10)
        except Exception:
            pass


def _read_data(kinds):
    """Reuse the research-runner's cached datasets — no extra OKX calls. hourly_closes is derived."""
    out = {}
    if kinds & {"hourly_ohlc", "hourly_closes"}:
        ho = pickle.loads(HOURLY.read_bytes()) if HOURLY.exists() else {}
        if "hourly_ohlc" in kinds:
            out["hourly_ohlc"] = ho
        if "hourly_closes" in kinds:
            out["hourly_closes"] = {c: {t: v[3] for t, v in s.items()} for c, s in ho.items()}
    if "hourly_fund" in kinds and HOURLY_FUND.exists():
        out["hourly_fund"] = pickle.loads(HOURLY_FUND.read_bytes())
    if "daily" in kinds and DAILY.exists():
        out["daily"] = pickle.loads(DAILY.read_bytes())
    if "oi" in kinds and OI_DATA.exists():
        out["oi"] = pickle.loads(OI_DATA.read_bytes())
    if "funding_carry" in kinds and FUND_CARRY.exists():
        out["funding_carry"] = pickle.loads(FUND_CARRY.read_bytes())
    return out


def tick():
    s = load_state()
    s["ticks"] += 1
    approved = [c for c in Q.load(QUEUE).get("approved", []) if c.get("family") in FAM]
    if not approved:
        s["snapshot"] = []
        _ping()
        save_state(s)
        return f"[{_now()}] crypto-shadow tick {s['ticks']}: 0 promoted crypto candidates"
    data = _read_data({FAM[c["family"]][0] for c in approved})
    snap = CS.compute_forward(approved, FAM, data)
    s["snapshot"] = snap
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    if s.get("hb_day") != today:
        notify(f"🪙 crypto-shadow {today}: tracking {len(snap)} promoted candidate(s)\n" +
               "\n".join(f"  {x['id']} {x['family']}: fwd n {x['fwd_n']} sharpe {x['fwd_sharpe']:+.2f} "
                         f"ret {x['fwd_ret']:+.3f}" for x in snap))
        s["hb_day"] = today
    _ping()
    save_state(s)
    return (f"[{_now()}] crypto-shadow tick {s['ticks']}: " +
            " | ".join(f"{x['id']} fwd n{x['fwd_n']} sh{x['fwd_sharpe']:+.2f}" for x in snap))


def status():
    s = load_state()
    print(f"crypto-shadow (started {s['started']}, {s['ticks']} ticks)")
    if not s.get("snapshot"):
        print("  no promoted crypto candidates yet (forward track begins when you /candidates -> Promote)")
    for x in s.get("snapshot", []):
        print(f"  {x['id']} — {x['family']} (since {x['since']}): forward n {x['fwd_n']} | "
              f"sharpe {x['fwd_sharpe']:+.3f} | cum ret {x['fwd_ret']:+.4f}")
    print("  NOTE: zero orders. Forward-only = trades dated after promotion (data it never trained on).")


def main():
    a = sys.argv[1:]
    if a and a[0] == "--status":
        status()
    elif a and a[0] == "--loop":
        every = int(a[1]) if len(a) > 1 else 3600
        print(f"crypto-shadow loop: tick every {every}s")
        while True:
            try:
                print(tick())
            except Exception as e:
                print(f"[{_now()}] tick error: {e}")
            time.sleep(every)
    else:
        print(tick())


if __name__ == "__main__":
    main()
