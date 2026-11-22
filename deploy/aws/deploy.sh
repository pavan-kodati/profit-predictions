#!/usr/bin/env bash
# Deploy the package uploaded to s3://stockresearch-backups-542885031953/deploy/app.tgz (made with `git archive`).
# Run on the server through Session Manager:  python deploy/aws/ssm_run.py deploy/aws/deploy.sh 1500
# Order: wait for running jobs, back up the database, keep the current code for rollback, unpack, rebuild, check health.
# Rollback: tar -xzf /opt/stockresearch-prev.tgz -C /opt/stockresearch && docker compose up -d --build app
set -euo pipefail
cd /opt/stockresearch
PKG=s3://stockresearch-backups-542885031953/deploy/app.tgz

echo "== 1. waiting for running jobs"
for _ in $(seq 1 30); do
  running=$(systemctl list-units 'stockresearch-job@*.service' --state=active --no-legend | awk '{print $1}' || true)
  [[ -z "$running" ]] && break
  echo "busy: $running"; sleep 30
done
if [[ -n "${running:-}" ]]; then echo "ABORT: jobs still running after 15 minutes: $running"; exit 3; fi

echo "== 2. backup"
bash deploy/backup.sh
ls -t data/backups | head -1

echo "== 3. keep current code for rollback"
tar --exclude=./data --exclude=./.env --exclude=./.backup.env -czf /opt/stockresearch-prev.tgz .

echo "== 4. unpack new code"
aws s3 cp --only-show-errors "$PKG" /tmp/app.tgz
tar -xzf /tmp/app.tgz -C /opt/stockresearch

echo "== 5. rebuild and restart (migrations run on start)"
docker compose up -d --build app
bash deploy/systemd/install-timers.sh

echo "== 6. health"
for _ in $(seq 1 30); do
  if docker compose exec -T app python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/health').read().decode())" 2>/dev/null; then
    ok=1; break
  fi
  sleep 5
done
if [[ -z "${ok:-}" ]]; then echo "ABORT: app not healthy"; docker compose logs --tail 80 app; exit 4; fi
docker compose exec -T app python -c "import sqlite3;print('db version', sqlite3.connect('/app/data/app.db').execute('select version_num from alembic_version').fetchone())"

echo "== 7. feasibility"
docker compose exec -T app stockresearch feasibility || true

echo "== 8. items spanning a split or bonus"
docker compose exec -T app stockresearch corporate-action check || true
echo "DEPLOY OK"
