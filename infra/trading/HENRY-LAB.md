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

## Promoting one candidate (declared, one shot)

Training on Oct 2021 to Oct 2026 produced no survivors. The best candidate was the daily 50-day trend
(long only, alts follow BTC): +120%, Sharpe 0.73 against a luck bar of 1.04. It is promoted for a
single test on 2017-21. The lessons file records that it missed the luck bar. If it fails, no second
candidate gets promoted onto the same holdout.

```bash
docker run --rm -u 0 --network none -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver \
  --entrypoint python "$BASE" -m evolver.trading.henry_lab promote --lessons /data/henry_lessons.json \
  --id "btc_filter=True|family=ma_trend|ma=50|side=long|tf=1d" --reason "best training candidate; declared single bet" \
  --out /data/henry_lessons_ma50.json
docker run --rm -u 0 --network none -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver \
  --entrypoint python "$BASE" -m evolver.trading.henry_lab test --data /data/henry_lab_2017_2021.json.gz \
  --lessons /data/henry_lessons_ma50.json --ledger /data/henry_test_ledger.json
```

# Funding carry lab (henry-carry-v1)

The trade: long spot plus short perp, equal notional. Funding is collected, the basis comes from daily
perp prices, and costs cover 4 legs. Capital is 1.5x notional, and legs are resized after a 30% move.
See `evolver/trading/henry_carry.py` for the rules. Two questions are judged separately:

- **Is the premium real?** Always-on carry, no luck bar (one hypothesis). It must be positive in at
  least 60% of blocks, and mean funding must be at least 3 standard errors above zero.
- **Does timing help?** 36 variants. Each must beat the luck bar from day-shuffled funding and also
  beat always-on carry.

Train on Oct 2021 to Oct 2026, then test once on 2017-21. Binance perps start in Sep 2019, so the
effective test era is Sep 2019 to Sep 2021. Every result also shows the cost of using Alpaca's 25 bps
spot fee. Execution needs a perp venue you can legally use, and exchange risk is not in any backtest.

```bash
docker run --rm -u 0 -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver --entrypoint python "$BASE" \
  -m evolver.trading.henry_desk_data --months 60 --perp --out /data/carry_train.json.gz
docker run --rm -u 0 -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver --entrypoint python "$BASE" \
  -m evolver.trading.henry_desk_data --months 50 --months-ago 60 --perp --out /data/carry_test.json.gz
docker run --rm -u 0 --network none -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver \
  --entrypoint python "$BASE" -m evolver.trading.henry_carry train --data /data/carry_train.json.gz --out /data/carry_lessons.json
docker run --rm -u 0 --network none -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver \
  --entrypoint python "$BASE" -m evolver.trading.henry_carry test --data /data/carry_test.json.gz \
  --lessons /data/carry_lessons.json --ledger /data/henry_test_ledger.json
```
