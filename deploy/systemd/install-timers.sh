#!/usr/bin/env bash
# Installs the job schedule (plan section 6) as systemd timers. Run once on the server with sudo.
set -euo pipefail

UNIT_DIR=/etc/systemd/system
cp "$(dirname "$0")/stockresearch-job@.service" "$UNIT_DIR/"

make_timer() {
  local job="$1" calendar="$2" description="$3"
  cat > "$UNIT_DIR/stockresearch-job@${job}.timer" <<EOF
[Unit]
Description=${description}

[Timer]
OnCalendar=${calendar}
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
EOF
  systemctl enable --now "stockresearch-job@${job}.timer"
}

# Times are India Standard Time.
make_timer collect      "*-*-* *:05:00 Asia/Kolkata"                "Hourly: new filings and approved headlines"
make_timer research     "*-*-* *:00/15:00 Asia/Kolkata"             "Every 15 minutes: research queue"
make_timer market-close "Mon..Fri *-*-* 19:00:00 Asia/Kolkata"      "Evening: prices, deals, surveillance, screen, exit alerts"
make_timer publish      "Mon..Fri *-*-* 08:00:00 Asia/Kolkata"      "Morning: rate, pick, email"
make_timer open-check   "Mon..Fri *-*-* 09:20:00 Asia/Kolkata"      "After the open: flag stale picks"
make_timer practice-weekly "Fri *-*-* 19:30:00 Asia/Kolkata"         "Friday: practice-portfolio summary email"

# Nightly backup (database copy, then sync to S3 if configured)
cat > "$UNIT_DIR/stockresearch-backup.service" <<'EOF'
[Unit]
Description=Stock research nightly backup
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/stockresearch
ExecStart=/opt/stockresearch/deploy/backup.sh
EOF
cat > "$UNIT_DIR/stockresearch-backup.timer" <<'EOF'
[Unit]
Description=Stock research nightly backup

[Timer]
OnCalendar=*-*-* 02:30:00 Asia/Kolkata
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Self-shutdown when idle (plan v4). Installed but only acts if /opt/stockresearch/.auto-shutdown exists.
cp "$(dirname "$0")/stockresearch-idle.service" "$(dirname "$0")/stockresearch-idle.timer" "$UNIT_DIR/"
chmod +x "$(dirname "$0")/../idle-shutdown.sh"

systemctl daemon-reload
systemctl enable --now stockresearch-backup.timer
systemctl enable --now stockresearch-idle.timer
systemctl list-timers 'stockresearch*'
