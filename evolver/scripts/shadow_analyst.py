"""Shadow v2 — forward shadow of the ANALYST LOOP's decisions. ZERO orders.

The roadmap's literal Phase 3: take live RV signals, run them through the SAME gpt-5-mini
analyst the loop uses, compute the exact spread order it WOULD place, mark it against LIVE OKX
prices forward, and — crucially — record what the heuristic paper sim ESTIMATED for the same
decision. Track shadow P&L (reality) vs sim P&L (the fantasy) and their DIVERGENCE: "the real
edge after the sim's lies." Places nothing.

EXECUTION FIDELITY: the order is DEFERRED and filled at the next bar's OPEN on both legs (the
decision is only known after the signal bar closes), and pays realistic spread/impact + a funding
drag — not the sim's optimistic 2bps. Still a paper mark; true fills need evolver.execution.

    python3 scripts/shadow_analyst.py            # one tick (cron)
    python3 scripts/shadow_analyst.py --loop 3600
    python3 scripts/shadow_analyst.py --status
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

from evolver.agents.analyst import build_fast_llm, decide, llm_decide  # noqa: E402
from evolver.config import DEFAULT_LIMITS, DEFAULT_STRATEGY  # noqa: E402
from evolver.core.signal import Signal  # noqa: E402
from evolver.core.sim import PerpPaperSim, TAKER_FEE_BPS  # noqa: E402
from evolver.data.okx import half_life_hours, mean, okx_candles_ohlc, std  # noqa: E402

CAP = DEFAULT_LIMITS.capital
PAIRS = ([(a, "ETH") for a in ("SOL", "BNB", "AVAX", "LINK", "DOT", "ARB", "OP", "INJ",
                               "NEAR", "ATOM", "LTC", "ADA", "DOGE", "XRP")]
         + [(a, "BTC") for a in ("ETH", "SOL", "BNB", "DOGE", "LTC", "XRP")])
ASSETS = sorted({a for p in PAIRS for a in p})
WINDOW, EXIT_Z, COOLDOWN_H = 168, 0.5, 12
RISK_PARAMS = {"new_pos_pct": 0.08, "new_leverage": 2.0}   # analyst clamps to limits anyway
# Honest frictions: realistic spread/impact (the sim's 2bps was optimistic) + a net perp-funding
# drag over the hold. Pairs are liquid majors so tighter than the liquidation basket, but not free.
SHADOW2_SLIP_BPS = float(os.getenv("SHADOW2_SLIP_BPS", "6"))            # spread/2 + impact, per leg/side
FUNDING_BPS_PER_8H = float(os.getenv("SHADOW2_FUNDING_BPS_8H", "1.5"))  # net funding drag (pure cost)
FEE_SLIP_RT = 4 * (TAKER_FEE_BPS + SHADOW2_SLIP_BPS) / 1e4              # 2 legs x (open + close)
STATE = pathlib.Path(os.getenv("EVOLVER_SHADOW2", str(ROOT / ".shadow_analyst_state.json")))
LEDGER = pathlib.Path(os.getenv("EVOLVER_SHADOW2_LEDGER", str(ROOT / "shadow_analyst_ledger.jsonl")))
HEARTBEAT_URL = os.getenv("EVOLVER_HEARTBEAT2_URL")
SIM = PerpPaperSim(CAP)
_LLM = None


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def load():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"open": [], "pending": [], "closed": [], "equity": CAP, "sim_equity": CAP,
            "last_entry": {}, "started": _now(), "ticks": 0, "last_tick_epoch": None}


def save(s):
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


def _ping2():
    """Liveness ping to the external dead-man's-switch — EVERY tick (process alive), independent of
    whether OKX returned data, so an OKX blip can't masquerade as a dead box."""
    if HEARTBEAT_URL:
        try:
            urllib.request.urlopen(HEARTBEAT_URL, timeout=10)
        except Exception:
            pass


def _signal(a, b, closes):
    """Build a live stat-arb-pair Signal from the A/B price ratio (z-score + OU half-life)."""
    ts = sorted(set(closes.get(a, {})) & set(closes.get(b, {})))
    if len(ts) < WINDOW + 2:
        return None, None
    ratio = [closes[a][t] / closes[b][t] for t in ts if closes[b][t]]
    win = ratio[-WINDOW:]
    m, sd = mean(win), std(win)
    if sd <= 0 or m <= 0:
        return None, None
    z = (ratio[-1] - m) / sd
    rr = [win[i] / win[i - 1] - 1 for i in range(1, len(win)) if win[i - 1]]
    vol = std(rr)
    d = {
        "signal_id": f"{a}{b}-{ts[-1]}", "timestamp": _now(), "type": "stat_arb_pair",
        "assets": (a, b), "zscore": round(z, 3), "spread_value": round(ratio[-1] / m - 1, 5),
        "expected_convergence_hours": round(half_life_hours(win, 1.0, 48.0), 1),
        "risk_score": round(min(max(vol * 25, 0.05), 0.95), 3),
        "confidence": round(min(max(abs(z) / 3, 0.1), 0.95), 3),
        "regime": "high_vol" if vol > 0.03 else "low_vol",
    }
    return Signal.from_dict(d), z


