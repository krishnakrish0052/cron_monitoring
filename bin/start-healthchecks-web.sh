#!/bin/bash
set -euo pipefail

cd /home/ubuntu/monitoring/healthchecks
export DEBUG="False"
export PYTHONPATH="/home/ubuntu/monitoring/django:${PYTHONPATH:-}"
exec /home/ubuntu/monitoring/healthchecks/venv/bin/python -m gunicorn hc.wsgi:application \
  --bind 0.0.0.0:9000 \
  --worker-class gthread \
  --workers 3 \
  --threads 8 \
  --timeout 60 \
  --graceful-timeout 30
