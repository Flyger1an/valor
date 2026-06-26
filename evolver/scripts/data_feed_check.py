"""One-command smoke test for the NEW structural-edge data feeds — does OKX actually return usable
data for oi_reversion (#1), funding_carry (#2), liquidation_print (#3)? Run it anywhere with OKX
reachability (your box, or on the droplet: `docker compose run --rm research-runner python
scripts/data_feed_check.py`). Verdict per feed: OK / THIN / EMPTY / ERROR. No auth, no orders."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from evolver.data.okx import (daily_funding, okx_funding_history,  # noqa: E402
                              okx_intraday_closes, okx_liquidations, okx_oi_history)

COINS = ["BTC", "ETH", "SOL"]


def verdict(n, thin):
    return "EMPTY" if n == 0 else ("THIN" if n < thin else "OK")


def check(label, fn, thin):
    try:
        d = fn()
    except Exception as e:
        return f"  {label:34s} ERROR  {type(e).__name__}: {str(e)[:60]}"
    n = len(d)
    sample = ""
    if d:
        k = sorted(d)[-1]
        sample = f" | latest {k}: {d[k]}"
    return f"  {label:34s} {verdict(n, thin):6s} {n:>5d} points{sample}"


def main():
    print("=== OKX structural-feed check (no auth, no orders) ===\n")
    print("open interest (oi_reversion #1) — rubik feed, expect SHORT history:")
    for c in COINS:
        print(check(f"OI {c} 1D", lambda c=c: okx_oi_history(c, "1D"), 60))
        print(check(f"OI {c} 1H", lambda c=c: okx_oi_history(c, "1H"), 200))
    print("\nfunding (funding_carry #2) — expect FULL history:")
    for c in COINS:
        print(check(f"funding {c} (daily-summed)",
                    lambda c=c: daily_funding(okx_funding_history(f"{c}-USDT-SWAP", 200)), 150))
    print("\nliquidation prints (liquidation_print #3) — recent-only, accumulates:")
    for c in COINS:
        print(check(f"liq {c}-USDT", lambda c=c: okx_liquidations(f"{c}-USDT"), 24))
    print("\ncontrol — hourly closes (known-good):")
    print(check("closes BTC 1H", lambda: okx_intraday_closes("BTC", "1H", 300), 200))
    print("\nReading: OK = usable now; THIN = works but needs to accumulate; EMPTY/ERROR = feed "
          "unavailable here (a vendor feed is the fix for that family).")


if __name__ == "__main__":
    main()
