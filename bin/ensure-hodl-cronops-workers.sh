#!/bin/bash
set -euo pipefail

# A PM2 process can be explicitly stopped without triggering PM2 autorestart.
# This watchdog restores only unhealthy CronOps lane processes and proves that
# each one has written a fresh heartbeat before declaring the system healthy.
umask 077

MONITORING_DIR="/home/ubuntu/monitoring"
ECOSYSTEM_FILE="$MONITORING_DIR/ecosystem.config.js"
LIVE_URL="${HODL_CRONOPS_LIVE_URL:-http://127.0.0.1:8001/api/cronops/live/}"
REQUIRED_WORKER_QUEUES="${HODL_CRONOPS_REQUIRED_WORKER_QUEUES:-financial,rank,analytics,maintenance,fetcher,reconciler}"
LOCK_FILE="${HODL_CRONOPS_WORKER_WATCHDOG_LOCK:-$MONITORING_DIR/runtime/hodl-cronops-worker-watchdog.lock}"
ATTEMPTS="${HODL_CRONOPS_WORKER_WATCHDOG_ATTEMPTS:-18}"
DELAY_SECONDS="${HODL_CRONOPS_WORKER_WATCHDOG_DELAY_SECONDS:-5}"
CHECK_ONLY=0
CHANGED=0

case "${1:-}" in
  "") ;;
  --check) CHECK_ONLY=1 ;;
  *)
    echo "Usage: $0 [--check]" >&2
    exit 2
    ;;
esac

