#!/bin/bash
set -euo pipefail

HODL_DIR="/home/ubuntu/hodlbackend2/HODL-2025"
MONITORING_DIR="/home/ubuntu/monitoring"
RELOAD_CRONTABS="$MONITORING_DIR/bin/reload-crontabs.sh"
EXPECTED_MONITORED_CRONS="${HODL_EXPECTED_MONITORED_CRONS:-31}"
CHECK_ONLY=0

if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=1
fi

if [[ "$(id -u)" == "0" ]]; then
  echo "ERROR: do not run this deploy script as root. Use the ubuntu deploy user." >&2
  exit 1
fi

log() {
  echo
  echo "==> $*"
}

run() {
  echo "+ $*"
  "$@"
}

count_cron() {
  local pattern="$1"
  crontab -l 2>/dev/null | grep -F -c "$pattern" || true
}

verify_crontab() {
  local direct_count
  local monitored_count
  direct_count="$(count_cron "/home/ubuntu/hodlbackend2/HODL-2025/manage.py crontab run")"
  monitored_count="$(count_cron "/home/ubuntu/monitoring/django/hodl/manage_monitored.py")"
  echo "direct HODL crons: $direct_count"
  echo "monitored HODL crons: $monitored_count"
  if [[ "$direct_count" != "0" ]]; then
    echo "ERROR: direct HODL crons must be 0." >&2
    return 1
  fi
  if [[ "$monitored_count" != "$EXPECTED_MONITORED_CRONS" ]]; then
    echo "ERROR: monitored HODL crons must be $EXPECTED_MONITORED_CRONS." >&2
    return 1
  fi
}

print_cronops_summary() {
  local attempts="${1:-1}"
  local delay="${2:-2}"
  local attempt

  for attempt in $(seq 1 "$attempts"); do
    if command -v jq >/dev/null 2>&1; then
      if curl -fsS --max-time 8 http://127.0.0.1:8001/api/cronops/live/ \
        | jq '{queue_summary, queue_by_queue, spool_summary}'; then
        return 0
      fi
    else
      if curl -fsS --max-time 8 http://127.0.0.1:8001/api/cronops/live/; then
        return 0
      fi
    fi
    if [[ "$attempt" != "$attempts" ]]; then
      sleep "$delay"
    fi
  done

  echo "ERROR: CronOps live API was not reachable after $attempts attempt(s)." >&2
  return 1
}

restart_pm2_app() {
  local name="$1"
  run pm2 restart "$name" --update-env
}

log "Entering HODL repo"
cd "$HODL_DIR"

log "Activating virtualenv"
source venv/bin/activate

if [[ "$CHECK_ONLY" == "1" ]]; then
  log "Check mode: no git pull, migrations, crontab writes, or PM2 restarts"
  run python manage.py check
  verify_crontab
  run pm2 status
  print_cronops_summary
  exit 0
fi

log "Updating hodl-backend branch"
run git fetch origin hodl-backend
run git checkout hodl-backend
run git pull --ff-only origin hodl-backend

log "Installing Python dependencies"
run pip install -r requirements.txt

log "Running Django checks and migrations"
run python manage.py check
run python manage.py migrate --noinput

log "Syncing CronOps job inventory"
if python manage.py help sync_cronops_jobs >/dev/null 2>&1; then
  run python manage.py sync_cronops_jobs
else
  echo "sync_cronops_jobs command not available; skipping"
fi

log "Removing direct HODL crons and reloading monitored crons"
DJANGO_SETTINGS_MODULE=config.settings python manage.py crontab remove || true
run "$RELOAD_CRONTABS"
verify_crontab

log "Restarting HODL PM2 processes by name"
restart_pm2_app "hodl-backend"
restart_pm2_app "hodl-cronops-worker-financial"
restart_pm2_app "hodl-cronops-worker-rank"
restart_pm2_app "hodl-cronops-worker-analytics"
restart_pm2_app "hodl-cronops-worker-maintenance"
run pm2 save

log "Final CronOps queue summary"
print_cronops_summary 45 2

log "HODL deploy completed"
