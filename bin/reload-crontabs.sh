#!/bin/bash
set -euo pipefail

cd /home/ubuntu/ak1111-backend
source venv/bin/activate
python /home/ubuntu/monitoring/django/ak1111/manage_monitored.py crontab remove || true
python /home/ubuntu/monitoring/django/ak1111/manage_monitored.py crontab add

cd /home/ubuntu/hodlbackend2/HODL-2025
source venv/bin/activate
python /home/ubuntu/monitoring/django/hodl/manage_monitored.py crontab remove || true
python /home/ubuntu/monitoring/django/hodl/manage_monitored.py crontab add