if ! [[ "$ATTEMPTS" =~ ^[1-9][0-9]*$ && "$DELAY_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "ERROR: watchdog attempts and delay must be positive numeric values." >&2
  exit 2
fi

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH}"
PM2_BIN="${PM2_BIN:-$(command -v pm2 || true)}"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"

for binary in "$PM2_BIN" "$NODE_BIN" "$PYTHON_BIN"; do
  if [[ -z "$binary" || ! -x "$binary" ]]; then
    echo "ERROR: required executable is unavailable: ${binary:-unknown}" >&2
    exit 1
  fi
done

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "Another CronOps worker watchdog invocation is already running."
  exit 0
fi

IFS=',' read -r -a QUEUES <<< "$REQUIRED_WORKER_QUEUES"
PROCESSES=(
  "hodl-cronops-worker-financial"
  "hodl-cronops-worker-rank"
  "hodl-cronops-worker-analytics"
  "hodl-cronops-worker-maintenance"
  "hodl-cronops-worker-fetcher"
  "hodl-cronops-reconciler"
)
EXPECTED_QUEUES=(financial rank analytics maintenance fetcher reconciler)

if [[ "${QUEUES[*]}" != "${EXPECTED_QUEUES[*]}" ]]; then
  echo "ERROR: HODL_CRONOPS_REQUIRED_WORKER_QUEUES must match the configured lane processes." >&2
  exit 2
fi

pm2_app_state() {
  local name="$1"
  "$PM2_BIN" jlist | "$NODE_BIN" -e '
const apps = JSON.parse(require("fs").readFileSync(0, "utf8"));
const name = process.argv[1];
const app = apps.find((candidate) => candidate.name === name);
process.stdout.write(app ? String((app.pm2_env || {}).status || "unknown") : "missing");
' "$name"
}

coverage_lines() {
  curl -fsS --max-time 8 "$LIVE_URL" \
    | HODL_CRONOPS_REQUIRED_WORKER_QUEUES="$REQUIRED_WORKER_QUEUES" "$PYTHON_BIN" -c '
import json
import os
import sys

payload = json.load(sys.stdin)
if not isinstance(payload, dict):
    raise ValueError("CronOps live response is not an object")
rows = payload.get("worker_coverage")
if not isinstance(rows, list):
    raise ValueError("CronOps live response has no worker_coverage list")

required = [item.strip() for item in os.environ["HODL_CRONOPS_REQUIRED_WORKER_QUEUES"].split(",") if item.strip()]
coverage = {
    str(item.get("queue_name", "")).strip(): str(item.get("status", "missing")).strip()
    for item in rows
    if isinstance(item, dict)
}
for queue_name in required:
    print("{}\t{}".format(queue_name, coverage.get(queue_name, "missing")))
'
}

declare -A COVERAGE=()
declare -A RESTARTED_STALE=()

read_live_coverage() {
  local rows
  local queue_name
  local status

  if ! rows="$(coverage_lines)"; then
    return 1
  fi
  COVERAGE=()
  while IFS=$'\t' read -r queue_name status; do
    [[ -n "$queue_name" ]] && COVERAGE["$queue_name"]="$status"
  done <<< "$rows"
}

coverage_healthy() {
  local queue_name
  for queue_name in "${QUEUES[@]}"; do
    [[ "${COVERAGE[$queue_name]:-missing}" == "running" ]] || return 1
  done
}

coverage_summary() {
  local queue_name
  local result=()
  for queue_name in "${QUEUES[@]}"; do
    result+=("$queue_name=${COVERAGE[$queue_name]:-missing}")
  done
  local IFS=", "
  printf '%s' "${result[*]}"
}

start_non_online_processes() {
  local index
  local process_name
  local state

  for index in "${!PROCESSES[@]}"; do
    process_name="${PROCESSES[$index]}"
    state="$(pm2_app_state "$process_name")" || {
      log "Could not read PM2 state for $process_name."
      return 1
    }
    if [[ "$state" != "online" ]]; then
      log "Starting required worker $process_name from PM2 state=$state."
      "$PM2_BIN" startOrRestart "$ECOSYSTEM_FILE" --only "$process_name" --update-env
      CHANGED=1
    fi
  done
}

restart_stale_online_processes() {
  local index
  local process_name
  local queue_name
  local state

  for index in "${!PROCESSES[@]}"; do
    process_name="${PROCESSES[$index]}"
    queue_name="${QUEUES[$index]}"
    [[ "${COVERAGE[$queue_name]:-missing}" == "running" ]] && continue
    [[ -n "${RESTARTED_STALE[$process_name]:-}" ]] && continue

    state="$(pm2_app_state "$process_name")" || return 1
    if [[ "$state" == "online" ]]; then
      log "Restarting unresponsive worker $process_name for queue=$queue_name (coverage=${COVERAGE[$queue_name]:-missing})."
      "$PM2_BIN" startOrRestart "$ECOSYSTEM_FILE" --only "$process_name" --update-env
      RESTARTED_STALE["$process_name"]=1
      CHANGED=1
    fi
  done
}

if [[ "$CHECK_ONLY" == "1" ]]; then
  if ! read_live_coverage; then
    echo "ERROR: CronOps live API is unavailable at $LIVE_URL." >&2
    exit 1
  fi
  if ! coverage_healthy; then
    echo "ERROR: CronOps worker coverage is unhealthy: $(coverage_summary)." >&2
    exit 1
  fi
  log "CronOps worker coverage is healthy: $(coverage_summary)."
  exit 0
fi

start_non_online_processes

for attempt in $(seq 1 "$ATTEMPTS"); do
  if read_live_coverage; then
    if coverage_healthy; then
      if [[ "$CHANGED" == "1" ]]; then
        "$PM2_BIN" save
      fi
      log "CronOps worker coverage is healthy: $(coverage_summary)."
      exit 0
    fi
    log "CronOps worker coverage is unhealthy: $(coverage_summary)."
    if (( attempt >= 3 )); then
      restart_stale_online_processes
    fi
  else
    log "CronOps live API is unavailable at $LIVE_URL. Required workers were started but health cannot be confirmed."
  fi

  if [[ "$attempt" != "$ATTEMPTS" ]]; then
    sleep "$DELAY_SECONDS"
    start_non_online_processes
  fi
done

echo "ERROR: CronOps worker coverage did not become healthy after $ATTEMPTS attempt(s)." >&2
exit 1
