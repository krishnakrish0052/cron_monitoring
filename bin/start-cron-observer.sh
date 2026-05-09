#!/bin/bash
set -euo pipefail

export PYTHONPATH="/home/ubuntu/monitoring/django:${PYTHONPATH:-}"
export MONITORING_ROOT="/home/ubuntu/monitoring"
export MONITORING_RUNTIME_ROOT="/home/ubuntu/monitoring/runtime/observer"
export MONITORING_CRON_LOG_ROOT="/home/ubuntu/monitoring/logs/crons"

cd /home/ubuntu/monitoring
exec /home/ubuntu/monitoring/healthchecks/venv/bin/python -m monitoring_observer.service
