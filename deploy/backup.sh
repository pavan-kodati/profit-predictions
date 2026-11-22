#!/usr/bin/env bash
# Backup (nightly, and before every self-shutdown): consistent SQLite copy, keep 30 days locally, and sync to S3 when BACKUP_S3_URI is set
# (e.g. BACKUP_S3_URI=s3://my-bucket/stockresearch in /opt/stockresearch/.backup.env). S3 has 5 GB free.
set -euo pipefail
cd /opt/stockresearch

docker compose exec -T app stockresearch backup --out /app/data/backups
find ./data/backups -name 'app-*.db' -mtime +30 -delete

if [[ -f .backup.env ]]; then
  # shellcheck disable=SC1091
  source .backup.env
fi
if [[ -n "${BACKUP_S3_URI:-}" ]]; then
  aws s3 sync ./data/backups "$BACKUP_S3_URI" --delete --only-show-errors
fi
