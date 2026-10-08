#!/bin/sh
# Pull-based auto-deploy for Henry v2 ONLY. Runs on the droplet from a systemd timer.
#
# Every run: fetch the watched branch; if Henry v2's files changed since the last deploy,
# run Henry v2's tests inside the running Valor image, rebuild, and swap the container.
#   - Same HENRY_V2_RULES  -> same volume; the journal resumes with cash, position and history intact.
#   - Changed HENRY_V2_RULES -> a NEW volume and a fresh $500 run; the old volume is kept untouched,
#     because the journal refuses to load under different rules and results must never be mixed.
# Never touches the three-book experiment, the source ledger, the worker, or any other service.
# Failed tests or a failed build leave the running Henry v2 exactly as it was.
set -eu

CONF=/etc/default/valor-henry-v2
[ -f "$CONF" ] && . "$CONF"
REPO_URL=${REPO_URL:-https://github.com/Flyger1an/valor.git}
BRANCH=${BRANCH:-henry-v2-raging-bull}
SRC=${SRC:-/opt/valor-henry-v2/src}
STATE=${STATE:-/var/lib/valor-henry-v2/deployed}
NAME=valor-henry-v2
FILES="evolver/evolver/trading/henry_v2.py evolver/evolver/trading/henry_v2_runner.py evolver/evolver/trading/strategies.py evolver/evolver/trading/contracts.py infra/trading/policy.demo.json"

exec 9>/var/lock/valor-henry-v2-autodeploy.lock
flock -n 9 || { echo "another deploy is running"; exit 0; }
mkdir -p "$(dirname "$STATE")" "$(dirname "$SRC")"
log() { echo "henry-v2-autodeploy: $*"; }

if [ ! -d "$SRC/.git" ]; then
  git clone -q --depth 1 --branch "$BRANCH" "$REPO_URL" "$SRC"
else
  git -C "$SRC" fetch -q --depth 1 origin "$BRANCH"
  git -C "$SRC" checkout -q -f FETCH_HEAD
fi
COMMIT=$(git -C "$SRC" rev-parse HEAD)

# Fingerprint only Henry v2's inputs, so unrelated commits never restart him.
INPUTS=$(cd "$SRC" && cat $FILES | sha256sum | cut -d' ' -f1)
LAST_INPUTS=$(cat "$STATE.inputs" 2>/dev/null || true)
if [ "$INPUTS" = "$LAST_INPUTS" ] && docker ps -q --filter "name=^${NAME}$" --filter status=running | grep -q .; then
  exit 0
fi

BASE=$(docker ps --filter name=valor-experiment --format '{{.Image}}' | head -1)
[ -n "$BASE" ] || { log "no running valor-experiment image to build on; nothing changed"; exit 1; }
log "change detected at $COMMIT on $BRANCH (base $BASE)"

# Tests run inside the same Python/runtime Henry will use. Any failure aborts with Henry untouched.
if ! OUT=$(docker run --rm --network none -v "$SRC":/src:ro -e PYTHONPATH=/src/evolver -e PYTHONDONTWRITEBYTECODE=1 \
    -w /src/evolver --entrypoint python "$BASE" -m unittest tests.test_trading_henry_v2 2>&1); then
  echo "$OUT" | tail -15
  log "tests FAILED at $COMMIT; keeping the running Henry v2"
  exit 1
fi
log "tests passed: $(echo "$OUT" | grep -E '^Ran ' || true)"

RULES=$(docker run --rm --network none -v "$SRC":/src:ro -e PYTHONPATH=/src/evolver --entrypoint python "$BASE" -c \
  "from evolver.trading.henry_v2 import HENRY_V2_RULES as R,digest;print(digest(R)[:12], R['version'])")
RULES_HASH=${RULES%% *}; RULES_VERSION=${RULES#* }

# Which volume holds the journal for these exact rules. The original deploy used valor-henry-v2_state.
if [ ! -f "$STATE.volume" ] && docker volume inspect valor-henry-v2_state >/dev/null 2>&1; then
  RUNNING=$(docker exec "$NAME" python -c "from evolver.trading.henry_v2 import HENRY_V2_RULES as R,digest;print(digest(R)[:12])" 2>/dev/null || true)
  [ -n "$RUNNING" ] && { echo valor-henry-v2_state > "$STATE.volume"; echo "$RUNNING" > "$STATE.rules"; }
fi
LAST_RULES=$(cat "$STATE.rules" 2>/dev/null || true)
if [ "$RULES_HASH" = "$LAST_RULES" ]; then
  VOLUME=$(cat "$STATE.volume")
  FRESH=no
else
  VOLUME="valor-henry-v2-${RULES_HASH}_state"
  FRESH=yes
  log "rules changed -> $RULES_VERSION ($RULES_HASH): new run in $VOLUME; previous journal kept in $(cat "$STATE.volume" 2>/dev/null || echo none)"
fi

CTX=$(mktemp -d)
cp "$SRC"/evolver/evolver/trading/henry_v2.py "$SRC"/evolver/evolver/trading/henry_v2_runner.py \
   "$SRC"/evolver/evolver/trading/strategies.py "$SRC"/evolver/evolver/trading/contracts.py "$CTX"/
cp "$SRC"/infra/trading/policy.demo.json "$CTX"/policy.json
cat > "$CTX/Dockerfile" <<'DF'
ARG BASE=scratch
FROM ${BASE}
USER root
COPY --chown=10001:10001 henry_v2.py henry_v2_runner.py strategies.py contracts.py /app/evolver/trading/
COPY --chown=10001:10001 policy.json /config/henry-v2-policy.json
RUN mkdir -p /henry && chown 10001:10001 /henry
USER 10001:10001
ENTRYPOINT ["python", "-m", "evolver.trading.henry_v2_runner"]
DF
TAG="valor-henry-v2:$(echo "$COMMIT" | cut -c1-12)"
docker build -q --build-arg BASE="$BASE" -t "$TAG" "$CTX" >/dev/null || { rm -rf "$CTX"; log "build FAILED; keeping the running Henry v2"; exit 1; }
rm -rf "$CTX"

H="--read-only --network none --user 10001:10001 --init --cap-drop ALL --security-opt no-new-privileges:true \
 --pids-limit 32 --memory 256m --cpus 0.25 --tmpfs /tmp:rw,noexec,nosuid,size=16m \
 -v $VOLUME:/henry -v valor-demo_market:/runtime/market:ro"

if [ "$FRESH" = yes ]; then
  docker run --rm $H "$TAG" init --policy /config/henry-v2-policy.json --root /henry >/dev/null \
    || { log "init FAILED; keeping the running Henry v2"; exit 1; }
fi
# A same-rules image must open the existing journal before we swap anything.
docker run --rm $H "$TAG" report --policy /config/henry-v2-policy.json --root /henry >/dev/null \
  || { log "new image cannot open $VOLUME; keeping the running Henry v2"; exit 1; }

docker stop -t 20 "$NAME" >/dev/null 2>&1 || true
docker rm "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped --log-opt max-size=5m --log-opt max-file=2 \
  --label valor.henry-v2.commit="$COMMIT" --label valor.henry-v2.rules="$RULES_VERSION:$RULES_HASH" \
  $H "$TAG" run --policy /config/henry-v2-policy.json --root /henry --source-root /runtime >/dev/null

echo "$INPUTS" > "$STATE.inputs"; echo "$RULES_HASH" > "$STATE.rules"; echo "$VOLUME" > "$STATE.volume"
echo "$COMMIT" > "$STATE.commit"
log "deployed $COMMIT ($RULES_VERSION) on $VOLUME"