def tick():
    global _LLM
    s = load()
    s["ticks"] += 1
    s.setdefault("pending", [])        # migrate older state files
    journal = []                       # buffer ledger writes -> flush AFTER save(s) (no crash double-write)
    try:
        raw = {a: okx_candles_ohlc(a, "1H", 300) for a in ASSETS}   # ts -> (o,h,l,c)
    except Exception as e:
        _ping2()                       # process is alive; an OKX blip must not look like a dead box
        save(s)
        return f"[{_now()}] tick {s['ticks']}: live feed error ({e})"
    closes = {a: {t: raw[a][t][3] for t in raw[a]} for a in ASSETS}
    px = {a: (sorted(closes[a]) and closes[a][sorted(closes[a])[-1]]) for a in ASSETS}

    # 0) FILL deferred entries at the NEXT bar's OPEN for BOTH legs. The decision is known only once
    #    the signal bar CLOSES, so the realistic fill is the next open — not the signal-bar close.
    filled, still_pending = 0, []
    for p in s["pending"]:
        a, b = p["a"], p["b"]
        na = next((t for t in sorted(raw.get(a, {})) if t > p["signal_ts"]), None)
        nb = next((t for t in sorted(raw.get(b, {})) if t > p["signal_ts"]), None)
        if na is None or nb is None:
            still_pending.append(p)
            continue
        entry_ts = max(na, nb)
        pos = {"a": a, "b": b, "dir": p["dir"], "notional": p["notional"],
               "a_entry": raw[a][na][0], "b_entry": raw[b][nb][0], "entry_ts": entry_ts,
               "exit_ts": int(entry_ts + p["max_hold"] * 3.6e6), "entry_z": p["entry_z"],
               "sim_pnl_pct": p["sim_pnl_pct"], "rationale": p["rationale"]}
        s["open"].append(pos)
        journal.append({"event": "fill", **pos})
        filled += 1
    s["pending"] = still_pending

    # 1) manage open shadow positions — exit on convergence or max-hold, mark vs LIVE prices
    still, closed_n = [], 0
    for p in s["open"]:
        a, b = p["a"], p["b"]
        _, z = _signal(a, b, closes)
        converged = z is not None and abs(z) < EXIT_Z
        due = time.time() * 1000 >= p["exit_ts"]
        if not (converged or due) or not px[a] or not px[b]:
            still.append(p)
            continue
        a_ret, b_ret = px[a] / p["a_entry"] - 1, px[b] / p["b_entry"] - 1
        gross = p["dir"] * (a_ret - b_ret)
        hold_h = max(0.0, (time.time() * 1000 - p["entry_ts"]) / 3.6e6)
        net = gross - FEE_SLIP_RT - FUNDING_BPS_PER_8H * (hold_h / 8.0) / 1e4
        pnl = p["notional"] * net
        s["equity"] += pnl
        s["sim_equity"] += p["sim_pnl_pct"] * CAP
        rec = {**p, "a_exit": px[a], "b_exit": px[b], "shadow_pnl_pct": round(p["notional"] * net / CAP, 5),
               "divergence": round(p["notional"] * net / CAP - p["sim_pnl_pct"], 5),
               "converged": converged, "closed": _now()}
        s["closed"].append(rec)
        journal.append({"event": "close", **rec})
        closed_n += 1
    s["open"] = still

    # 2) for each pair: live signal -> SAME analyst -> shadow the order it WOULD place
    if _LLM is None:
        _LLM = build_fast_llm() or False
    busy = ({f"{p['a']}{p['b']}" for p in s["open"]}
            | {f"{p['a']}{p['b']}" for p in s["pending"]})
    opened = 0
    for a, b in PAIRS:
        key = f"{a}{b}"
        if key in busy or time.time() * 1000 - s["last_entry"].get(key, 0) < COOLDOWN_H * 3.6e6:
            continue
        sig, z = _signal(a, b, closes)
        if sig is None or abs(z) < DEFAULT_STRATEGY.get("min_abs_zscore", 1.0):
            continue   # Valor only emits meaningful signals; pre-gate before spending an LLM call
        dec = (llm_decide(sig, {"regime": sig.regime}, RISK_PARAMS, DEFAULT_LIMITS, _LLM, DEFAULT_STRATEGY)
               if _LLM else decide(sig, RISK_PARAMS, DEFAULT_LIMITS, DEFAULT_STRATEGY))
        if dec.get("action") in ("long", "short") and float(dec.get("size_usd", 0)) < CAP * 0.01:
            dec["size_usd"] = round(CAP * RISK_PARAMS["new_pos_pct"], 2)   # LLM sometimes returns a fraction, not $
        if dec.get("action") not in ("long", "short") or dec.get("size_usd", 0) <= 0:
            continue
        notional = float(dec["size_usd"]) * float(dec.get("leverage", 1.0))
        direction = 1.0 if dec["action"] == "long" else -1.0   # long_spread = long A / short B
        sim_fill = SIM.execute(sig, dec)                        # the heuristic ESTIMATE (fantasy side)
        # LLM exit object is free-form (may omit max_hold_hours) — fall back to the convergence horizon
        max_hold = float((dec.get("exit") or {}).get("max_hold_hours") or sig.expected_convergence_hours * 1.5)
        common = sorted(set(raw.get(a, {})) & set(raw.get(b, {})))
        if not common:
            continue
        pend = {"a": a, "b": b, "dir": direction, "notional": round(notional, 2),
                "signal_ts": common[-1], "max_hold": max_hold, "entry_z": round(z, 3),
                "sim_pnl_pct": round(sim_fill.pnl_pct, 5), "rationale": dec.get("rationale", "")[:120]}
        s["pending"].append(pend)                          # fills at next bar's open, next tick
        s["last_entry"][key] = int(time.time() * 1000)
        journal.append({"event": "intent", **pend})
        opened += 1

    nowep = time.time()
    if s.get("last_tick_epoch") and nowep - s["last_tick_epoch"] > 7200:
        notify(f"⚠️ shadow-analyst back after ~{(nowep - s['last_tick_epoch'])/3600:.1f}h gap")
    s["last_tick_epoch"] = nowep
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    div = (s["equity"] - s["sim_equity"])
    if s.get("hb_day") != today:
        notify(f"💓 shadow-analyst {today} — shadow ${s['equity']:,.0f} vs sim ${s['sim_equity']:,.0f} "
               f"(divergence ${div:+,.0f}) | open {len(s['open'])} | closed {len(s['closed'])}")
        s["hb_day"] = today
    _ping2()
    save(s)
    with LEDGER.open("a") as f:        # commit ledger only AFTER state -> the two stay consistent on a crash
        for r in journal:
            f.write(json.dumps(r) + "\n")
    msg = (f"[{_now()}] tick {s['ticks']}: +{opened} intents, {filled} filled, {closed_n} closed | "
           f"open {len(s['open'])} pending {len(s['pending'])} | "
           f"shadow ${s['equity']:,.0f} vs sim ${s['sim_equity']:,.0f} (div ${div:+,.0f})")
    if opened or closed_n:
        notify(msg)
    return msg


