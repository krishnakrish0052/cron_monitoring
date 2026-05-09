#!/bin/bash
set -euo pipefail

export PYTHONPATH="/home/ubuntu/monitoring/django:${PYTHONPATH:-}"
export DB_MAINTENANCE_RUNTIME="/home/ubuntu/monitoring/runtime/db-maintenance"

cd /home/ubuntu/monitoring
exec /home/ubuntu/monitoring/healthchecks/venv/bin/python -m db_maintenance.worker

