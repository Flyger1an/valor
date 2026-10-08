# Henry, research-desk edition (henry-desk-v2)

Henry as a small trading research desk: analysts write notes, a regime playbook proposes setups,
nothing trades without a written thesis that clears every check, a risk manager sizes it, and an
LLM reviewer may veto (live only). Candidate status: it goes live only after a PASS on real history.

## The desk (hourly, on closed 1h bars, with 4h and daily context)

| Analyst | What it judges |
|---|---|
| regime | bull / range / bear from 4h and daily trend plus 4h efficiency, with confidence |
| structure | 48h and 7d ranges, position in range, breakouts and breakdowns |
| volatility | ATR, realized-vol percentile, compression vs expansion |
| positioning | perp funding level and percentile: crowded longs or shorts |
| cross_asset | BTC regime and the coin's 3-day strength vs BTC |
| execution | round-trip cost: 2x fee + 2x slippage + spread |

## Playbooks

| Regime | Setups |
|---|---|
| bull | trend pullback to the 1h EMA20 (long, runner); 48h breakout on 1.5x volume (long, runner) |
| range | buy the bottom 20% of the 7d range; short the top 20% (target 60% across) |
| bear | short a rally into the 1h EMA20; 48h breakdown on volume (runner) |

Shorts are simulated perpetuals (fees both ways, funding every 8h, 1x collateral), labeled
`simulated_perp`. Executing them needs a perp venue; Alpaca spot cannot short.

## Bull core

When BTC's regime is bull, its daily trend is up and its 14-day daily efficiency is >= 0.3, the desk
holds half the book in BTC as a core long, separate from its two tactical slots. The core exits when
BTC's daily trend turns down or the regime turns bear (12% catastrophe stop), then waits 72h before
re-entering. Every replay also prints the result WITHOUT the core so its value is measured, not assumed.

## A thesis must pass every check

reward-to-risk >= 2, target >= 2.5x round-trip cost, stop >= 2.5x round-trip cost away,
structure must not oppose the trade (v1 replay: -0.69R when it disagreed, +0.31R when it agreed),
conviction >= 60 (weighted analyst agreement), stop and target on the correct sides, and for alts,
BTC not in the opposite regime. The LLM reviewer can then veto with a cited reason; it can never
create or resize a trade, and if it is unavailable it abstains and the desk decision stands.

## Risk manager

2.5% of equity at risk per trade, scaled 0.5x to 1.2x by conviction; max 2 positions; gross
exposure <= 1x equity (no leverage); half risk in a 15% drawdown; half size when the last 20 trades
average below 0R; a 72h stand-down when they average below -0.3R; $250 equity floor halts.

Management: breakeven at +1R, trail 2.5 ATR after +2R (runners trail past their target),
exit on a regime flip against the position, 72h time stop if not +0.5R.

## Proving it on real history

```bash
# 1. code (candidate branch; auto-deploy does not watch it)
[ -d /opt/henry-desk/.git ] && git -C /opt/henry-desk fetch -q --depth 1 origin henry-desk-candidate \
  && git -C /opt/henry-desk checkout -q -f FETCH_HEAD \
  || git clone -q --depth 1 --branch henry-desk-candidate https://github.com/Flyger1an/valor.git /opt/henry-desk
BASE=$(docker ps --filter name=valor-experiment --format '{{.Image}}' | head -1)
mkdir -p /opt/henry-desk-data

# 2. six months of hourly bars + funding from the Binance public archive (needs network, ~2 min)
docker run --rm -u 0 -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver \
  --entrypoint python "$BASE" -m evolver.trading.henry_desk_data --months 6 --out /data/henry_desk.json.gz

# 3. replay and grade (offline)
docker run --rm --network none -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data:ro -e PYTHONPATH=/src/evolver \
  -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python "$BASE" -m evolver.trading.henry_desk_replay \
  --data /data/henry_desk.json.gz --sweep --theses 2
```

The gate: return > 0, max drawdown <= 25%, profit factor >= 1.2, at least 30 trades, and neither
half of the period worse than -5%. Controls in the test suite: the desk must FAIL on trendless
noise; a desk that passes on noise is fooling itself.

## Out-of-sample holdout

v2's changes were designed from the April to October 2026 replay. Grading v2 on that same data would
be grading our own homework, so the deciding test is the six months BEFORE it, never looked at:

```bash
docker run --rm -u 0 -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver \
  --entrypoint python "$BASE" -m evolver.trading.henry_desk_data --months 6 --months-ago 6 --out /data/henry_desk_holdout.json.gz
docker run --rm --network none -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data:ro -e PYTHONPATH=/src/evolver \
  -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python "$BASE" -m evolver.trading.henry_desk_replay --data /data/henry_desk_holdout.json.gz
```

The desk is deployable only if it passes the gate on the holdout. Re-tuning after looking at the
holdout spends it; the next honest test would then need fresh data (the forward live run).

## What the replay cannot show

Binance prices stand in for Alpaca's; spreads are modeled; fills are at the next hour's open.
The news/macro analyst and the LLM reviewer only exist live (there is no honest historical news
archive to replay), so their value is measured forward: every veto is logged with what the
trade would have done.
