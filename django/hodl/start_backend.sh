#!/bin/bash
# Wrapper for HODL-2025 that adds monitoring without modifying main codebase.

export PYTHONPATH="/home/ubuntu/monitoring/django/hodl:/home/ubuntu/monitoring/django:$PYTHONPATH"
export DJANGO_SETTINGS_MODULE="monitored_settings"

cd /home/ubuntu/hodlbackend2/HODL-2025
source venv/bin/activate

# Re-register crontab with monitoring wrapper
python /home/ubuntu/monitoring/django/hodl/manage_monitored.py crontab add

# Start Django server
exec python3 manage.py runserver 0.0.0.0:8001
