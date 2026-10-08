#!/bin/sh
# Ship the local evolver/trading package to the PAPER/DEMO droplet.
#   infra/trading/hotfix-deploy.sh           read-only: diff running code vs local, show book state
#   infra/trading/hotfix-deploy.sh --apply   backup, overlay-build, validate, restart feed/worker/agents/research, verify
# Never touches volumes, ledgers, policy files, credentials or the live block. Refuses unless the book is
# demo-mode, unhalted and flat. Rollback command is printed after apply.
set -eu
cd "$(dirname "$0")/../.."
MODE=check; [ "${1:-}" = "--apply" ] && MODE=apply
PROJECT=${VALOR_PROJECT:-valor-demo}
TS=$(date -u +%Y%m%dT%H%M%SZ)
HOST=$(python3 -c 'import json;print(json.load(open(".valor/cloud-connection.json"))["host"])')
OPTS="-i .secrets/valor-study-deploy -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=.secrets/valor-study-known-hosts -o ConnectTimeout=15"

if python3 -c 'import sys;sys.exit(sys.version_info<(3,10))' 2>/dev/null; then
  echo "== local trading tests"
  PYTHONPATH=evolver python3 -m unittest discover -s evolver/tests -p 'test_trading*.py' 2>&1 | tail -3 | tee /tmp/valor-hotfix-tests.txt
  grep -q '^OK' /tmp/valor-hotfix-tests.txt || { echo "local tests failed; aborting"; exit 1; }
else
  echo "== local python3 < 3.10: skipping local tests (the image validation step still runs)"
fi

TAR="/tmp/valor-trading-$TS.tgz"
COPYFILE_DISABLE=1 tar -C evolver/evolver --exclude=__pycache__ --exclude='*.pyc' --exclude=.DS_Store -czf "$TAR" trading
scp -q $OPTS "$TAR" "root@$HOST:/tmp/valor-trading-$TS.tgz"
rm -f "$TAR"

ssh $OPTS "root@$HOST" "MODE=$MODE TS=$TS PROJECT=$PROJECT sh -s" <<'REMOTE'
set -eu
W=/tmp/valor-hotfix-$TS; mkdir -p "$W/new" "$W/running"
tar --warning=no-unknown-keyword -xzf /tmp/valor-trading-$TS.tgz -C "$W/new"
C=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=worker --filter status=running | head -1)
[ -n "$C" ] || { echo "no running $PROJECT worker; aborting"; exit 1; }
label() { docker inspect -f "{{index .Config.Labels \"$1\"}}" "$C"; }
WD=$(label com.docker.compose.project.working_dir); FILES=$(label com.docker.compose.project.config_files)
ENVF=$(label com.docker.compose.project.environment_file)
IMG=$(docker inspect -f '{{.Config.Image}}' "$C"); IMGID=$(docker inspect -f '{{.Image}}' "$C")
POLICY=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/config/policy.json"}}{{.Source}}{{end}}{{end}}' "$C")
echo "== project $PROJECT  dir $WD  image $IMG ($IMGID)"
echo "   compose files: $FILES   env: $ENVF   policy: $POLICY"
docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '   {{.Label "com.docker.compose.service"}}  {{.Image}}  {{.Status}}'

echo "== code drift: running worker vs local package"
docker cp "$C:/app/evolver/trading/." "$W/running/" >/dev/null
diff -rq -x __pycache__ "$W/running" "$W/new/trading" | sed 's|'"$W"'/||g' || true
echo "== other running containers carrying the trading package (files differing from local)"
for x in $(docker ps -q); do
  n=$(docker inspect -f '{{.Name}}' "$x"); d="$W/other$n"; mkdir -p "$d"
  if docker cp "$x:/app/evolver/trading/." "$d/" >/dev/null 2>&1; then
    echo "   $n: $(diff -rq -x __pycache__ "$d" "$W/new/trading" | wc -l) files differ"
  fi
done

