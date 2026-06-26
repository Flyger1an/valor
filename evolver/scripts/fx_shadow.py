"""FX shadow — forward track record of PROMOTED FX candidates. ZERO orders.

The FX twin of shadow_runner, adapted to FX's daily/session families. Rather than a tick-by-tick
position book (crypto's liquidation events), each tick re-runs each promoted candidate's family
backtest on the live rolling OANDA window and reports the FORWARD-ONLY slice — trades dated AFTER the
human promotion. That is the honest "does this edge hold on data it never trained on". Reads the FX
queue's `approved` list (whatever you /fxcandidates → Promote); marks nothing, places nothing.

    python3 scripts/fx_shadow.py            # one tick (cron)
    python3 scripts/fx_shadow.py --loop 3600
    python3 scripts/fx_shadow.py --status
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
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

from evolver.data.fx import fx_candles_history, fx_closes_history  # noqa: E402
from evolver.evolve import candidate_shadow as CS  # noqa: E402
from evolver.optimize.cross_sectional import run_cross_sectional as RXS  # noqa: E402
from evolver.optimize.fx_carry import run_fx_carry as RFC  # noqa: E402
from evolver.optimize.fx_session import run_fx_session as RFXS  # noqa: E402
from evolver.optimize.trend_following import run_trend as RT  # noqa: E402
from evolver.research import queue as Q  # noqa: E402

FX_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF", "NZD_USD",
            "EUR_JPY", "GBP_JPY", "EUR_GBP", "AUD_JPY", "EUR_AUD"]
# family -> (data kind, backtest, fee_bps) — must match research_tick's FX families
FAM = {"fx_trend": ("daily", RT, 1.0), "fx_xsection": ("daily", RXS, 1.0),
       "fx_carry": ("daily", RFC, 1.0), "fx_session": ("hourly", RFXS, 0.5)}
FX_STATE = os.getenv("EVOLVER_FX_RESEARCH", str(ROOT / ".fx_research_state.json"))
STATE = pathlib.Path(os.getenv("EVOLVER_FX_SHADOW", str(ROOT / ".fx_shadow_state.json")))
HEARTBEAT_URL = os.getenv("EVOLVER_FX_HEARTBEAT_URL") or os.getenv("EVOLVER_HEARTBEAT_URL")


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


def tick():
    s = load_state()
    s["ticks"] += 1
    approved = [c for c in Q.load(FX_STATE).get("approved", []) if c.get("family") in FAM]
    if not approved:
        s["snapshot"] = []
        _ping()
        save_state(s)
        return f"[{_now()}] fx-shadow tick {s['ticks']}: 0 promoted FX candidates (nothing to track yet)"

    need = {FAM[c["family"]][0] for c in approved}
    daily = {p: fx_closes_history(p, "D", 900) for p in FX_PAIRS} if "daily" in need else {}
    hourly = {p: fx_candles_history(p, "H1", 3000) for p in FX_PAIRS} if "hourly" in need else {}

    snap = CS.compute_forward(approved, FAM, {"daily": daily, "hourly": hourly})
    s["snapshot"] = snap
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    if s.get("hb_day") != today:                              # once-a-day alive ping
        notify(f"💱 fx-shadow {today}: tracking {len(snap)} promoted FX candidate(s)\n" +
               "\n".join(f"  {x['id']} {x['family']}: fwd n {x['fwd_n']} sharpe {x['fwd_sharpe']:+.2f} "
                         f"ret {x['fwd_ret']:+.3f}" for x in snap))
        s["hb_day"] = today
    _ping()
    save_state(s)
    return (f"[{_now()}] fx-shadow tick {s['ticks']}: " +
            " | ".join(f"{x['id']} fwd n{x['fwd_n']} sh{x['fwd_sharpe']:+.2f}" for x in snap))


def status():
    s = load_state()
    print(f"fx-shadow (started {s['started']}, {s['ticks']} ticks)")
    if not s.get("snapshot"):
        print("  no promoted FX candidates yet (forward track begins when you /fxcandidates -> Promote)")
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
        print(f"fx-shadow loop: tick every {every}s")
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
