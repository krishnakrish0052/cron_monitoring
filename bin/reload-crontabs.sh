#!/bin/bash
set -euo pipefail

LOCK_FILE="/tmp/reload-crontabs.lock"
CHECK_ONLY=0

if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=1
fi

render_project_crons() {
  local python_bin="$1"
  local project_root="$2"
  local monitor_root="$3"

  "$python_bin" - "$project_root" "$monitor_root" <<'PY'
import contextlib
import os
import sys

project_root = sys.argv[1]
monitor_root = sys.argv[2]
shared_monitoring_path = "/home/ubuntu/monitoring/django"

for path in (shared_monitoring_path, monitor_root, project_root):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ["DJANGO_SETTINGS_MODULE"] = "monitored_settings"

real_stdout = sys.stdout
with contextlib.redirect_stdout(sys.stderr):
    import django
    from django_crontab.crontab import Crontab

    django.setup()
    crontab = Crontab(verbosity=0, readonly=True)
    crontab.crontab_lines = []
    crontab.add_jobs()

real_stdout.write("".join(crontab.crontab_lines))
PY
}

count_pattern() {
  local path="$1"
  local pattern="$2"
  grep -F -c "$pattern" "$path" || true
}

verify_file() {
  local path="$1"
  local direct_hodl
  local monitored_hodl

  direct_hodl="$(count_pattern "$path" "/home/ubuntu/hodlbackend2/HODL-2025/manage.py crontab run")"
  monitored_hodl="$(count_pattern "$path" "/home/ubuntu/monitoring/django/hodl/manage_monitored.py")"

  echo "direct HODL crons: ${direct_hodl:-0}"
  echo "monitored HODL crons: ${monitored_hodl:-0}"

  if [[ "${direct_hodl:-0}" != "0" ]]; then
    echo "ERROR: direct HODL crons must be 0." >&2
    return 1
  fi
  if [[ "${monitored_hodl:-0}" != "31" ]]; then
    echo "ERROR: monitored HODL crons must be 31." >&2
    return 1
  fi
}

main() {
  local tmp_current
  local tmp_next
  local tmp_rendered

  tmp_current="$(mktemp)"
  tmp_next="$(mktemp)"
  tmp_rendered="$(mktemp)"
  trap 'rm -f "$tmp_current" "$tmp_next" "$tmp_rendered"' EXIT

  crontab -l >"$tmp_current" 2>/dev/null || true

  grep -vF '# ak1111-backend' "$tmp_current" \
    | grep -vF '# hodl-2025' \
    | grep -vF '/home/ubuntu/hodlbackend2/HODL-2025/manage.py crontab run' \
    | grep -vF '/home/ubuntu/ak1111-backend/manage.py crontab run' \
    >"$tmp_next" || true

  render_project_crons \
    "/home/ubuntu/ak1111-backend/venv/bin/python" \
    "/home/ubuntu/ak1111-backend" \
    "/home/ubuntu/monitoring/django/ak1111" \
    >>"$tmp_rendered"

  render_project_crons \
    "/home/ubuntu/hodlbackend2/HODL-2025/venv/bin/python" \
    "/home/ubuntu/hodlbackend2/HODL-2025" \
    "/home/ubuntu/monitoring/django/hodl" \
    >>"$tmp_rendered"

  cat "$tmp_rendered" >>"$tmp_next"
  verify_file "$tmp_next"

  if [[ "$CHECK_ONLY" == "1" ]]; then
    echo "check mode: rendered crontab is valid; not installing"
    return 0
  fi

  crontab "$tmp_next"
  echo "installed monitored crontab atomically"
}

flock "$LOCK_FILE" bash -c "$(declare -f render_project_crons count_pattern verify_file main); CHECK_ONLY=$CHECK_ONLY; main"
