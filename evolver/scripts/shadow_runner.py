"""Shadow runner — forward PAPER validation of the liquidation-reversion basket. ZERO orders.

Each tick is self-contained and idempotent: load state -> pull live OKX 1h OHLC -> fill deferred
entries -> close any positions whose hold elapsed -> scan newly-closed bars for liquidation wicks
-> defer paper fades -> persist. Because state lives in a file (atomic write) and every tick
re-derives from it, the process can crash or restart between any two ticks and lose nothing.

EXECUTION FIDELITY (so the forward book isn't a fantasy): a wick signal is only known once its bar
CLOSES, so entries fill at the NEXT bar's OPEN (never the signal bar's close — that's the price that
defines the edge), and every trade pays taker fee + spread/impact on both legs plus a conservative
funding drag. This is still a paper mark, not a real fill — true fidelity needs the OKX-demo
executor (evolver.execution.okx_executor) — but it kills the biggest optimism the backtest carried.

    python3 scripts/shadow_runner.py            # one tick (for cron)
    python3 scripts/shadow_runner.py --loop 3600 # long-running: tick every hour
    python3 scripts/shadow_runner.py --status    # print the forward book, no trading
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

from evolver.data.okx import okx_candles_ohlc  # noqa: E402

CAPITAL = 100_000.0
FEE_BPS = 8.0                       # taker fee per side
# Execution frictions the BACKTEST omits — added here so the forward book is honest, not optimistic.
# A liquidation fade is a TAKER hitting a thin, fast, post-cascade book; spread+impact is the real
# cost, and it is largest exactly when this strategy fires. Tune via env once we see testnet fills.
SLIP_BPS = float(os.getenv("SHADOW_SLIP_BPS", "12"))           # spread/2 + market impact, per side
FUNDING_BPS_PER_8H = float(os.getenv("SHADOW_FUNDING_BPS_8H", "1.5"))  # pure DRAG over the hold
#   (modeled as always-a-cost, never a credit -> conservative; real per-coin funding is a refinement)
# CAVEAT (survivorship): currently-listed symbols only — coins delisted in-window are absent. The
# liquidation fade is a pooled event study so it's the least survivorship-sensitive family, but the
# bias is UP. Forward paper from here on is immune (it only ever sees live, currently-traded names).
UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "DOT", "LTC", "ADA",
            "NEAR", "ARB", "OP", "INJ", "SUI", "APT", "SEI", "ATOM", "FIL"]
BASKET = [{"wick_atr": w, "hold_hours": h, "body_max": 0.55, "cooldown_h": 5, "atr_window": a}
          for w in (3.5, 4.0, 4.5) for h in (6, 9, 12) for a in (40, 70)]
WEIGHT = 1.0 / len(BASKET)
STATE = pathlib.Path(os.getenv("EVOLVER_SHADOW", str(ROOT / ".shadow_state.json")))
LEDGER = pathlib.Path(os.getenv("EVOLVER_SHADOW_LEDGER", str(ROOT / "shadow_ledger.jsonl")))
# external dead-man's-switch: ping this each healthy tick (e.g. a healthchecks.io URL). If pings
# stop for the grace period YOU set there, that service alerts you — works even if this box dies.
HEARTBEAT_URL = os.getenv("EVOLVER_HEARTBEAT_URL")
STALE_SECS = int(os.getenv("EVOLVER_STALE_SECS", "7200"))   # catch-up alert if a tick gap exceeds this


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"open": [], "pending": [], "closed": [], "equity": CAPITAL, "last_bar": {},
            "last_entry": {}, "started": _now(), "ticks": 0}


def save_state(s):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s))
    os.replace(tmp, STATE)            # atomic — a crash mid-write can't corrupt the book


def log(rec):
    with LEDGER.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def notify(msg):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_ADMIN_CHAT_IDS", "").split(",")[0]
    if not (tok and chat):
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10)
    except Exception:
        pass


def _ping_alive():
    """Liveness ping to the external dead-man's-switch — fires EVERY tick because the PROCESS is
    alive, independent of whether OKX returned data. A transient OKX outage must NOT look like a
    dead box; if the process is genuinely dead it can't ping, so healthchecks still catches that."""
    if HEARTBEAT_URL:
        try:
            urllib.request.urlopen(HEARTBEAT_URL, timeout=10)
        except Exception:
            pass


