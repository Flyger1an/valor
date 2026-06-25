"""Verify a research family end-to-end through the REAL gate: a planted edge should PASS, noise should
be REJECTED. Mirrors noise_gate_test/power but for the funding_session and xs_reversal families.

    python3 scripts/edge_verify.py
"""
from __future__ import annotations

import math
import random
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import research_tick as rt  # noqa: E402

BASE, STEP = 1_600_000_000_000, 3_600_000


def gen_fsession(seed, planted, n_coins=12, n_bars=2200, edge=0.018, target_phase=5, hold_edge=3):
    """5-tuple OHLC+funding. If planted: at `target_phase` of the 8h cycle, when funding is extreme,
    price drifts WITH the funding sign over the next few bars (the mechanical settlement flow)."""
    rng = random.Random(seed)
    data = {}
    for ci in range(n_coins):
        fund, f = [0.0] * n_bars, 0.0
        for i in range(n_bars):
            f = 0.97 * f + rng.gauss(0, 0.0005); fund[i] = f
        rets = [rng.gauss(0, 0.010) for _ in range(n_bars)]
        if planted:
            for i in range(n_bars - hold_edge - 1):
                if (BASE // STEP + i) % 8 == target_phase and abs(fund[i]) >= 0.0006:
                    d = 1 if fund[i] > 0 else -1
                    for k in range(1, hold_edge + 1):
                        rets[i + k] += d * (edge / hold_edge)
        p, s = 100.0 * math.exp(rng.gauss(0, 0.5)), {}
        for i in range(n_bars):
            o = p; p = p * math.exp(rets[i]); c = p; sm = abs(rng.gauss(0, 0.004))
            s[BASE + i * STEP] = (o, max(o, c) * (1 + sm), min(o, c) * (1 - sm), c, fund[i])
        data[f"S{ci}"] = s
    return data


def gen_xsrev(seed, planted, n_coins=14, n_bars=1600, k=0.30, L=12):
    """Hourly closes. If planted: each coin's idio return reverts its own trailing L-bar move -> recent
    cross-sectional winners underperform (short-term reversal). Dollar-neutral L/S captures it."""
    rng = random.Random(seed)
    data = {}
    for ci in range(n_coins):
        eps = [rng.gauss(0, 0.010) for _ in range(n_bars)]
        rets = eps[:]
        if planted:
            for t in range(L, n_bars):
                rets[t] = eps[t] - k * (sum(eps[t - L:t]) / L)
        p, s = 100.0 * math.exp(rng.gauss(0, 0.5)), {}
        for i in range(n_bars):
            p = p * math.exp(rets[i]); s[BASE + i * STEP] = p
        data[f"R{ci}"] = s
    return data


def check(name, gen, n=6):
    fam = next(f for f in rt.FAMILIES if f["name"] == name)
    pp = pn = 0
    for s in range(n):
        if rt.cycle(fam, gen(s, True))[1] is not None:
            pp += 1
        if rt.cycle(fam, gen(s + 500, False))[1] is not None:
            pn += 1
    print(f"{name:16}: planted-edge PASS {pp}/{n}  |  noise PASS {pn}/{n}")
    return pp, pn


def main():
    print("end-to-end gate verification (planted edge should pass; noise should not)\n")
    check("funding_session", gen_fsession)
    check("xs_reversal", gen_xsrev)
    print("\nwant: planted > 0 (gate can exploit a real edge), noise == 0 (rejects randomness)")


if __name__ == "__main__":
    main()
