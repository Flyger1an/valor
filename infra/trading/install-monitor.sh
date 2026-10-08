#!/bin/sh
set -eu
id valor-monitor >/dev/null 2>&1 || useradd --create-home --shell /bin/sh valor-monitor
install -d -m 0700 -o root -g root /home/valor-monitor/.ssh
# Key is public; the private monitoring key stays on the user's computer.
cat > /home/valor-monitor/.ssh/authorized_keys <<'EOF'
restrict,command="/usr/local/bin/valor-monitor" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEzWRJWdRnWhBYp5fgS7v3QjhJqvwKE8hyJstAVvzjjb valor-study-2026-monitor
EOF
chown root:valor-monitor /home/valor-monitor/.ssh /home/valor-monitor/.ssh/authorized_keys
chmod 0750 /home/valor-monitor/.ssh
chmod 0640 /home/valor-monitor/.ssh/authorized_keys
install -m 0755 /opt/valor-study/infra/trading/monitor-command.py /usr/local/bin/valor-monitor
install -m 0755 /opt/valor-study/infra/trading/backup.py /usr/local/bin/valor-backup
install -d -m 0750 -o root -g valor-monitor /var/backups/valor-study
cat > /etc/systemd/system/valor-backup.service <<'EOF'
[Unit]
Description=Consistent Valor study backup without credentials
After=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/bin/valor-backup
EOF
cat > /etc/systemd/system/valor-backup.timer <<'EOF'
[Unit]
Description=Daily Valor study backup
[Timer]
OnCalendar=*-*-* 00:15:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now valor-backup.timer
