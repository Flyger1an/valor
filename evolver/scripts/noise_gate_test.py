"""Noise-in -> candidates-out: the decisive test of whether the research gate is fit for purpose.

Feeds PROVABLY zero-edge data (independent random walks; wicks are cosmetic noise uncorrelated with
forward returns) through the REAL research_tick.cycle() and counts how often a candidate clears the
gate. On pure noise the ideal surface rate is ~0; anything materially above the nominal false-positive
rate means the gate leaks. Run before and after gate fixes to measure the change.

    python3 scripts/noise_gate_test.py liquidation 40
    python3 scripts/noise_gate_test.py trend 30
"""
from __future__ import annotations

import math
import random
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import research_tick as rt  # noqa: E402


def gen_noise(n_coins: int, n_bars: int, seed: int, ohlc: bool, hourly: bool):
    """Independent geometric random walks (zero drift). For OHLC, high/low are random wicks that do
    NOT depend on the path -> the wick signal carries zero forward information. Guaranteed no edge."""
    base = 1_600_000_000_000
    step = 3_600_000 if hourly else 86_400_000
    rng = random.Random(seed)
    data = {}
    for ci in range(n_coins):
        price = 100.0 * math.exp(rng.gauss(0, 0.5))
        series = {}
        for i in range(n_bars):
            nxt = price * math.exp(rng.gauss(0, 0.012))
            if ohlc:
                o, c = price, nxt
                # fat-tailed wicks: ~8% of bars are "cascade" spikes (4-11% intrabar = several ATR),
                # on a random side, with a small body. The wick is purely COSMETIC — c is the random-
                # walk close, so forward returns are independent of the wick -> provably zero edge.
                big = rng.uniform(0.04, 0.11) if rng.random() < 0.08 else abs(rng.gauss(0, 0.012))
                small = abs(rng.gauss(0, 0.006))
                if rng.random() < 0.5:
                    l = min(o, c) * (1 - big); h = max(o, c) * (1 + small)
                else:
                    h = max(o, c) * (1 + big); l = min(o, c) * (1 - small)
                series[base + i * step] = (o, h, l, c)
            else:
                series[base + i * step] = nxt
            price = nxt
        data[f"N{ci}"] = series
    return data


def gen_edge(n_coins: int, n_bars: int, seed: int, edge: float, hold_edge: int = 8,
             vol: float = 0.010, spike: float = 0.12):
    """Like gen_noise but with a REAL liquidation-reversion edge injected: after a big wick, the next
    `hold_edge` bars drift in the FADE direction (down-wick -> up-drift) summing to `edge` gross. The
    wick still defines the signal; now fading it genuinely pays. Tests the OPPOSITE failure mode of
    gen_noise: can a true edge clear the (now strict + honest-cost) gate, or is it a false-negative trap?"""
    base, step = 1_600_000_000_000, 3_600_000
    rng = random.Random(seed)
    data = {}
    for ci in range(n_coins):
        rets = [rng.gauss(0, vol) for _ in range(n_bars)]
        wick = {}
        for i in range(120, n_bars - hold_edge - 2):
            if rng.random() < spike:
                d = 1 if rng.random() < 0.5 else -1          # +1 = down-wick (fade long), -1 = up-wick
                wick[i] = d
                for k in range(1, hold_edge + 1):
                    rets[i + k] += d * (edge / hold_edge)    # reversion the fade captures
        p = 100.0 * math.exp(rng.gauss(0, 0.5))
        px = []
        for i in range(n_bars):
            nx = p * math.exp(rets[i]); px.append((p, nx)); p = nx
        series = {}
        for i in range(n_bars):
            o, c = px[i]; d = wick.get(i, 0); sm = abs(rng.gauss(0, 0.006))
            if d == 1:
                big = rng.uniform(0.04, 0.11); l, h = min(o, c) * (1 - big), max(o, c) * (1 + sm)
            elif d == -1:
                big = rng.uniform(0.04, 0.11); h, l = max(o, c) * (1 + big), min(o, c) * (1 - sm)
            else:
                w = abs(rng.gauss(0, vol))
                if rng.random() < 0.5:
                    l, h = min(o, c) * (1 - w), max(o, c) * (1 + sm)
                else:
                    h, l = max(o, c) * (1 + w), min(o, c) * (1 - sm)
            series[base + i * step] = (o, h, l, c)
        data[f"E{ci}"] = series
    return data


