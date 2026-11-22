#!/usr/bin/env bash
# Switches the server off when the app says it is idle (plan v4). Run every 10 minutes by stockresearch-idle.timer.
# It only acts when /opt/stockresearch/.auto-shutdown exists ("armed"). Without that file it just reports what it would do.
# Anything unclear means "stay on": the server keeps running, which costs a little but loses nothing.
set -uo pipefail
cd /opt/stockresearch

if ! reason=$(docker compose exec -T app stockresearch --plain-logs idle-check 2>&1); then
  echo "keeping the server on: ${reason}"
  exit 0
fi
echo "idle: ${reason}"

if [[ ! -f .auto-shutdown ]]; then
  echo "dry run (not armed): the server would switch off now"
  exit 0
fi

echo "backing up before switching off"
timeout 300 ./deploy/backup.sh || echo "backup failed; switching off anyway"
sync
systemctl poweroff