def status():
    s = load()
    cl = s["closed"]
    print(f"shadow-analyst (started {s['started']}, {s['ticks']} ticks)")
    print(f"  SHADOW (live marks) equity ${s['equity']:,.2f} ({(s['equity']/CAP-1)*100:+.2f}%)")
    print(f"  SIM    (heuristic)  equity ${s['sim_equity']:,.2f} ({(s['sim_equity']/CAP-1)*100:+.2f}%)")
    print(f"  DIVERGENCE ${s['equity']-s['sim_equity']:+,.2f}  <- reality minus the sim's estimate")
    print(f"  open {len(s['open'])} | closed {len(cl)}")
    if cl:
        wins = sum(1 for c in cl if c["shadow_pnl_pct"] > 0)
        print(f"  shadow win-rate {wins/len(cl):.0%} | mean divergence "
              f"{sum(c['divergence'] for c in cl)/len(cl)*100:+.3f}%/trade")
    print("  NOTE: zero orders. Measures sim-vs-reality gap on the analyst's live decisions.")


def main():
    a = sys.argv[1:]
    if a and a[0] == "--status":
        status()
    elif a and a[0] == "--loop":
        every = int(a[1]) if len(a) > 1 else 3600
        print(f"shadow-analyst loop: tick every {every}s")
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
