# Henry, live (paper): the 50-day trend rule (henry-trend-v1)

Henry now trades the one rule that held up in every test:

- **Lab one-shot (2017-21): PASS.** +568%, Sharpe 1.64, max drawdown 26%, against BTC hold at
  +923%, Sharpe 1.12, max drawdown 83%.
- **Robustness battery: PASS on every check.**
  - Bitstamp BTC 2011-17: Sharpe 1.99 vs 1.72 for holding, max drawdown 38% vs 85%.
  - 30 unseen coins in 2017-21: Sharpe 1.39 vs 1.08, max drawdown 28% vs 92%.
  - 30 unseen coins in 2021-now: +36% vs -67% for holding.
  - On collapsed coins it lost 12% of what holding lost.
  - Still works with a day of lag, at double costs, with all 6 neighboring moving averages, and in
    100% of bootstrap resamples.

## The rule (the lab's code, run unchanged)

- A daily decision at the UTC close. Daily bars come from the feed's closed 5-minute bars, after a
  ~400-day seed.
- Hold a coin while it closes above a rising 50-day average, and exit when that breaks.
- Alts only enter while BTC is above its own 100-day average.
- One equal sleeve per coin, sized to a 2.5% daily-volatility target, never levered. A 3 ATR
  protective stop is watched on live quotes.
- Paper fills at the next quote, with 5 bps adverse slippage and 25 bps fees.

## What to expect

It makes few trades and stays flat for long stretches. It lags buy-and-hold in strong bull runs. Its
job is the crash: 2014, 2018, 2022. Since 2021 its Sharpe has been about 0.4, so expect modest
returns, not a moonshot.

## Live scorecard (declared before the first trade)

It is reviewed monthly, against equal-weight hold and BTC hold of the same coins over the same days.

| Check | Healthy | Action |
|---|---|---|
| Max drawdown | below 35% | at 35%: automatic liquidation and halt (kill switch) |
| Drawdown vs holding | smaller than equal-weight hold's | if worse for 3 straight months: review |
| Shadow agreement | live decisions match the Binance replay | 3 days of disagreement in a row: flagged for review |
| Daily bars | real bars | flat days, from missed 5m data, are repaired by the 00:20 UTC seed refresh |

No rule changes are made on live results alone. Any change goes back through the lab and gets a new
journal.

## Operate

```bash
# one-time install (retires v4, keeps its data)
git clone -q --depth 1 --branch henry-desk-candidate https://github.com/Flyger1an/valor.git /opt/valor-henry-trend/src 2>/dev/null || true
sh /opt/valor-henry-trend/src/infra/trading/install-henry-trend.sh

# scoreboard
docker exec valor-henry-trend python -m evolver.trading.henry_trend_runner report --root /henry --policy /config/henry-trend-policy.json

# logs
journalctl -u valor-henry-trend-deploy -n 20 --no-pager
journalctl -u valor-henry-trend-daily -n 20 --no-pager
```
