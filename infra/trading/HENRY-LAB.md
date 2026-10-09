# Henry's research lab (henry-lab-v1)

Learn which setups work on one era, freeze the lessons, then test them once on an era the lab never saw.

- **Train:** October 2021 to October 2026 (the data already studied).
- **Test, once:** August 2017 to September 2021. That covers the 2017 bubble, the 2018 crash, 2019-20 and
  the 2021 bull. BTC and ETH have the full span, ADA joins in 2018, SHIB arrives in 2021, and WIF and SKY
  did not exist yet.

## Setup library (60 setups)

| Family | Idea | Variants |
|---|---|---|
| donchian_breakout | buy a 20/55-bar high, exit on a 10/20-bar low (mirror for shorts) | 4h, 1d |
| ma_trend | hold while price is above a rising 50/100/200-day average | 1d |
| momentum | hold the direction of the last 30/90 days | 1d |
| trend_pullback | in an uptrend (above the 200-day), buy an RSI(2) flush below 5/10, exit on strength | 4h, 1d |
| squeeze_breakout | after volatility compresses to its lowest fifth, trade the band break | 4h, 1d |
| fair_value_gap | in trend, enter the first retrace into a 3-candle gap that holds; stop beyond the gap, 2R target, 20-bar limit; gaps under 0.5 ATR are ignored | 4h, 1d |

Each family runs long-only or long/short (shorts are simulated perps with funding), with or without a
BTC trend filter for alts. Sizing targets 2.5% daily volatility per coin, capped at 1x (no leverage).
Every trade carries a 3 ATR catastrophe stop. Costs are 25 bps fee, 5 bps slippage and half the spread
per side.

## How a setup earns a place (decided before training)

- It was profitable in at least 60% of the 6-month blocks, and lost no more than 15% in its worst block.
- It had a profit factor of at least 1.2 over at least 20 trades.
- It beat the **luck bar**: the best Sharpe that any setup reached on shuffled copies of the same data,
  where real patterns are destroyed but volatility and overall drift remain.
- Up to three survivors are kept, one per family, combined with equal capital and frozen with a
  fingerprint.

## The one-shot test (gate declared before any test ran)

The test passes only if all of these hold:

- return above 0
- Sharpe of at least 0.5
- max drawdown of 35% or less, and below BTC buy-and-hold's drawdown
- at least half the calendar years profitable
- no year worse than -20%

The ledger records each test. A second test of the same lessons is flagged as spent.

## Controls

On a synthetic market with real regime cycles, the lab learns trend lessons. On pure noise it learns
nothing: zero survivors. Both controls are in the test suite.

## Run it on the droplet

```bash
git -C /opt/henry-desk fetch -q --depth 1 origin henry-desk-candidate && git -C /opt/henry-desk checkout -q -f FETCH_HEAD
BASE=$(docker ps --filter name=valor-experiment --format '{{.Image}}' | head -1)
# test era: Aug 2017 to Sep 2021 (network)
docker run --rm -u 0 -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver --entrypoint python "$BASE" \
  -m evolver.trading.henry_desk_data --months 50 --months-ago 60 --out /data/henry_lab_2017_2021.json.gz
# train on Oct 2021 to Oct 2026, freeze the lessons, then test once (offline)
docker run --rm -u 0 --network none -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver \
  -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python "$BASE" -m evolver.trading.henry_lab train \
  --data /data/henry_desk_5y_holdout.json.gz --data /data/henry_desk.json.gz --out /data/henry_lessons.json
docker run --rm -u 0 --network none -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver \
  -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python "$BASE" -m evolver.trading.henry_lab test \
  --data /data/henry_lab_2017_2021.json.gz --lessons /data/henry_lessons.json --ledger /data/henry_test_ledger.json
```
