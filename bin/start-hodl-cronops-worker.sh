#!/bin/bash
set -euo pipefail

# The database-outage spool can contain trigger metadata. Create it private
# from the first filesystem operation; cronops.spool enforces the same mode.
umask 077

cd /home/ubuntu/hodlbackend2/HODL-2025
source venv/bin/activate

export DJANGO_SETTINGS_MODULE="config.settings"
export HODL_CRONOPS_QUEUE_ENABLED="${HODL_CRONOPS_QUEUE_ENABLED:-1}"
export HODL_CRONOPS_SPOOL_DIR="${HODL_CRONOPS_SPOOL_DIR:-/home/ubuntu/monitoring/runtime/hodl-cronops-spool}"
export HODL_HEALTHCHECKS_REGISTRY_PATH="${HODL_HEALTHCHECKS_REGISTRY_PATH:-/home/ubuntu/monitoring/runtime/hodl-cronops-checks.json}"

mkdir -p "$HODL_CRONOPS_SPOOL_DIR/pending" "$HODL_CRONOPS_SPOOL_DIR/processed" "$HODL_CRONOPS_SPOOL_DIR/failed"

poll_seconds="${HODL_CRONOPS_WORKER_POLL_SECONDS:-5}"
capacity="${HODL_CRONOPS_WORKER_CAPACITY:-1}"
reconcile_seconds="${HODL_CRONOPS_RECONCILE_EVERY_SECONDS:-60}"
spool_seconds="${HODL_CRONOPS_SPOOL_REPLAY_EVERY_SECONDS:-60}"
spool_limit="${HODL_CRONOPS_SPOOL_REPLAY_LIMIT:-20}"
queues="${HODL_CRONOPS_WORKER_QUEUES:-}"
auto_reconcile="${HODL_CRONOPS_AUTO_RECONCILE:-0}"
spool_replay_enabled="${HODL_CRONOPS_SPOOL_REPLAY_ENABLED:-0}"

args=(
  manage.py
  cronops_worker
  --poll-seconds "$poll_seconds"
  --capacity "$capacity"
  --reconcile-every-seconds "$reconcile_seconds"
  --spool-replay-every-seconds "$spool_seconds"
  --spool-replay-limit "$spool_limit"
)

IFS=',' read -ra queue_list <<< "$queues"
for queue in "${queue_list[@]}"; do
  queue="$(echo "$queue" | xargs)"
  if [[ -n "$queue" ]]; then
    args+=(--queue "$queue")
  fi
done

if [[ ! "${auto_reconcile,,}" =~ ^(1|true|yes|on)$ ]]; then
  args+=(--no-auto-reconcile)
fi

if [[ ! "${spool_replay_enabled,,}" =~ ^(1|true|yes|on)$ ]]; then
  args+=(--no-spool-replay)
fi

exec python "${args[@]}"