def power_test(n_cycles: int, edge: float):
    fam = next(f for f in rt.FAMILIES if f["name"] == "liquidation")
    passed = 0
    osrs, dhos = [], []
    print(f"power test: {n_cycles} cycles, REAL edge ~{edge*1e4:.0f}bps gross/event "
          f"(~{edge*1e4-41:.0f}bps net after the honest ~41bps cost)\n")
    for t in range(n_cycles):
        data = gen_edge(20, 1800, seed=5000 + t, edge=edge)
        summ, cand = rt.cycle(fam, data)
        passed += cand is not None
        m = _RX.search(summ)
        if m:
            osrs.append(float(m.group(1))); dhos.append(float(m.group(3)))
        print(f"  [{t+1:>2}] {summ}{'  <-- PASSED' if cand else ''}")
    print("\n" + "=" * 70)
    print(f"GATE PASSED A REAL EDGE: {passed}/{n_cycles}  ({passed/n_cycles:.0%})")
    if osrs:
        print(f"holdout OOS Sharpe: mean {sum(osrs)/len(osrs):+.2f} | DSR mean {sum(dhos)/len(dhos):.2f}")
    print("If this is ~0, the gate is TOO strict (rejects real edges) — a false-negative trap.")


_RX = re.compile(r"OOS ([+\-][\d.]+) \(p ([\d.]+), DSR ([\-\d.]+), n (\d+)\).*?pbo (\S+)")


def rolling_test(n_cycles: int):
    """Faithful end-to-end test of the red-team's core claim: drive the REAL tick() over a SLIDING
    window of zero-edge data (each cycle adds a couple of bars + trims the front, so consecutive
    cycles share ~99% of their data, exactly like the hourly live refresh) with the CONFIRM streak
    live, and count how many candidates reach the human queue. Correlated data + a never-reset
    streak is the mechanism by which a single lucky region surfaces repeatedly."""
    import json
    import tempfile
    fam = next(f for f in rt.FAMILIES if f["name"] == "liquidation")
    n_coins, target, slide = 12, 1500, 2
    total = target + slide * n_cycles + 60
    rng = random.Random(42)
    full = {}
    for ci in range(n_coins):
        price = 100.0 * math.exp(rng.gauss(0, 0.5))
        bars = []
        for _ in range(total):
            nxt = price * math.exp(rng.gauss(0, 0.012))
            o, c = price, nxt
            big = rng.uniform(0.04, 0.11) if rng.random() < 0.08 else abs(rng.gauss(0, 0.012))
            small = abs(rng.gauss(0, 0.006))
            if rng.random() < 0.5:
                l, h = min(o, c) * (1 - big), max(o, c) * (1 + small)
            else:
                h, l = max(o, c) * (1 + big), min(o, c) * (1 - small)
            bars.append((o, h, l, c))
            price = nxt
        full[f"N{ci}"] = bars
    base, step, ctr = 1_600_000_000_000, 3_600_000, {"k": 0}

    def refresh():
        off = ctr["k"] * slide
        ctr["k"] += 1
        return {co: {base + (off + i) * step: full[co][off + i] for i in range(target)} for co in full}

    tmp = pathlib.Path(tempfile.mkdtemp())
    rt.Q.STATE = tmp / "state.json"
    rt.LEDGER = tmp / "ledger.jsonl"
    rt.notify = lambda *a, **k: None
    liq = dict(fam); liq["refresh"] = refresh; liq["min_cov"] = 1
    rt.FAMILIES = [liq]

    gate_passes = first_surface = 0
    print(f"rolling test: {n_cycles} cycles, sliding window (target {target}, +{slide} bars/cycle = "
          f"{100*(1-slide/target):.1f}% shared) on zero-edge data\n")
    for k in range(n_cycles):
        msg = rt.tick()
        led = [json.loads(x) for x in rt.LEDGER.read_text().splitlines()] if rt.LEDGER.exists() else []
        if led and led[-1].get("surfaced"):
            gate_passes += 1
        pend = rt.Q.load()["pending"]
        if pend and not first_surface:
            first_surface = k + 1
        if (k + 1) % 10 == 0 or pend:
            print(f"  cycle {k+1:>3}: gate-passes so far {gate_passes} | queue {len(pend)} | {msg.split('—')[-1].strip()[:60]}")
    st = rt.Q.load()
    streaks = st.get("streaks", {})
    print("\n" + "=" * 70)
    print(f"GATE CLEARED (per-cycle) {gate_passes}/{n_cycles} times on rolling noise")
    print(f"REACHED THE HUMAN QUEUE: {len(st['pending'])} candidates "
          f"(first at cycle {first_surface or '—'})")
    print(f"max region streak (CONFIRM bar={rt.CONFIRM}): {max(streaks.values()) if streaks else 0} "
          f"across {len(streaks)} regions  <- streaks never reset is the defeat")
    print("Ideal: 0 reach the queue on zero-edge data.")