def _watchdog(s, summary):
    """Liveness layer: catch-up alert on downtime, daily heartbeat, external ping."""
    nowep = time.time()
    prev = s.get("last_tick_epoch")
    if prev and nowep - prev > STALE_SECS:                       # we were down between ticks
        notify(f"⚠️ shadow runner BACK after ~{(nowep - prev) / 3600:.1f}h gap. {summary}")
    s["last_tick_epoch"] = nowep
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    if s.get("last_heartbeat_day") != today:                    # once-a-day "still alive"
        notify(f"💓 shadow heartbeat {today} — {summary}")
        s["last_heartbeat_day"] = today
    _ping_alive()                                               # external dead-man's-switch


def _atr(bars, i, w):
    s = 0.0
    for j in range(max(1, i - w), i):
        h, l, pc = bars[j][2], bars[j][3], bars[j - 1][4]
        s += max(h - l, abs(h - pc), abs(l - pc))
    return s / max(1, min(w, i - 1))


def _round_trip_cost(hold_hours):
    """Round-trip friction the backtest leaves out, as a return-fraction: taker fee + spread/impact
    on BOTH legs, plus a conservative funding drag over the hold (always a cost). This is what turns
    an optimistic 'mark at close' into something closer to what you'd actually net."""
    side = (FEE_BPS + SLIP_BPS) / 1e4
    return 2 * side + FUNDING_BPS_PER_8H * (hold_hours / 8.0) / 1e4


