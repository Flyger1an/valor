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

# Robustness battery for the 50-day trend rule

The 50-day trend rule passed its one-shot test on 2017-21. Before it is wired in, the battery runs
it, frozen, on data it never saw, and judges it against a scorecard written before that data was
fetched (`SCORECARD` in `evolver/trading/henry_battery.py`).

The datasets:

- BTC before Binance (Bitstamp, 2011 to 2017).
- 30 unseen Binance coins, including collapsed ones (LUNA, FTT, WAVES, SRM, EOS, ICP and others),
  over Aug 2017 to Sep 2021 and over Oct 2021 to now. A reused ticker is split into separate listings.
- S&P 500, Nasdaq 100, gold, oil, TLT and EUR/USD from Stooq, from 1990.
- Stress tests on the 2017-21 coins: acting 1 and 2 days late, double costs, MAs from 30 to 100 days,
  and a 2,000-sample block bootstrap.

It passes only if all of these hold:

- drawdown is below buy-and-hold in every dataset
- it is profitable in at least 75% of datasets
- on collapsed coins it loses at most half of what holding lost
- a day of lag keeps at least 70% of the Sharpe
- it is still profitable at double costs
- at least 4 of 6 neighboring MAs agree
- the bootstrap shows Sharpe > 0 in at least 90% of resamples

```bash
docker run --rm -u 0 -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver --entrypoint python "$BASE" \
  -m evolver.trading.henry_battery fetch --out /data/battery.json.gz
docker run --rm -u 0 --network none -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver \
  --entrypoint python "$BASE" -m evolver.trading.henry_battery run --battery /data/battery.json.gz \
  --stress-data /data/henry_lab_2017_2021.json.gz
```

# Stocks and FX: does the rule travel? (henry_tradfi)

The battery's Stooq leg never loaded, so the rule is untested outside crypto. Before any stocks or
FX book is built, the same frozen rule runs on traditional markets, judged by scorecards written
before the data was fetched (`SCORECARDS` in `evolver/trading/henry_tradfi.py`).

Choices, declared up front:

- **Stocks**: long only. Equity ETFs only enter while SPY is above its 100-day average (the BTC
  filter's twin). Bonds, gold and commodities get no filter. Indexes and ETFs only, because free
  single-stock history covers survivors. Costs: no commission, 1 bp slippage, 2 bp spread.
- **FX**: long and short, no filter. Interest carry is not modeled (a known gap). Costs: 0.5 bp
  slippage, 1.5 bp spread. Crypto perp funding is switched off for these runs.
- **Data**: Yahoo's public chart API (adjusted closes for ETFs), ECB reference rates as an FX
  fallback.

Stocks pass only if: drawdown is below buy-and-hold in every dataset (6 world indexes from as far
back as they go, plus a 16-ETF basket), profitable in 75% of datasets, it loses less than holding
in at least 4 of the 6 named S&P crashes (1973-74, 1987, dot-com, 2008, COVID, 2022), and the ETF
basket survives a day of lag, double costs, 4 of 6 neighboring MAs and the bootstrap. Crashes the
data does not cover are reported as missing, never scored as losses.

FX passes only if: portfolio Sharpe is at least 0.3, at least half the pairs are profitable, max
drawdown is 25% or less, plus the same stress checks.

Whatever passes gets a live paper book (stocks via Alpaca paper, FX via the OANDA practice
executor). Whatever fails does not.

```bash
docker run --rm -u 0 -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver --entrypoint python "$BASE" \
  -m evolver.trading.henry_tradfi fetch --out /data/tradfi.json.gz
docker run --rm -u 0 --network none -v /opt/henry-desk:/src:ro -v /opt/henry-desk-data:/data -e PYTHONPATH=/src/evolver \
  --entrypoint python "$BASE" -m evolver.trading.henry_tradfi run --data /data/tradfi.json.gz
```

## Result, Oct 8 2026: stocks FAIL, FX FAIL. No stocks or FX book gets built on this rule.

**Stocks: FAIL** on one check out of 7. In the FTSE 100, the rule's drawdown (57.4%) was worse than
holding (52.6%). Everything else passed: it was profitable in 7 of 7 datasets and lost less than
holding in all 6 named S&P crashes (2008: -22% vs -57%; COVID: -4% vs -34%) and in the Nikkei
1990-92 bust (-3% vs -63%). It also cleared every stress test. But it earns roughly half of
buy-and-hold (ETF basket 4.4%/yr vs 10.7%/yr), and its Sharpe beats holding in only 3 of 7 datasets
(Nasdaq, Russell, Nikkei). In stocks it works as crash insurance that costs about half the return,
not as an edge. That matches the published work on moving-average timing for equity indexes.

**FX: FAIL** on all 7 checks. All 9 pairs lost money (portfolio -2.3%/yr, Sharpe -0.28, drawdown
64%), and every neighboring MA was negative. Without the intrabar stop fills (the lag and
double-cost paths) it sits around Sharpe 0, which suggests Yahoo's FX highs and lows trigger bad
stops. It is still nowhere near the gate either way. At a 50-day horizon, FX since the late 1990s
has no trend to ride.

Why crypto and not these: crypto's big moves are huge and its crashes are 80%+, so stepping aside
pays. Stocks drift up steadily, so time out of the market costs more than the crashes it avoids.
FX mostly mean-reverts at this horizon.

These datasets are now seen. Any new stocks or FX hypothesis needs a fresh holdout (other country
indexes, other periods) and a new declared scorecard.