def main():
    if sys.argv[1:2] == ["rolling"]:
        rolling_test(int(sys.argv[2]) if len(sys.argv) > 2 else 80)
        return
    if sys.argv[1:2] == ["power"]:
        power_test(int(sys.argv[2]) if len(sys.argv) > 2 else 20,
                   float(sys.argv[3]) if len(sys.argv) > 3 else 0.012)
        return
    fam_name = sys.argv[1] if len(sys.argv) > 1 else "liquidation"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    fam = next(f for f in rt.FAMILIES if f["name"] == fam_name)
    is_liq = fam_name == "liquidation"
    n_coins, n_bars = (12, 1800) if is_liq else (12, 1000)

    surfaced = 0
    op_pass = dho_pass = o2_pass = 0
    osrs = []
    print(f"noise gate test: {fam_name}, {n} cycles on zero-edge data "
          f"({n_coins} coins x {n_bars} {'hourly' if is_liq else 'daily'} bars)\n")
    for t in range(n):
        data = gen_noise(n_coins, n_bars, seed=1000 + t, ohlc=is_liq, hourly=is_liq)
        summ, cand = rt.cycle(fam, data)
        surfaced += cand is not None
        m = _RX.search(summ)
        if m:
            osr, op, dho = float(m.group(1)), float(m.group(2)), float(m.group(3))
            osrs.append(osr)
            op_pass += op < 0.05
            dho_pass += dho > 0.95
        flag = "  <-- SURFACED" if cand else ""
        print(f"  [{t+1:>2}] {summ}{flag}")

    print("\n" + "=" * 70)
    print(f"SURFACED A CANDIDATE: {surfaced}/{n}  ({surfaced/n:.0%})  on PURE NOISE")
    if osrs:
        print(f"sub-gate pass rates (of {n}): bootstrap p<0.05 = {op_pass}/{n} "
              f"({op_pass/n:.0%}) | DSR>0.95 = {dho_pass}/{n} ({dho_pass/n:.0%})")
        print(f"holdout OOS Sharpe on noise: mean {sum(osrs)/len(osrs):+.3f}, "
              f"max {max(osrs):+.3f} (should hover ~0)")
    print("Ideal surface rate on zero-edge data is ~0. Higher = the gate leaks false positives.")


if __name__ == "__main__":
    main()