def tick():
    s = load_state()
    s["ticks"] += 1
    s.setdefault("pending", [])        # migrate older state files
    feed = {}
    for c in UNIVERSE:
        try:
            o = okx_candles_ohlc(c, "1H", 300)
            feed[c] = sorted([(t, *o[t]) for t in o])     # (ts,o,h,l,c) ascending
        except Exception:
            pass
    if not feed:
        s["data_fail"] = s.get("data_fail", 0) + 1
        if s["data_fail"] in (3, 12, 48):     # ~3/12/48h of no OKX data — SOFT warn (not a dead box)
            notify(f"⚠️ shadow-runner: no OKX data {s['data_fail']} ticks (likely OKX rate limit). "
                   f"Process alive; will recover.")
        _ping_alive()             # process is alive -> keep the dead-man's-switch GREEN through OKX blips
        save_state(s)
        return f"no live data (fail streak {s['data_fail']})"
    s["data_fail"] = 0            # OKX healthy again

    # 0) FILL deferred entries at the NEXT bar's OPEN. A wick signal is only known once its bar
    #    CLOSES, so the earliest realistic fill is the next bar's open — never the signal bar's own
    #    close (that price is what DEFINES the edge; assuming you trade at it is the cardinal lie).
    filled, still_pending = 0, []
    for p in s["pending"]:
        bars = feed.get(p["coin"])
        nb = next((b for b in bars if b[0] > p["signal_ts"]), None) if bars else None
        if nb is None:
            still_pending.append(p)
            continue
        cfg = BASKET[p["cfg"]]
        pos = {"id": p["id"], "cfg": p["cfg"], "coin": p["coin"], "entry_ts": nb[0],
               "entry_px": nb[1], "exit_ts": nb[0] + cfg["hold_hours"] * 3_600_000,
               "side": p["side"], "weight": WEIGHT}
        s["open"].append(pos)
        log({"event": "open", **pos})
        filled += 1
    s["pending"] = still_pending

    # 1) CLOSE positions whose hold elapsed (mark at first bar >= planned exit)
    still_open, closed_now = [], 0
    for pos in s["open"]:
        bars = feed.get(pos["coin"])
        mark = None
        if bars:
            for b in bars:
                if b[0] >= pos["exit_ts"]:
                    mark = b[4]
                    break
        if mark is None:
            still_open.append(pos)
            continue
        ret = pos["side"] * (mark / pos["entry_px"] - 1) - _round_trip_cost(BASKET[pos["cfg"]]["hold_hours"])
        pnl = pos["weight"] * CAPITAL * ret
        s["equity"] += pnl
        rec = {**pos, "exit_px": mark, "ret": round(ret, 5), "pnl_usd": round(pnl, 2), "closed": _now()}
        s["closed"].append(rec)
        log({"event": "close", **rec})
        closed_now += 1
    s["open"] = still_open

    # 2) DETECT new entries on bars newer than last processed (idempotent)
    opened = 0
    for c, bars in feed.items():
        last = s["last_bar"].get(c, 0)
        n = len(bars)
        for i in range(1, n):
            ts, o, h, l, cl = bars[i]
            if ts <= last:
                continue
            body, rng = abs(cl - o), h - l
            if rng <= 0 or cl <= 0:
                continue
            for ci, cfg in enumerate(BASKET):
                aw = cfg["atr_window"]
                if i < aw + 1:
                    continue
                atr = _atr(bars, i, aw)
                if atr <= 0 or body > cfg["body_max"] * rng:
                    continue
                side = 0
                if (min(o, cl) - l) / atr >= cfg["wick_atr"]:
                    side = 1
                elif (h - max(o, cl)) / atr >= cfg["wick_atr"]:
                    side = -1
                if not side:
                    continue
                key = f"{ci}:{c}"
                if ts - s["last_entry"].get(key, 0) < cfg["cooldown_h"] * 3_600_000:
                    continue
                eid = f"{ts}-{ci}-{c}"
                if i + 1 < n:                  # next bar already here -> fill at ITS open now
                    ets, eo = bars[i + 1][0], bars[i + 1][1]
                    pos = {"id": eid, "cfg": ci, "coin": c, "entry_ts": ets, "entry_px": eo,
                           "exit_ts": ets + cfg["hold_hours"] * 3_600_000, "side": side,
                           "weight": WEIGHT}
                    s["open"].append(pos)
                    log({"event": "open", **pos})
                    opened += 1
                else:                          # wick on the latest bar -> defer to next tick's open
                    s["pending"].append({"id": eid, "cfg": ci, "coin": c,
                                         "signal_ts": ts, "side": side})
                s["last_entry"][key] = ts
        if bars:
            s["last_bar"][c] = bars[-1][0]

    msg = (f"[{_now()}] tick {s['ticks']}: +{opened + filled} opened, {closed_now} closed | "
           f"open {len(s['open'])} pending {len(s['pending'])} | equity ${s['equity']:,.0f} "
           f"({(s['equity']/CAPITAL-1)*100:+.2f}%) | {len(s['closed'])} closed trades")
    _watchdog(s, msg)        # heartbeat / catch-up / external ping (only on a healthy data tick)
    save_state(s)
    if opened or closed_now:
        notify(msg)
    return msg


def status():
    s = load_state()
    rets = [c["ret"] for c in s["closed"]]
    wins = sum(1 for r in rets if r > 0)
    sh = 0.0
    if len(rets) > 1:
        m = sum(rets) / len(rets)
        sd = (sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        sh = m / sd if sd else 0.0
    print(f"shadow book (started {s['started']}, {s['ticks']} ticks)")
    print(f"  equity ${s['equity']:,.2f} ({(s['equity']/CAPITAL-1)*100:+.2f}%) | open {len(s['open'])} | "
          f"closed {len(s['closed'])}")
    if rets:
        print(f"  closed: win-rate {wins/len(rets):.0%} | sharpe/trade {sh:+.3f} | "
              f"mean {sum(rets)/len(rets)*100:+.3f}%")
    print("  NOTE: paper only, zero orders. Forward track record accrues here for significance.")


def main():
    a = sys.argv[1:]
    if a and a[0] == "--status":
        status()
    elif a and a[0] == "--loop":
        every = int(a[1]) if len(a) > 1 else 3600
        print(f"shadow loop: tick every {every}s (Ctrl-C to stop)")
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
