# Henry v2 (henry-raging-bull-v1)

A standalone, aggressive, cash-only virtual book. It is a fourth, separate study, not part of the
three-book experiment. Baseline, kelly and henry v1 keep their journal, identity and rules unchanged.

## What Henry v2 does

| | Three-book study | Henry v2 |
|---|---|---|
| Entries | one frozen strategy | all 9 approved strategies, all allowed symbols; breakout first, then EMA trend, then dip buys |
| Hours | shared session | 24/7, no supervisor, news, session or daily-trade gate |
| Sizing | caps / Kelly / all-cash | probe with 50% of cash, press the rest once up one stop distance with the trend intact |
| Exits | fixed target, 12h max hold, first EMA cross | no target, no hold clock; trailing stop at 2x stop distance under the high-water mark (moves to breakeven once working); momentum trades also exit on 2 closes under the slow EMA |
| Loss limits | daily loss, drawdown halts | none, except the equity floor |
| Floor | n/a | equity under $250 halts permanently and liquidates |
| Leverage | none | none (Alpaca spot crypto cannot do margin, so leveraged data would be meaningless) |

Realism kept on purpose: fills at the next observed quote, adverse slippage, modeled fees both
sides, a 1% bar-volume liquidity cap, exchange increments and minimums, closed bars only.

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

`snapshot.json` reports equity, return, max drawdown, win rate, average win and loss, payoff ratio,
best and worst trade, how many trades were pressed, the open position with its live trailing stop,
and the last decision. The questions it answers: does letting winners run beat the capped books,
and what drawdown does that cost.

New rules mean a new root. The journal refuses to load under changed rules, so results are never mixed.
