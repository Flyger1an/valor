# Henry v2 (henry-raging-bull-v3)

A standalone, aggressive, cash-only virtual book. It is a fourth, separate study, not part of the
three-book experiment. Baseline, kelly and henry v1 keep their journal, identity and rules unchanged.

## What Henry v2 does

| | Three-book study | Henry v2 |
|---|---|---|
| Regime | none | 1h bars built from 5m: uptrend, consolidation, transition or downtrend, from a 20h EMA, its 5h slope and 24h efficiency (net move / total path). Seeded from the feed's history.json so a fresh run is not blind |
| Entries | one frozen strategy | uptrend: breakouts and EMA trend entries. consolidation: dip buys only. transition and downtrend: cash. Strongest 24h coin wins ties, 24/7, no supervisor, news, session or daily-trade gate |
| Sizing | caps / Kelly / all-cash | probe with 50% of cash, press the rest only in an uptrend once up 2 stop distances with the 5m trend intact |
| Stops | fixed 1-3% | 2.5x ATR (5m), clamped 0.8% to 6%; once working, trail 3x ATR under the high-water mark, never below true round-trip breakeven |
| Exits | fixed target, 12h max hold, first EMA cross | no target or hold clock. Momentum: trailing stop, 2 closes under the slow EMA, or the regime turning to downtrend. Dip buys: stop or the range middle |
| Loss limits | daily loss, drawdown halts | none, except the equity floor |
| Floor | n/a | equity under $250 halts permanently and liquidates |
| Leverage | none | none (Alpaca spot crypto cannot do margin, so leveraged data would be meaningless) |

Realism kept on purpose: fills at the next observed quote, adverse slippage, modeled fees both
sides. v2 liquidity: entries may use the larger of 1% of bar volume or $2,000 per bar (Alpaca reports
only its own thin volume); exits always fill the whole position. Each position is one trade record,
tagged with its entry and exit regime, family, stop size, ATR and spread. Quotes wider than 1.5% are ignored as bad data.
Exchange increments and minimums and closed bars only are also kept.

## Deploy on the droplet

Henry v2 mounts only the market volume, read-only, with no network.

```bash
cd ~/valor
CTX=$(mktemp -d)
cp evolver/evolver/trading/{henry_v2.py,henry_v2_runner.py,strategies.py,contracts.py} "$CTX"/
cp infra/trading/policy.demo.json "$CTX"/policy.json
docker build -f infra/trading/Dockerfile.henry-v2 \
  --build-arg VALOR_EXPERIMENT_BASE="$(docker inspect --format '{{.Config.Image}}' valor-experiment-ledgers-1)" \
  -t valor-henry-v2:raging-bull-v1 "$CTX"
export VALOR_HENRY_V2_IMAGE=valor-henry-v2:raging-bull-v1

# One-time explicit start of the $500 bankroll. Refuses to run twice.
docker compose -f infra/trading/henry-v2.compose.yaml run --rm henry-v2 \
  init --policy /config/henry-v2-policy.json --root /henry

docker compose -f infra/trading/henry-v2.compose.yaml up -d
docker compose -f infra/trading/henry-v2.compose.yaml run --rm henry-v2 \
  report --policy /config/henry-v2-policy.json --root /henry
```

If the experiment container has a different name, `docker ps --format '{{.Names}} {{.Image}}'` shows it.

## Auto-deploy from GitHub

One-time install on the droplet (the repo is public, so no keys are needed):

```bash
git clone -q --depth 1 --branch henry-v2-raging-bull https://github.com/Flyger1an/valor.git /opt/valor-henry-v2/src
sh /opt/valor-henry-v2/src/infra/trading/install-henry-v2-autodeploy.sh henry-v2-raging-bull
```

A systemd timer checks the branch every 5 minutes. When Henry v2's own files change, it runs his tests
inside the running Valor image, rebuilds, proves the new image can open the journal, then swaps the
container. Same rules resume the same journal; changed `HENRY_V2_RULES` start a fresh $500 run in a new
volume and keep the old one. Failed tests or builds leave the running Henry v2 alone. Nothing else on
the droplet is ever touched.

- Watch a different branch: edit `/etc/default/valor-henry-v2`.
- See what happened: `journalctl -u valor-henry-v2-autodeploy -n 30 --no-pager`
- Deploy now instead of waiting: `systemctl start valor-henry-v2-autodeploy`
- Pause auto-deploy: `systemctl disable --now valor-henry-v2-autodeploy.timer`

## Reading the data

`regimes` shows what Henry thinks each coin is doing right now, and `by_entry_regime` totals trades,
wins and PnL per regime: the answer to where he actually makes money.


`snapshot.json` reports equity, return, max drawdown, win rate, average win and loss, payoff ratio,
best and worst trade, how many trades were pressed, the open position with its live trailing stop,
and the last decision. The questions it answers: does letting winners run beat the capped books,
and what drawdown does that cost.

New rules mean a new root. The journal refuses to load under changed rules, so results are never mixed.
