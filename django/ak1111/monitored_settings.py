"""
Wrapper settings that extend config.settings with monitoring support.
This file lives outside the git repo so no main codebase changes are needed.
"""

# Import everything from the original settings
from config.settings import *  # noqa: F401,F403

# Add unique crontab comment to avoid conflicts with other projects
CRONTAB_COMMENT = "ak1111-backend"

# Point crontab entries to our wrapper manage.py so crons also use monitored settings
CRONTAB_DJANGO_MANAGE_PATH = "/home/ubuntu/monitoring/django/ak1111/manage_monitored.py"

# Add monitoring app
INSTALLED_APPS = list(INSTALLED_APPS) + ["monitoring"]  # noqa: F405
