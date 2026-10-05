# Prospective USD universe assessment — 5 October 2026

**Result: no additional pair passed the full predeclared admission criteria.** ADA/USD, SHIB/USD, SKY/USD and WIF/USD passed quote freshness during the measured windows, but failed execution availability and recent modeled-volume capacity. They are research candidates, not enabled trading instruments. The existing BTC/USD and ETH/USD configuration remains unchanged.

This report records an observed gate failure. It does not implement or claim a completed dynamic-universe expansion. No deployment, policy migration, service restart, order, credential change, subscription purchase or risk-limit change was performed for this scan.

## Observation and frozen criteria

Criteria were written before the first provider request at **2026-10-05 13:50:34.694644 UTC**. The [complete criteria](universe-evidence/2026-10-05-criteria.json) have SHA-256 `3f6daa23c32b5031c1b65d26223422ddcdefca3e60a8ecb0f52813dd07a6866f`.

All 36 active/tradable USD crypto pairs returned by the existing demo catalog were sampled through the same **Alpaca US (`us`)** quote and orderbook endpoints used for the study venue. The catalog was revalidated before observation. Catalog orderability is not proof that a broker would accept a particular order.

| Window | Start UTC | End UTC | Samples per pair | Largest sample interval |
|---|---|---|---:|---:|
| 1 | 2026-10-05 13:50:36 UTC | 2026-10-05 13:55:36 UTC | 30 | 10.120 s |
| 2 | 2026-10-05 13:57:36 UTC | 2026-10-05 14:02:36 UTC | 30 | 10.407 s |
| 3 | 2026-10-05 14:04:36 UTC | 2026-10-05 14:09:36 UTC | 30 | 10.141 s |

There were 90 samples per pair, or 3,240 quote observations and 3,240 orderbook observations. All 180 bulk quote/book requests returned HTTP 200. No missing, invalid, crossed or rewound quotes were observed. Each five-minute window was separated by two unsampled minutes. The evidence covers these sampled windows only; it does not establish long-term reliability or profitable edge.

An addition had to satisfy **every** window: at least 95% of sampled quotes aged 0–30 seconds; no sampled quote older than 45 seconds; no observed within-window provider update gap above 45 seconds; no sampled stale episode longer than 20 seconds; and no missing/invalid quote. At least 80% of samples had to jointly pass quote freshness, spread at most 20 basis points, book age at most 30 seconds, and at least $25 of displayed liquidity on **both** sides within five basis points of the best prices. Displayed depth is not a fill guarantee.

The catalog minimum quantity, quantity increment and price increment also had to permit a $10 minimum entry within the existing $25 position cap. History had to contain at least 100 valid closed five-minute bars from the same venue, with at least one of the latest 20 bars providing $10 of modeled capacity at the current 1% volume participation rule. USD stablecoins were excluded from directional-alpha additions; PAXG was separately labeled as a gold-backed token. Selection used no return ranking.

## Per-pair results

“Executable %” is the combined quote/spread/book/depth test, not a predicted fill rate. Percentage triplets correspond to windows 1 / 2 / 3. “Capacity bars” counts qualifying bars among the most recent 20 at the history cutoff, 2026-10-05 14:01:25 UTC. The [CSV](universe-evidence/2026-10-05-results.csv) includes exact failure codes and update-gap measurements.

