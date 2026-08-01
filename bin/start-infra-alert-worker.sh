#!/bin/bash
set -euo pipefail

export PYTHONPATH="/home/ubuntu/monitoring/django:${PYTHONPATH:-}"
export DEBUG="False"
export MONITORING_INFRA_ALERT_RUNTIME="/home/ubuntu/monitoring/runtime/infra-alerts"
export MONITORING_OBSERVER_STATE_PATH="/home/ubuntu/monitoring/runtime/observer/state.json"

cd /home/ubuntu/monitoring
exec /home/ubuntu/monitoring/healthchecks/venv/bin/python -m infra_alerts.service
