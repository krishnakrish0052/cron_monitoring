#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${1:---dry-run}"
LOG_ROOT="${MONITORING_CRON_LOG_ROOT:-/home/ubuntu/monitoring/logs/crons}"
DETAIL_DAYS="${MONITORING_CRON_DETAIL_RETENTION_DAYS:-14}"
METADATA_DAYS="${MONITORING_CRON_METADATA_RETENTION_DAYS:-90}"
SAFE_ROOT="/home/ubuntu/monitoring/logs/crons"

usage() {
  cat <<'EOF'
Usage: prune-cron-logs.sh [--dry-run|--apply]

Retains raw cron event and stdout/stderr files for 14 days by default, and
run metadata JSON files for 90 days. It never touches cronops database data.
EOF
}

case "$MODE" in
  --dry-run|--apply) ;;
  --help|-h)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

if ! [[ "$DETAIL_DAYS" =~ ^[0-9]+$ && "$METADATA_DAYS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: retention days must be non-negative integers." >&2
  exit 2
fi

LOG_ROOT="$(realpath -m "$LOG_ROOT")"
case "$LOG_ROOT" in
  "$SAFE_ROOT"|"$SAFE_ROOT"/*) ;;
  *)
    echo "ERROR: refusing to prune outside $SAFE_ROOT: $LOG_ROOT" >&2
    exit 2
    ;;
esac

if [[ ! -d "$LOG_ROOT" ]]; then
  echo "ERROR: cron log root does not exist: $LOG_ROOT" >&2
  exit 1
fi

exec 9>"$LOG_ROOT/.prune.lock"
if ! flock -n 9; then
  echo "cron log pruning is already running; nothing to do"
  exit 0
fi

find_detail() {
  find "$LOG_ROOT" -xdev -type f \( -name '*.events.jsonl' -o -name '*.log' \) -mtime +"$DETAIL_DAYS" "$@"
}

find_metadata() {
  find "$LOG_ROOT" -xdev -type f -name '*.json' -mtime +"$METADATA_DAYS" "$@"
}

summarize() {
  local label="$1"
  shift
  local totals
  totals="$("$@" -printf '%s\n' | awk '{bytes += $1; count += 1} END {printf "%d %.0f", count, bytes}')"
  printf '%s files=%s bytes=%s\n' "$label" "${totals%% *}" "${totals##* }"
}

echo "cron log root: $LOG_ROOT"
echo "mode: $MODE"
echo "detail retention days: $DETAIL_DAYS"
echo "metadata retention days: $METADATA_DAYS"
summarize "expired detail" find_detail
summarize "expired metadata" find_metadata

if [[ "$MODE" == "--dry-run" ]]; then
  echo "dry run only; rerun with --apply to delete the expired raw files"
  exit 0
fi

find_detail -delete
find_metadata -delete
find "$LOG_ROOT" -xdev -depth -mindepth 1 -type d -empty -delete
echo "cron log pruning completed"