| Pair | Fresh % | Executable % | Maximum quote age, s | Closed bars | Capacity bars | Freshness passed |
|---|---|---|---:|---:|---:|---|
| AAVE/USD | 100.0 / 66.7 / 96.7 | 66.7 / 30.0 / 40.0 | 97.36 | 571 | 0 | no |
| ADA/USD | 100.0 / 100.0 / 100.0 | 0.0 / 0.0 / 0.0 | 27.55 | 559 | 0 | yes |
| ARB/USD | 100.0 / 96.7 / 100.0 | 0.0 / 0.0 / 0.0 | 51.55 | 573 | 0 | no |
| AVAX/USD | 76.7 / 90.0 / 93.3 | 0.0 / 0.0 / 0.0 | 75.95 | 560 | 0 | no |
| BAT/USD | 33.3 / 20.0 / 66.7 | 0.0 / 0.0 / 0.0 | 292.07 | 564 | 0 | no |
| BCH/USD | 73.3 / 60.0 / 80.0 | 0.0 / 0.0 / 0.0 | 92.67 | 553 | 1 | no |
| BONK/USD | 100.0 / 83.3 / 100.0 | 0.0 / 0.0 / 0.0 | 45.15 | 571 | 0 | no |
| BTC/USD | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 | 6.51 | 572 | 2 | yes |
| CRV/USD | 83.3 / 83.3 / 93.3 | 43.3 / 63.3 / 66.7 | 69.76 | 570 | 0 | no |
| DOGE/USD | 70.0 / 80.0 / 90.0 | 0.0 / 0.0 / 0.0 | 80.68 | 523 | 0 | no |
| DOT/USD | 96.7 / 90.0 / 100.0 | 50.0 / 60.0 / 40.0 | 50.94 | 572 | 0 | no |
| ETH/USD | 96.7 / 70.0 / 93.3 | 96.7 / 70.0 / 93.3 | 83.16 | 469 | 1 | no |
| FIL/USD | 100.0 / 96.7 / 93.3 | 0.0 / 0.0 / 0.0 | 44.36 | 564 | 0 | no |
| GRT/USD | 66.7 / 86.7 / 83.3 | 0.0 / 0.0 / 0.0 | 126.35 | 553 | 0 | no |
| HYPE/USD | 90.0 / 100.0 / 93.3 | 0.0 / 0.0 / 0.0 | 58.52 | 547 | 0 | no |
| LDO/USD | 70.0 / 96.7 / 93.3 | 0.0 / 0.0 / 0.0 | 50.53 | 561 | 1 | no |
| LINK/USD | 56.7 / 86.7 / 76.7 | 0.0 / 30.0 / 40.0 | 113.49 | 562 | 0 | no |
| LTC/USD | 50.0 / 60.0 / 66.7 | 0.0 / 0.0 / 0.0 | 118.74 | 566 | 0 | no |
| ONDO/USD | 90.0 / 96.7 / 96.7 | 0.0 / 0.0 / 0.0 | 43.16 | 569 | 0 | no |
| PAXG/USD | 53.3 / 53.3 / 46.7 | 0.0 / 0.0 / 0.0 | 158.22 | 321 | 0 | no |
| PEPE/USD | 96.7 / 86.7 / 93.3 | 0.0 / 0.0 / 0.0 | 41.52 | 542 | 0 | no |
| POL/USD | 80.0 / 50.0 / 63.3 | 0.0 / 0.0 / 0.0 | 166.04 | 556 | 0 | no |
| RENDER/USD | 76.7 / 86.7 / 90.0 | 0.0 / 0.0 / 0.0 | 90.53 | 559 | 0 | no |
| SHIB/USD | 100.0 / 100.0 / 100.0 | 0.0 / 0.0 / 0.0 | 20.99 | 554 | 0 | yes |
| SKY/USD | 100.0 / 100.0 / 100.0 | 50.0 / 60.0 / 40.0 | 24.54 | 575 | 0 | yes |
| SOL/USD | 66.7 / 70.0 / 86.7 | 66.7 / 70.0 / 86.7 | 91.16 | 534 | 1 | no |
| SUSHI/USD | 63.3 / 73.3 / 73.3 | 40.0 / 53.3 / 26.7 | 86.43 | 557 | 0 | no |
| TRUMP/USD | 66.7 / 76.7 / 73.3 | 0.0 / 0.0 / 0.0 | 117.35 | 551 | 0 | no |
| UNI/USD | 90.0 / 96.7 / 93.3 | 36.7 / 43.3 / 33.3 | 45.94 | 568 | 0 | no |
| USDC/USD | 0.0 / 0.0 / 0.0 | 0.0 / 0.0 / 0.0 | 1364.50 | 32 | 8 | no |
| USDG/USD | 0.0 / 0.0 / 0.0 | 0.0 / 0.0 / 0.0 | 391057.65 | 0 | 0 | no |
| USDT/USD | 0.0 / 0.0 / 0.0 | 0.0 / 0.0 / 0.0 | 1468.99 | 13 | 5 | no |
| WIF/USD | 100.0 / 96.7 / 100.0 | 0.0 / 0.0 / 0.0 | 35.23 | 570 | 0 | yes |
| XRP/USD | 83.3 / 83.3 / 96.7 | 0.0 / 0.0 / 0.0 | 61.53 | 542 | 0 | no |
| XTZ/USD | 40.0 / 46.7 / 60.0 | 0.0 / 0.0 / 0.0 | 328.65 | 555 | 0 | no |
| YFI/USD | 30.0 / 83.3 / 43.3 | 10.0 / 30.0 / 16.7 | 136.94 | 558 | 0 | no |

BTC passed the complete gate and is already enabled. ETH had freshness of 96.7%, 70.0% and 93.3%, reaching 83.16 seconds of sampled age; its existing stale-entry controls remain necessary. The four new freshness candidates had no qualifying recent capacity bars. ADA, SHIB and WIF never met the combined execution check; SKY met it on only 50%, 60% and 40% of samples. No failed threshold was relaxed after seeing the results.

The bounded seven-day history request reached 20 pages after 48,553 bars and 25 symbols, without finishing pagination. A separate two-day request completed in seven pages, returning 18,198 bars for 35 symbols; USDG had no returned history. Both records were retained. This also exposes a concrete scaling limit in the current feed’s seven-day/20-page retrieval path.

