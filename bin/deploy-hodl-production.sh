#!/bin/bash
set -euo pipefail

HODL_DIR="/home/ubuntu/hodlbackend2/HODL-2025"
MONITORING_DIR="/home/ubuntu/monitoring"
HEALTHCHECKS_DIR="$MONITORING_DIR/healthchecks"
RELOAD_CRONTABS="$MONITORING_DIR/bin/reload-crontabs.sh"
CRONOPS_WORKER_WATCHDOG="$MONITORING_DIR/bin/ensure-hodl-cronops-workers.sh"
CRONOPS_WORKER_WATCHDOG_SERVICE="$MONITORING_DIR/systemd/hodl-cronops-worker-watchdog.service"
CRONOPS_WORKER_WATCHDOG_TIMER="$MONITORING_DIR/systemd/hodl-cronops-worker-watchdog.timer"
CRONOPS_LIVE_URL="${HODL_CRONOPS_LIVE_URL:-http://127.0.0.1:8001/api/cronops/live/}"
REQUIRED_WORKER_QUEUES="${HODL_CRONOPS_REQUIRED_WORKER_QUEUES:-financial,rank,analytics,maintenance,fetcher,reconciler}"
EXPECTED_MONITORED_CRONS="${HODL_EXPECTED_MONITORED_CRONS:-31}"
GIT_FETCH_TIMEOUT_SECONDS="${HODL_GIT_FETCH_TIMEOUT_SECONDS:-60}"
CHECK_ONLY=0
START_STOPPED_WORKERS="${HODL_CRONOPS_START_STOPPED_WORKERS:-1}"

case "${1:-}" in
  "") ;;
  --check) CHECK_ONLY=1 ;;
  --start-stopped-workers) START_STOPPED_WORKERS=1 ;;
  *)
    echo "Usage: $0 [--check|--start-stopped-workers]" >&2
    exit 2
    ;;
esac

if [[ "$(id -u)" == "0" ]]; then
  echo "ERROR: do not run this deploy script as root. Use the ubuntu deploy user." >&2
  exit 1
fi