echo "== book state"
STATUS=$(curl -fsS http://127.0.0.1:9001/status)
echo "$STATUS" | python3 -c '
import json,sys,time
s=json.load(sys.stdin); sup=s.get("supervisor") or {}
print("   healthy=%s mode=%s halt=%r equity=%s positions=%d pending=%d" % (s.get("healthy"),s.get("mode"),s.get("halt"),
      s.get("equity"),len(s.get("positions") or []),len(s.get("pending_orders") or [])))
print("   supervisor=%s reason=%s" % (sup.get("action"),(sup.get("reason") or "")[:140]))
r=(s.get("pipeline") or {}).get("research_updated_at")
print("   research age: %s h" % (round((time.time()-r)/3600,1) if r else "n/a"))
print("   strategy round trips=%s" % (s.get("readiness") or {}).get("strategy_round_trips"))'

if [ "$MODE" != apply ]; then echo "== check only. Re-run with --apply to deploy."; exit 0; fi

echo "$STATUS" | python3 -c '
import json,sys
s=json.load(sys.stdin)
bad=[k for k,v in (("mode_not_demo",s.get("mode")!="demo"),("halted",bool(s.get("halt"))),
     ("positions_open",bool(s.get("positions"))),("orders_pending",bool(s.get("pending_orders")))) if v]
if bad: print("REFUSING: "+", ".join(bad)); sys.exit(1)'
case "$IMG" in *@sha256:*|"") echo "REFUSING: running image is not a plain tag ($IMG)"; exit 1;; esac
[ "$(docker image inspect -f '{{.Id}}' "$IMG")" = "$IMGID" ] || { echo "REFUSING: tag $IMG no longer matches the running image"; exit 1; }
REPO=${IMG%:*}; NEW="$REPO:hotfix-$TS"
if [ -n "$ENVF" ] && grep -q "=$IMG\$" "$ENVF"; then PIN=env
elif grep -q "image: *$IMG\$" $(echo "$FILES" | tr , ' '); then PIN=compose
else echo "REFUSING: cannot find where $IMG is pinned (env file or compose file)"; exit 1; fi
echo "   image pinned in: $PIN"

echo "== backup"
systemctl start valor-backup.service 2>/dev/null || echo "   (backup unit not started; continuing with existing latest)"
[ -f /var/backups/valor-study/latest.tar.gz ] && cp -p /var/backups/valor-study/latest.tar.gz "/var/backups/valor-study/pre-hotfix-$TS.tar.gz" && echo "   /var/backups/valor-study/pre-hotfix-$TS.tar.gz"

echo "== build overlay image"
cat > "$W/Dockerfile" <<EOF
FROM $IMG
USER root
RUN rm -rf /app/evolver/trading
COPY new/trading /app/evolver/trading
USER 10001:10001
EOF
docker build -q -t "$NEW" -f "$W/Dockerfile" "$W" >/dev/null
docker run --rm --network none -v "$POLICY:/config/policy.json:ro" "$NEW" validate --policy /config/policy.json
docker run --rm --network none --entrypoint python "$NEW" -c \
  "import evolver.trading.worker, evolver.trading.agent_worker, evolver.trading.runtime, evolver.trading.engine; print('   imports ok')"

echo "== swap and restart"
if [ "$PIN" = env ]; then T="$ENVF"; else T=$(grep -l "image: *$IMG\$" $(echo "$FILES" | tr , ' ') | head -1); fi
cp -p "$T" "$T.pre-hotfix-$TS"
sed -i "s|$IMG\$|$NEW|" "$T"
echo "   $T now pins $NEW (previous copy: $T.pre-hotfix-$TS)"
F=""; OLDIFS=$IFS; IFS=,; for f in $FILES; do F="$F -f $f"; done; IFS=$OLDIFS
E=""; [ -n "$ENVF" ] && E="--env-file $ENVF"
cd "$WD"
docker compose -p "$PROJECT" $E $F up -d --no-deps --no-build feed worker agents research
echo "   waiting 90s for health..."; sleep 90
docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '   {{.Label "com.docker.compose.service"}}  {{.Image}}  {{.Status}}'
curl -fsS http://127.0.0.1:9001/healthz && echo
echo "== ROLLBACK if needed:"
echo "   cp -p $T.pre-hotfix-$TS $T && cd $WD && docker compose -p $PROJECT $E $F up -d --no-deps --no-build feed worker agents research"
REMOTE
