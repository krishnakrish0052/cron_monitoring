#!/bin/bash
set -euo pipefail

HODL_DIR="/home/ubuntu/hodlbackend2/HODL-2025"
MONITORING_DIR="/home/ubuntu/monitoring"
HEALTHCHECKS_DIR="$MONITORING_DIR/healthchecks"
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

  run git -C "$repo_dir" fetch origin "$branch"

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
    echo "ERROR: $repo_dir has $local_ahead local commit(s) not in $remote_ref. Push or merge them before deployment." >&2
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
      | jq '{queue_summary, queue_by_queue, spool_summary, svr4plus_ingestion}'; then
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

log "Restarting HODL PM2 processes by name"
restart_pm2_app "hodl-cronops-worker-financial"
restart_pm2_app "hodl-cronops-worker-rank"
restart_pm2_app "hodl-cronops-worker-analytics"
restart_pm2_app "hodl-cronops-worker-maintenance"
restart_pm2_app "hodl-cronops-worker-fetcher"
restart_pm2_app "healthchecks-web"
run pm2 save

log "Final CronOps queue summary"
print_cronops_summary 45 2

log "HODL deploy completed"