if ! [[ "$GIT_FETCH_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: HODL_GIT_FETCH_TIMEOUT_SECONDS must be a positive whole number." >&2
  exit 2
fi

log() {
  echo
  echo "==> $*"
}

run() {
  echo "+ $*"
  "$@"
}

fetch_repo() {
  local repo_dir="$1"
  local branch="$2"

  echo "+ GIT_TERMINAL_PROMPT=0 timeout $GIT_FETCH_TIMEOUT_SECONDS git -C $repo_dir fetch --prune origin $branch"
  if ! GIT_TERMINAL_PROMPT=0 timeout "$GIT_FETCH_TIMEOUT_SECONDS" \
    git -C "$repo_dir" fetch --prune origin "$branch"; then
    echo "ERROR: could not fetch $repo_dir/$branch within ${GIT_FETCH_TIMEOUT_SECONDS}s." >&2
    echo "Check network access and the repository read credential. Buddy must never wait for an interactive Git prompt." >&2
    return 1
  fi
}

count_cron() {
  local pattern="$1"
  local count
  count="$(crontab -l 2>/dev/null | grep -F -c "$pattern" || true)"
  printf '%s\n' "${count:-0}"
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

local_changes_match_remote() {
  local repo_dir="$1"
  local remote_ref="$2"
  local path
  local -a changed_paths=()
  local -a untracked_paths=()
  local -a mismatched_paths=()

  while IFS= read -r path; do
    [[ -n "$path" ]] && changed_paths+=("$path")
  done < <(
    {
      git -C "$repo_dir" diff --name-only
      git -C "$repo_dir" diff --cached --name-only
    } | sort -u
  )

  while IFS= read -r path; do
    [[ -n "$path" ]] && untracked_paths+=("$path")
  done < <(git -C "$repo_dir" ls-files --others --exclude-standard)

  for path in "${changed_paths[@]}"; do
    if ! git -C "$repo_dir" diff --quiet "$remote_ref" -- "$path"; then
      mismatched_paths+=("$path")
    fi
  done

  for path in "${untracked_paths[@]}"; do
    if ! git -C "$repo_dir" cat-file -e "$remote_ref:$path" 2>/dev/null \
      || ! git -C "$repo_dir" show "$remote_ref:$path" | cmp -s - "$repo_dir/$path"; then
      mismatched_paths+=("$path")
    fi
  done

  if (( ${#mismatched_paths[@]} )); then
    echo "ERROR: $repo_dir has local files that differ from $remote_ref:" >&2
    printf '  %s\n' "${mismatched_paths[@]}" >&2
    return 1
  fi
}

update_repo() {
  local repo_dir="$1"
  local branch="$2"
  local remote_ref="origin/$branch"
  local current_branch
  local local_ahead
  local remote_ahead
  local stash_ref

  fetch_repo "$repo_dir" "$branch"

  current_branch="$(git -C "$repo_dir" branch --show-current)"
  if [[ "$current_branch" != "$branch" ]]; then
    if [[ -n "$(git -C "$repo_dir" status --porcelain --untracked-files=normal)" ]]; then
      echo "ERROR: $repo_dir is on $current_branch with local changes; refusing to switch branches." >&2
      return 1
    fi
    run git -C "$repo_dir" checkout "$branch"
  fi

  read -r local_ahead remote_ahead < <(git -C "$repo_dir" rev-list --left-right --count "HEAD...$remote_ref")
  if [[ "$local_ahead" != "0" ]]; then
    echo "ERROR: $repo_dir has $local_ahead local commit(s) not in $remote_ref." >&2
    echo "Push those commits first; Buddy deploys only code that exists on the remote branch." >&2
    return 1
  fi

  if [[ -n "$(git -C "$repo_dir" status --porcelain --untracked-files=normal)" ]]; then
    if [[ "$remote_ahead" == "0" ]] || ! local_changes_match_remote "$repo_dir" "$remote_ref"; then
      echo "ERROR: $repo_dir has local changes. Commit, move, or deliberately clean them before deployment." >&2
      return 1
    fi

    log "Preserving local files already present in $remote_ref"
    run git -C "$repo_dir" stash push --include-untracked --message "deploy backup before $branch fast-forward $(date -u +%Y%m%dT%H%M%SZ)"
    stash_ref="$(git -C "$repo_dir" stash list -1 --format='%gd')"
    echo "Retained local backup in $repo_dir $stash_ref"
  fi

  run git -C "$repo_dir" pull --ff-only origin "$branch"
}

refresh_monitoring_assets() {
  log "Checking and collecting monitoring static assets"
  (
    cd "$HEALTHCHECKS_DIR"
    export DEBUG=False
    export PYTHONPATH="$MONITORING_DIR/django:${PYTHONPATH:-}"
    run ./venv/bin/python manage.py check
    run ./venv/bin/python manage.py collectstatic --noinput
  )
}

wait_for_cronops_inventory() {
  local attempts="${1:-30}"
  local delay="${2:-2}"
  local attempt

  for attempt in $(seq 1 "$attempts"); do
    if curl -fsS --max-time 5 http://127.0.0.1:8001/api/cronops/inventory/ >/dev/null; then
      return 0
    fi
    if [[ "$attempt" != "$attempts" ]]; then
      sleep "$delay"
    fi
  done

  echo "ERROR: CronOps inventory API was not reachable after $attempts attempt(s)." >&2
  return 1
}

read_cronops_worker_coverage() {
  curl -fsS --max-time 8 "$CRONOPS_LIVE_URL" \
    | HODL_CRONOPS_REQUIRED_WORKER_QUEUES="$REQUIRED_WORKER_QUEUES" python3 -c '
import json
import os
import sys

payload = json.load(sys.stdin)
if not isinstance(payload, dict):
    raise ValueError("CronOps live response is not an object")
coverage_rows = payload.get("worker_coverage")
if not isinstance(coverage_rows, list):
    raise ValueError("CronOps live response has no worker_coverage list")

required = [item.strip() for item in os.environ["HODL_CRONOPS_REQUIRED_WORKER_QUEUES"].split(",") if item.strip()]
coverage = {
    str(item.get("queue_name", "")).strip(): str(item.get("status", "missing")).strip()
    for item in coverage_rows
    if isinstance(item, dict)
}
result = ["{}={}".format(queue_name, coverage.get(queue_name, "missing")) for queue_name in required]
print(", ".join(result))
if any(coverage.get(queue_name) != "running" for queue_name in required):
    raise SystemExit(1)
'
}

wait_for_cronops_worker_coverage() {
  local attempts="${1:-45}"
  local delay="${2:-2}"
  local attempt
  local coverage=""

  for attempt in $(seq 1 "$attempts"); do
    if coverage="$(read_cronops_worker_coverage)"; then
      echo "CronOps worker coverage: $coverage"
      return 0
    fi
    if [[ "$attempt" != "$attempts" ]]; then
      sleep "$delay"
    fi
  done

  echo "ERROR: CronOps worker coverage did not become healthy after $attempts attempt(s): ${coverage:-unavailable}." >&2
  return 1
}

sync_hodl_healthchecks_registry() {
  log "Synchronizing HODL Healthchecks registry from CronOps inventory"
  (
    cd "$HEALTHCHECKS_DIR"
    export DEBUG=False
    export PYTHONPATH="$MONITORING_DIR/django:${PYTHONPATH:-}"
    export HODL_HEALTHCHECKS_REGISTRY_PATH="$MONITORING_DIR/runtime/hodl-cronops-checks.json"
    run ./venv/bin/python manage.py sync_hodl_cronops_checks --apply
  )
}

print_cronops_summary() {
  local attempts="${1:-1}"
  local delay="${2:-2}"
  local attempt

  for attempt in $(seq 1 "$attempts"); do
    if command -v jq >/dev/null 2>&1; then
      if curl -fsS --max-time 8 http://127.0.0.1:8001/api/cronops/live/ \
      | jq '{queue_summary, queue_by_queue, worker_coverage, spool_summary, svr4plus_ingestion}'; then
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
  run pm2 startOrRestart "$MONITORING_DIR/ecosystem.config.js" --only "$name" --update-env
}

install_cronops_worker_watchdog() {
  if [[ ! -f "$CRONOPS_WORKER_WATCHDOG_SERVICE" || ! -f "$CRONOPS_WORKER_WATCHDOG_TIMER" ]]; then
    echo "ERROR: CronOps watchdog unit source files are missing from $MONITORING_DIR/systemd." >&2
    return 1
  fi
  if ! sudo -n true; then
    echo "ERROR: passwordless sudo is required to install the CronOps watchdog timer." >&2
    return 1
  fi

  run sudo -n install -o root -g root -m 0644 \
    "$CRONOPS_WORKER_WATCHDOG_SERVICE" \
    /etc/systemd/system/hodl-cronops-worker-watchdog.service
  run sudo -n install -o root -g root -m 0644 \
    "$CRONOPS_WORKER_WATCHDOG_TIMER" \
    /etc/systemd/system/hodl-cronops-worker-watchdog.timer
  run sudo -n systemctl daemon-reload
  run sudo -n systemctl enable --now hodl-cronops-worker-watchdog.timer
  run sudo -n systemctl is-active --quiet hodl-cronops-worker-watchdog.timer
}

is_truthy() {
  [[ "${1,,}" =~ ^(1|true|yes|on)$ ]]
}

pm2_app_state() {
  local name="$1"
  local state

  if ! state="$(pm2 jlist | node -e '
const apps = JSON.parse(require("fs").readFileSync(0, "utf8"));
const name = process.argv[1];
const app = apps.find((candidate) => candidate.name === name);
process.stdout.write(app ? String((app.pm2_env || {}).status || "unknown") : "missing");
' "$name")"; then
    echo "ERROR: could not determine PM2 state for $name." >&2
    return 2
  fi
  printf '%s\n' "$state"
}

worker_queue_state() {
  local queue_name="$1"
  local state

  if ! state="$(QUEUE_NAME="$queue_name" python manage.py shell -c '
import os
from cronops.models import CronTrigger

active = CronTrigger.objects.filter(
    queue_name=os.environ["QUEUE_NAME"],
    status=CronTrigger.STATUS_RUNNING,
).exists()
print("active" if active else "idle")
' 2>&1)"; then
    echo "ERROR: could not determine active work for queue=$queue_name: $state" >&2
    return 2
  fi
  printf '%s\n' "$state"
}

restart_hodl_worker_if_idle() {
  local process_name="$1"
  local queue_name="$2"
  local state
  local pm2_state

  state="$(worker_queue_state "$queue_name")" || return $?
  if [[ "$state" == "active" ]]; then
    echo "Leaving $process_name online: queue=$queue_name has an active trigger."
    return 0
  fi
  if [[ "$state" != "idle" ]]; then
    echo "ERROR: unexpected queue state for $queue_name: $state" >&2
    return 2
  fi

  pm2_state="$(pm2_app_state "$process_name")" || return $?
  if [[ "$pm2_state" == "stopped" || "$pm2_state" == "missing" ]]; then
    if ! is_truthy "$START_STOPPED_WORKERS"; then
      echo "ERROR: refusing to leave required worker $process_name in $pm2_state state. Set HODL_CRONOPS_START_STOPPED_WORKERS=1." >&2
      return 1
    fi
    echo "Starting required worker $process_name from $pm2_state state."
  fi

  restart_pm2_app "$process_name"
}

log "Entering HODL repo"
cd "$HODL_DIR"

log "Activating virtualenv"
source venv/bin/activate

if [[ "$CHECK_ONLY" == "1" ]]; then
  log "Check mode: no git pull, migrations, crontab writes, or PM2 restarts"
  run python manage.py check
  (
    cd "$HEALTHCHECKS_DIR"
    DEBUG=False PYTHONPATH="$MONITORING_DIR/django:${PYTHONPATH:-}" run ./venv/bin/python manage.py check
  )
  verify_crontab
  run pm2 status
  wait_for_cronops_worker_coverage 1 0
  print_cronops_summary
  exit 0
fi

log "Updating monitoring branch"
update_repo "$MONITORING_DIR" main

log "Updating hodl-backend branch"
update_repo "$HODL_DIR" hodl-backend

cd "$HODL_DIR"
source venv/bin/activate

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

log "Restarting HODL API before binding external Healthchecks"
restart_pm2_app "hodl-backend"
wait_for_cronops_inventory
sync_hodl_healthchecks_registry

log "Removing direct HODL crons and reloading monitored crons"
DJANGO_SETTINGS_MODULE=config.settings python manage.py crontab remove || true
run "$RELOAD_CRONTABS"
verify_crontab

refresh_monitoring_assets

log "Pruning expired raw monitoring cron logs"
run "$MONITORING_DIR/bin/prune-cron-logs.sh" --apply

log "Installing CronOps worker watchdog timer"
install_cronops_worker_watchdog

log "Restarting HODL PM2 processes by name"
restart_hodl_worker_if_idle "hodl-cronops-reconciler" "reconciler"
restart_hodl_worker_if_idle "hodl-cronops-worker-financial" "financial"
restart_hodl_worker_if_idle "hodl-cronops-worker-rank" "rank"
restart_hodl_worker_if_idle "hodl-cronops-worker-analytics" "analytics"
restart_hodl_worker_if_idle "hodl-cronops-worker-maintenance" "maintenance"
restart_hodl_worker_if_idle "hodl-cronops-worker-fetcher" "fetcher"

log "Verifying required CronOps worker lanes"
run "$CRONOPS_WORKER_WATCHDOG"
wait_for_cronops_worker_coverage

restart_pm2_app "healthchecks-web"
restart_pm2_app "infra-alert-worker"
run pm2 save

log "Final CronOps queue summary"
print_cronops_summary 45 2

log "HODL deploy completed"
