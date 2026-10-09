#!/bin/sh
# One-time install: retire Henry v2/v4 (its journal is kept), bring up the live trend book, and add
#   - valor-henry-trend-deploy.timer  every 5 min: redeploy when the trend book's code changes
#   - valor-henry-trend-daily.timer   00:20 UTC daily: refresh the daily seed and the Binance shadow
set -eu
BRANCH=${1:-henry-desk-candidate}
SRC=/opt/valor-henry-trend/src

# 1. retire v4: its watcher first, or it would redeploy v4 the moment the container stops
systemctl disable --now valor-henry-v2-autodeploy.timer 2>/dev/null || true
docker update --restart=no valor-henry-v2 >/dev/null 2>&1 || true
docker stop valor-henry-v2 >/dev/null 2>&1 || true
echo "retired valor-henry-v2 (container stopped, volumes kept)"

# 2. code and config
mkdir -p /opt/valor-henry-trend /var/lib/valor-henry-trend
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }
[ -d "$SRC/.git" ] || git clone -q --depth 1 --branch "$BRANCH" https://github.com/Flyger1an/valor.git "$SRC"
install -m 0755 "$SRC/infra/trading/henry-trend-deploy.sh" /usr/local/bin/valor-henry-trend-deploy
printf 'BRANCH=%s\n' "$BRANCH" > /etc/default/valor-henry-trend

cat > /etc/systemd/system/valor-henry-trend-deploy.service <<'EOF'
[Unit]
Description=Henry trend book pull-based deploy
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/default/valor-henry-trend
ExecStartPre=/bin/sh -c 'git -C /opt/valor-henry-trend/src fetch -q --depth 1 origin "$${BRANCH}" && git -C /opt/valor-henry-trend/src checkout -q -f FETCH_HEAD && install -m 0755 /opt/valor-henry-trend/src/infra/trading/henry-trend-deploy.sh /usr/local/bin/valor-henry-trend-deploy'
ExecStart=/usr/local/bin/valor-henry-trend-deploy
EOF
cat > /etc/systemd/system/valor-henry-trend-deploy.timer <<'EOF'
[Unit]
Description=Check GitHub for trend book updates every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/valor-henry-trend-daily.service <<'EOF'
[Unit]
Description=Henry trend book: refresh daily seed and Binance shadow
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'S=/var/lib/valor-henry-trend/deployed; V=$(cat $S.volume); I=$(cat $S.image); \
  F="docker run --rm --user 10001:10001 --read-only --tmpfs /tmp -v $V:/henry --entrypoint python $I -m evolver.trading.henry_trend_feed --policy /config/henry-trend-policy.json"; \
  $F shadow --out /henry/shadow.json; $F seed --out /henry/daily_seed.json'
EOF
cat > /etc/systemd/system/valor-henry-trend-daily.timer <<'EOF'
[Unit]
Description=Refresh the trend book's shadow and seed after each UTC daily close

[Timer]
OnCalendar=*-*-* 00:20:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
/usr/local/bin/valor-henry-trend-deploy
systemctl enable --now valor-henry-trend-deploy.timer valor-henry-trend-daily.timer
echo "installed. watching branch: $BRANCH"
