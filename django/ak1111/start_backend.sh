#!/bin/bash
# Wrapper script that adds monitoring support without modifying the main codebase.

export PYTHONPATH="/home/ubuntu/monitoring/django/ak1111:/home/ubuntu/monitoring/django:$PYTHONPATH"
export DJANGO_SETTINGS_MODULE="monitored_settings"

cd /home/ubuntu/ak1111-backend
source venv/bin/activate

# Install dependencies and run migrations (same as original bashscript.sh)
pip install -r requirements.txt

python manage.py migrate

# Use our wrapper manage.py for crontab (includes CRONTAB_COMMENT and CRONTAB_DJANGO_MANAGE_PATH)
python /home/ubuntu/monitoring/django/ak1111/manage_monitored.py crontab add

# Start Django server
exec python manage.py runserver 0.0.0.0:8000
