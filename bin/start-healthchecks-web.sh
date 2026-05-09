#!/bin/bash
set -euo pipefail

cd /home/ubuntu/monitoring/healthchecks
export PYTHONPATH="/home/ubuntu/monitoring/django:${PYTHONPATH:-}"
exec /home/ubuntu/monitoring/healthchecks/venv/bin/python -m gunicorn hc.wsgi:application --bind 0.0.0.0:9000 --workers 3
