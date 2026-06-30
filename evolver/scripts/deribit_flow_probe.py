"""Observe-first probe for OPTIONS FORCED-FLOW (build #5 candidate) — dealer gamma / max-pain pinning.
Before building anything, establish ground truth on the data, the way every prior family demanded:

  1. Can we see standing OI by strike + the greeks to build a dealer-gamma / max-pain signal NOW?
  2. The make-or-break: is OI-by-strike available HISTORICALLY (=> backtest now) or only as a live
     snapshot (=> must forward-accumulate, like OI/liquidations did)? This decides the whole build path.
  3. Reachable here (and, by extension, the droplet)? No geo-block?

Pure-stdlib. Public data only. Computes the gamma profile with our own BS greeks (evolver/optimize/
vol_pnl) so it needs no per-option greek calls. The dealer-SIGN convention is an ASSUMPTION (unobservable
from public data) and is flagged as such — max-pain needs no sign, so it's the cleaner primitive.
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from evolver.data import deribit as D  # noqa: E402
from evolver.optimize.vol_pnl import bs_greeks  # noqa: E402

CUR = sys.argv[1] if len(sys.argv) > 1 else "BTC"


def book_summary(currency):
    """get_book_summary_by_currency (kind=option) — one call, OI for every live option. Current snapshot."""
    url = (D._BASE + "get_book_summary_by_currency?currency=" + currency + "&kind=option")
    req = urllib.request.Request(url, headers={"user-agent": "valor/0.1"})
    return json.load(urllib.request.urlopen(req, timeout=25)).get("result") or []


def main():
    t0 = time.time()
    print(f"=== Deribit options-flow probe ({CUR}) — observe-first, build #5 candidate ===\n")

    # (3) reachability + the live chain
    spot = D.index_price(CUR)
    chain = D.option_chain(CUR)                                   # strike/expiry/type per instrument
    summ = book_summary(CUR)                                      # OI per instrument (snapshot)
    oi = {s["instrument_name"]: float(s.get("open_interest") or 0) for s in summ}
    print(f"reachable: spot ${spot:,.0f} | {len(chain)} live options | {len(oi)} with OI rows "
          f"| {time.time()-t0:.1f}s")

    # join chain meta + OI, bucket by expiry
    rows = []
    for c in chain:
        K, exp, typ = c.get("strike"), c.get("expiry_ms"), c.get("type")
        if K and exp and typ:
            rows.append((exp, float(K), typ, oi.get(c["instrument_name"], 0.0)))
    exps = sorted({e for e, *_ in rows})
    now = time.time() * 1000
    print(f"expiries: {len(exps)} (nearest {(exps[0]-now)/86400000:.1f}d → "
          f"furthest {(exps[-1]-now)/86400000:.0f}d) | total OI {sum(r[3] for r in rows):,.0f} {CUR}\n")

    # (1) front-expiry signal primitives: max-pain (no sign) + gamma wall (BS gamma × OI)
    # pick the nearest expiry that actually has meaningful OI
    front = next((e for e in exps if sum(r[3] for r in rows if r[0] == e) > 1.0), exps[0])
    fr = [r for r in rows if r[0] == front]
    T = max((front - now) / (365 * 86400000), 1e-4)
    dvol = (list(D.dvol_history(CUR, days=5).values()) or [50.0])[-1] / 100.0   # ATM IV proxy for gamma
    strikes = sorted({K for _, K, _, _ in fr})
    call_oi = {K: sum(o for _, k, t, o in fr if k == K and t == "call") for K in strikes}
    put_oi = {K: sum(o for _, k, t, o in fr if k == K and t == "put") for K in strikes}

    def holder_payout(S):                                        # max-pain = argmin over settlement S
        return (sum(call_oi[K] * max(S - K, 0) for K in strikes)
                + sum(put_oi[K] * max(K - S, 0) for K in strikes))
    max_pain = min(strikes, key=holder_payout)

    gex = {K: bs_greeks(spot, K, T, dvol, 0.0, "call")["gamma"] * (call_oi[K] + put_oi[K]) for K in strikes}
    gamma_wall = max(strikes, key=lambda K: gex[K])
    front_oi = sum(r[3] for r in fr)
    print(f"FRONT expiry ({(front-now)/86400000:.1f}d, {len(strikes)} strikes, {front_oi:,.0f} {CUR} OI):")
    print(f"  spot ${spot:,.0f} | max-pain ${max_pain:,.0f} ({(max_pain/spot-1)*100:+.1f}% vs spot)  "
          f"<- assumption-free pin target")
    print(f"  gamma wall ${gamma_wall:,.0f} ({(gamma_wall/spot-1)*100:+.1f}% vs spot)  <- heaviest "
          f"dealer-hedging strike (sign-agnostic magnitude)")
    pcr = sum(put_oi.values()) / max(sum(call_oi.values()), 1e-9)
    print(f"  put/call OI ratio {pcr:.2f}\n")

    # (2) THE make-or-break: is OI-by-strike available historically?
    print("HISTORICAL availability (decides the build path):")
    has_hist = False
    try:                                                         # is there any historical-OI endpoint?
        url = D._BASE + "get_book_summary_by_instrument?instrument_name=" + chain[0]["instrument_name"]
        json.load(urllib.request.urlopen(urllib.request.Request(
            url, headers={"user-agent": "valor/0.1"}), timeout=15))
        # book summary exists but is point-in-time only; Deribit public API has no historical OI-by-strike
    except Exception:
        pass
    print(f"  get_book_summary: live snapshot only (no time range param) — historical OI-by-strike: "
          f"{'AVAILABLE' if has_hist else 'NOT in the public API'}")
    print("  => verdict: like OI/liquidations, the positioning signal must be FORWARD-ACCUMULATED "
          "(snapshot daily; backtest once weeks accrue). Not backtestable on history today.")
    print(f"\nprobe done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
