#!/bin/sh
# One-time install on the droplet: Henry v2 checks GitHub every 5 minutes and redeploys itself.
#   sh install-henry-v2-autodeploy.sh [branch]      default branch: henry-v2-raging-bull
set -eu
BRANCH=${1:-henry-v2-raging-bull}
SRC=/opt/valor-henry-v2/src
mkdir -p /opt/valor-henry-v2 /var/lib/valor-henry-v2
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }
[ -d "$SRC/.git" ] || git clone -q --depth 1 --branch "$BRANCH" https://github.com/Flyger1an/valor.git "$SRC"
install -m 0755 "$SRC/infra/trading/henry-v2-autodeploy.sh" /usr/local/bin/valor-henry-v2-autodeploy
printf 'BRANCH=%s\n' "$BRANCH" > /etc/default/valor-henry-v2

cat > /etc/systemd/system/valor-henry-v2-autodeploy.service <<'EOF'
[Unit]
Description=Henry v2 pull-based auto-deploy
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/default/valor-henry-v2
# Refresh the deploy script itself from the repo before running it.
ExecStartPre=/bin/sh -c 'git -C /opt/valor-henry-v2/src fetch -q --depth 1 origin "$${BRANCH}" && git -C /opt/valor-henry-v2/src checkout -q -f FETCH_HEAD && install -m 0755 /opt/valor-henry-v2/src/infra/trading/henry-v2-autodeploy.sh /usr/local/bin/valor-henry-v2-autodeploy'
ExecStart=/usr/local/bin/valor-henry-v2-autodeploy
EOF

cat > /etc/systemd/system/valor-henry-v2-autodeploy.timer <<'EOF'
[Unit]
Description=Check GitHub for Henry v2 updates every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now valor-henry-v2-autodeploy.timer
systemctl start valor-henry-v2-autodeploy.service || true
echo "installed. watching branch: $BRANCH"
journalctl -u valor-henry-v2-autodeploy.service -n 15 --no-pager
