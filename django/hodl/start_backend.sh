#!/bin/bash
set -euo pipefail

# The deploy/reload script is the only owner of crontab writes.  Restarting
# the web process must never create a window where scheduled jobs disappear.

export PYTHONPATH="/home/ubuntu/monitoring/django/hodl:/home/ubuntu/monitoring/django:$PYTHONPATH"
export DJANGO_SETTINGS_MODULE="monitored_settings"
export HODL_HEALTHCHECKS_REGISTRY_PATH="${HODL_HEALTHCHECKS_REGISTRY_PATH:-/home/ubuntu/monitoring/runtime/hodl-cronops-checks.json}"

cd /home/ubuntu/hodlbackend2/HODL-2025
source venv/bin/activate

export DJANGO_DEBUG="${DJANGO_DEBUG:-false}"

workers="${HODL_GUNICORN_WORKERS:-2}"
threads="${HODL_GUNICORN_THREADS:-4}"
timeout="${HODL_GUNICORN_TIMEOUT_SECONDS:-90}"
graceful_timeout="${HODL_GUNICORN_GRACEFUL_TIMEOUT_SECONDS:-30}"
max_requests="${HODL_GUNICORN_MAX_REQUESTS:-2000}"
max_requests_jitter="${HODL_GUNICORN_MAX_REQUESTS_JITTER:-200}"
log_level="${HODL_GUNICORN_LOG_LEVEL:-info}"

exec ./venv/bin/gunicorn config.wsgi:application \
  --bind 0.0.0.0:8001 \
  --worker-class gthread \
  --workers "$workers" \
  --threads "$threads" \
  --timeout "$timeout" \
  --graceful-timeout "$graceful_timeout" \
  --max-requests "$max_requests" \
  --max-requests-jitter "$max_requests_jitter" \
  --access-logfile - \
  --error-logfile - \
  --log-level "$log_level"