Alpaca documents that crypto bars can include quote midpoint prices when no trade occurs, with zero volume. Consequently, a substantial bar history does not by itself provide the modeled fill capacity used by the virtual books. [Alpaca crypto data documentation](https://docs.alpaca.markets/us/docs/real-time-crypto-pricing-data)

## Same-venue streaming diagnostic

The existing credentials authenticated to the Alpaca US quote stream. A 36-symbol subscription was rejected with provider code 405 (symbol limit exceeded). A separate ten-symbol subscription was accepted and delivered 2,594 quote messages during a bounded two-minute diagnostic (2026-10-05 14:06:49 UTC to 2026-10-05 14:08:49 UTC). The exact subscription ceiling was not measured. No existing connection was displaced and the deployed REST feed was not changed. [Alpaca stream limits and errors](https://docs.alpaca.markets/us/docs/streaming-market-data)

The ten symbols were ADA, ARB, BONK, BTC, ETH, FIL, PEPE, SHIB, SKY and WIF, all quoted in USD. This short diagnostic is not part of the three-window selection evidence. Several symbols had no initial quote before the first sample; missing initial observations were retained. It demonstrates that a smaller stream subscription works, not that streaming makes the full universe consistently executable. No Kraken or other venue was mixed into the observations.

## Required engineering before a qualifying expansion

1. Bound or partition historical fetches and separate their latency from quote refresh. Pin venue identity explicitly in quote and bar provenance. Preserve the existing closed-bar revision checks. See [runtime.py](../../evolver/evolver/trading/runtime.py) and [alpaca.py](../../evolver/evolver/trading/alpaca.py).
2. Add a prospective, append-only universe/policy migration covering the source ledger, experiment identity, notifier source pin and dashboard pins. Preserve the original epoch, journal prefix, cash, fee settlements, evidence classifications, supervisor state and pending/protective orders. Startup must not silently substitute a policy. See [ledger.py](../../evolver/evolver/trading/ledger.py), [experiment.py](../../evolver/evolver/trading/experiment.py) and [telegram_alerts.py](../../evolver/evolver/trading/telegram_alerts.py).
3. Replace experiment-wide unused-symbol freshness gates with per-candidate entry checks plus fresh valuation for held inventory. Keep stale held-position marks and unavailable protective execution explicit. Generalize the two-asset Kelly estimator as a joint portfolio problem with a versioned evidence cohort; do not fabricate returns for new symbols or allocate independently across correlated assets. New assets with insufficient forward evidence remain cash. Henry must retain one cash-funded position and a deterministic ordering of currently eligible opportunities. See [shadow_sizing.py](../../evolver/evolver/trading/shadow_sizing.py).
4. Generalize catalog minima/increments, experiment capture, Telegram instrument validation and dashboard coverage, then test shared capital/exposure/loss budgets, stale-symbol isolation, deterministic selection, no leverage, cold starts, migrations without reset and BTC/ETH accounting regressions. Publish and deploy only an actually qualifying universe. The separate supervisor role/prompt issue is outside this pair-expansion work and remains unchanged.

## Verification and evidence

Ten deterministic offline evaluator tests passed, covering complete and insufficient windows, sampling gaps, crossed/missing/future prices, provider-clock freshness, the joint 80% threshold, two-sided depth, minimum increments, bar/volume/venue checks and exclusion of existing controls/stablecoins. Timestamp normalization was checked at fractional-second precisions from one through nine digits. No application behavior changed, so no new application-regression or CI pass is claimed.

At 14:11:31 UTC, all ten existing service container IDs, start times and restart counts matched the earlier baseline; all configured Docker healthchecks were healthy. Source policy and experiment identity, epoch and deadline were unchanged. The source ledger remained flat with no pending orders and the same historical order counts; the three virtual books had no fills and retained their original balances. Telegram remained healthy. The probes did not modify the running study.

Public evidence here contains criteria and aggregate market observations only. Account identifiers, credentials, private network/deployment details, raw journals and balances are excluded. Raw scan records and the evaluator are retained in the private working evidence directory.

| Evidence | SHA-256 |
|---|---|
| Frozen criteria | `3f6daa23c32b5031c1b65d26223422ddcdefca3e60a8ecb0f52813dd07a6866f` |
| Quote/orderbook scan | `2c5b9d6809f06a9de06fe48de73b543243f701779f4d166ab28f35367a842989` |
| Completed two-day bar probe | `7663c90858609841adfff4b93c46a64ac3bf4d8994fc4863d750dd9a5f496535` |
| Two-minute stream probe | `e608a2d02d35510e9748d6074bc5c10dd1154aa05e6f26fce372b7d838fb311b` |
