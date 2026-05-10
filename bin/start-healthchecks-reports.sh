#!/bin/bash
set -euo pipefail

cd /home/ubuntu/monitoring/healthchecks
export DEBUG="False"
export PYTHONPATH="/home/ubuntu/monitoring/django:${PYTHONPATH:-}"
exec /home/ubuntu/monitoring/healthchecks/venv/bin/python manage.py sendreports --loop
