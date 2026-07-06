# Step 0 — free-data pre-test of the max-pain pin (2026-07-06)

**Question:** before paying for Deribit options history, does ~7 years of FREE data show any
structure worth buying the daily history to validate?

**Data ($0):** Tardis.dev serves the 1st of every month free — `scripts/tardis_free_snapshots.py`
stream-harvests 88 first-of-month Deribit `options_chain` files (2019-04 → 2026-07) into
`.tardis_monthly_oi.pkl` (~5–50 MB read per multi-GB file: stop after one full chain sweep).
Outcomes from free Binance daily-close dumps (`data.binance.vision`). Both coins present in all
88 months. Max-pain computed by the SAME `evolver.optimize.options_flow.max_pain` the live
family uses.

**Test (`scripts/pin_pretest.py`):** per month × coin, dominant expiry in a dte band → gap
g=(max-pain − underlying)/underlying → return r to that expiry. Hit rate (sign r == sign g) vs
50%, OLS beta of r on g, median |distance-to-pin| ratio.

## Results

| band | n | hit | beta (t) | dist ratio |
|---|---|---|---|---|
| 14–45d (monthly) | 172 | 52% (p .65) | −0.02 (−0.2) | 1.66 |
| 8–14d | 150 | 49% (p .74) | −0.27 (−1.1) | 2.92 |
| **1.5–8d (expiring weekly)** | **162** | **45% (p .21)** | **−0.42 (−2.64)** | 2.17 |

Per-coin at 1.5–8d: BTC beta −0.38 (t −1.84), ETH −0.44 (t −1.87) — same sign independently.

## Honest read

1. **The classic pin (pull toward max-pain): NOT visible at any horizon.** The monthly form is
   stone dead (beta ≈ 0 on n=172).
2. **The final-days band shows a suggestive ANTI-pin:** magnitude-weighted movement AWAY from
   the expiring weekly's max-pain. Caveats that keep this at "suggestive": BTC/ETH correlation
   (effective t ≈ 2, not 2.64), 3 bands examined (multiplicity), outcome uses daily closes vs
   08:00 UTC settlement (~16h slop on a 2–7d horizon), and a mundane rival mechanism — max-pain
   marks where OI accrued ≈ where price HAS been, so "away from pin" may be short-horizon
   momentum in an options costume. Distinguishing forced-flow repulsion from momentum requires
   conditioning on net dealer gamma / OI freshness — i.e. richer data.
3. **Implication for the live family:** `options_pin`'s space already allows `trade_dir = −1`;
   no code change needed. The forward-accumulating daily snapshots remain the honest validation
   path; this pre-test raises the prior that the gate's verdict will land on repel-or-nothing,
   not pull.
4. **Implication for the data ladder:** Step 0 did its job — the monthly signal isn't worth a
   subscription, but the final-days effect is exactly what daily history resolves (~104 weekly
   cycles/yr × 2 coins at full resolution with exact settlements, vs 12/yr here). The buy
   decision now has an empirical basis instead of a hunch. Recommended next rung if spending:
   ONE Tardis Options month (Solo ~$900, 4-month lookback ≈ 70 weekly-expiry observations at
   daily resolution) before any quarter/yearly commitment; verify billing-period lookback with
   Tardis sales first.

**Verdict: NOT an edge, NOT tradeable, NOT gate-validated — a $0 empirical basis for the next
data decision, which was the entire point of Step 0.**
