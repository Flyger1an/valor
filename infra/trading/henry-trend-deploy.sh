#!/bin/sh
# Pull-based deploy for the live trend book. Run by a systemd timer every 5 minutes, and once by the installer.
#
# When the trend book's own inputs change on the watched branch: run its tests inside the running Valor
# image, rebuild, and swap the container.
#   - Same TREND_RULES   -> same volume; the journal resumes untouched.
#   - Changed TREND_RULES -> a NEW volume, freshly seeded; the old volume is kept.
# Never touches the three-book study, the source ledger, the worker, or any other service.
set -eu

CONF=/etc/default/valor-henry-trend
[ -f "$CONF" ] && . "$CONF"
REPO_URL=${REPO_URL:-https://github.com/Flyger1an/valor.git}
BRANCH=${BRANCH:-henry-desk-candidate}
SRC=${SRC:-/opt/valor-henry-trend/src}
STATE=${STATE:-/var/lib/valor-henry-trend/deployed}
NAME=valor-henry-trend
T=evolver/evolver/trading
FILES="$T/henry_trend.py $T/henry_trend_runner.py $T/henry_trend_feed.py $T/henry_lab.py $T/henry_desk_data.py $T/contracts.py infra/trading/policy.demo.json"

exec 9>/var/lock/valor-henry-trend-deploy.lock
flock -n 9 || { echo "another deploy is running"; exit 0; }
mkdir -p "$(dirname "$STATE")" "$(dirname "$SRC")"
log() { echo "henry-trend-deploy: $*"; }

if [ ! -d "$SRC/.git" ]; then
  git clone -q --depth 1 --branch "$BRANCH" "$REPO_URL" "$SRC"
else
  git -C "$SRC" fetch -q --depth 1 origin "$BRANCH"
  git -C "$SRC" checkout -q -f FETCH_HEAD
fi
COMMIT=$(git -C "$SRC" rev-parse HEAD)
INPUTS=$(cd "$SRC" && cat $FILES | sha256sum | cut -d' ' -f1)
if [ "$INPUTS" = "$(cat "$STATE.inputs" 2>/dev/null || true)" ] && docker ps -q --filter "name=^${NAME}$" --filter status=running | grep -q .; then
  exit 0
fi

BASE=$(docker ps --filter name=valor-experiment --format '{{.Image}}' | head -1)
[ -n "$BASE" ] || { log "no running valor-experiment image to build on"; exit 1; }
log "change detected at $COMMIT (base $BASE)"

if ! OUT=$(docker run --rm --network none -v "$SRC":/src:ro -e PYTHONPATH=/src/evolver -e PYTHONDONTWRITEBYTECODE=1 \
    -w /src/evolver --entrypoint python "$BASE" -m unittest tests.test_trading_henry_trend 2>&1); then
  echo "$OUT" | tail -15
  log "tests FAILED at $COMMIT; keeping the running trend book"
  exit 1
fi
log "tests passed: $(echo "$OUT" | grep -E '^Ran ' || true)"

RULES=$(docker run --rm --network none -v "$SRC":/src:ro -e PYTHONPATH=/src/evolver --entrypoint python "$BASE" -c \
  "from evolver.trading.henry_trend import TREND_RULES as R,digest;print(digest(R)[:12], R['version'])")
RULES_HASH=${RULES%% *}; RULES_VERSION=${RULES#* }

CTX=$(mktemp -d)
cp "$SRC"/$T/henry_trend.py "$SRC"/$T/henry_trend_runner.py "$SRC"/$T/henry_trend_feed.py \
   "$SRC"/$T/henry_lab.py "$SRC"/$T/henry_desk_data.py "$SRC"/$T/contracts.py "$CTX"/
cp "$SRC"/infra/trading/policy.demo.json "$CTX"/policy.json
cat > "$CTX/Dockerfile" <<'DF'
ARG BASE=scratch
FROM ${BASE}
USER root
COPY --chown=10001:10001 henry_trend.py henry_trend_runner.py henry_trend_feed.py henry_lab.py henry_desk_data.py contracts.py /app/evolver/trading/
COPY --chown=10001:10001 policy.json /config/henry-trend-policy.json
RUN mkdir -p /henry && chown 10001:10001 /henry
USER 10001:10001
ENTRYPOINT ["python", "-m", "evolver.trading.henry_trend_runner"]
DF
TAG="valor-henry-trend:$(echo "$COMMIT" | cut -c1-12)"
docker build -q --build-arg BASE="$BASE" -t "$TAG" "$CTX" >/dev/null || { rm -rf "$CTX"; log "build FAILED"; exit 1; }
rm -rf "$CTX"

if [ "$RULES_HASH" = "$(cat "$STATE.rules" 2>/dev/null || true)" ]; then
  VOLUME=$(cat "$STATE.volume"); FRESH=no
else
  VOLUME="valor-henry-trend-${RULES_HASH}_state"; FRESH=yes
  log "rules $RULES_VERSION ($RULES_HASH): new book in $VOLUME"
fi
RO="--read-only --user 10001:10001 --init --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 32 \
 --memory 384m --cpus 0.25 --tmpfs /tmp:rw,noexec,nosuid,size=16m -v $VOLUME:/henry"
FEED="docker run --rm $RO --entrypoint python $TAG -m evolver.trading.henry_trend_feed --policy /config/henry-trend-policy.json"

if [ "$FRESH" = yes ]; then
  $FEED seed --out /henry/daily_seed.json || { log "seed FAILED (network?)"; exit 1; }
  $FEED shadow --out /henry/shadow.json || log "shadow unavailable for now; the daily job will retry"
  docker run --rm --network none $RO "$TAG" init --root /henry --policy /config/henry-trend-policy.json --seed /henry/daily_seed.json >/dev/null \
    || { log "init FAILED"; exit 1; }
fi
CHECK=$(docker run --rm --network none $RO --entrypoint python "$TAG" -c \
  "from evolver.trading.henry_trend import TrendBook; b=TrendBook('/henry/henry_trend.sqlite'); r=b.report(); b.close(); print(r['rules_version'], r['equity_usd'], r['daily_history_days'])" 2>&1) \
  || { echo "$CHECK" | tail -5; log "new image cannot open $VOLUME; keeping the running book"; exit 1; }
log "journal check ok: $CHECK"

docker stop -t 20 "$NAME" >/dev/null 2>&1 || true
docker rm "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped --network none --log-opt max-size=5m --log-opt max-file=2 \
  --label valor.henry-trend.commit="$COMMIT" $RO -v valor-demo_market:/runtime/market:ro \
  "$TAG" run --root /henry --policy /config/henry-trend-policy.json --source-root /runtime >/dev/null

echo "$INPUTS" > "$STATE.inputs"; echo "$RULES_HASH" > "$STATE.rules"; echo "$VOLUME" > "$STATE.volume"
echo "$TAG" > "$STATE.image"; echo "$COMMIT" > "$STATE.commit"
log "deployed $COMMIT ($RULES_VERSION) on $VOLUME"
