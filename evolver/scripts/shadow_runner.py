"""Shadow runner — forward PAPER validation of the liquidation-reversion basket. ZERO orders.

Each tick is self-contained and idempotent: load state -> pull live OKX 1h OHLC -> close any
positions whose hold elapsed -> scan newly-closed bars for liquidation wicks -> open paper
fades -> persist. Because state lives in a file (atomic write) and every tick re-derives from
it, the process can crash or restart between any two ticks and lose nothing. Run it hourly via
cron, or as a long-loop container — see the 24/7 notes in the response.

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
FEE_BPS = 8.0
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
    return {"open": [], "closed": [], "equity": CAPITAL, "last_bar": {}, "last_entry": {},
            "started": _now(), "ticks": 0}


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
    if HEARTBEAT_URL:                                            # external dead-man's-switch
        try:
            urllib.request.urlopen(HEARTBEAT_URL, timeout=10)
        except Exception:
            pass


def _atr(bars, i, w):
    s = 0.0
    for j in range(max(1, i - w), i):
        h, l, pc = bars[j][2], bars[j][3], bars[j - 1][4]
        s += max(h - l, abs(h - pc), abs(l - pc))
    return s / max(1, min(w, i - 1))


def tick():
    s = load_state()
    s["ticks"] += 1
    fee = 2 * FEE_BPS / 1e4
    feed = {}
    for c in UNIVERSE:
        try:
            o = okx_candles_ohlc(c, "1H", 300)
            feed[c] = sorted([(t, *o[t]) for t in o])     # (ts,o,h,l,c) ascending
        except Exception:
            pass
    if not feed:
        save_state(s)
        return "no live data"

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
        ret = pos["side"] * (mark / pos["entry_px"] - 1) - fee
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
                pos = {"id": f"{ts}-{ci}-{c}", "cfg": ci, "coin": c, "entry_ts": ts,
                       "entry_px": cl, "exit_ts": ts + cfg["hold_hours"] * 3_600_000,
                       "side": side, "weight": WEIGHT}
                s["open"].append(pos)
                s["last_entry"][key] = ts
                log({"event": "open", **pos})
                opened += 1
        if bars:
            s["last_bar"][c] = bars[-1][0]

    msg = (f"[{_now()}] tick {s['ticks']}: +{opened} opened, {closed_now} closed | "
           f"open {len(s['open'])} | equity ${s['equity']:,.0f} "
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
