"""
Wrapper settings for HODL-2025 with monitoring support.
Lives outside the git repo - zero main codebase changes.
"""

from config.settings import *  # noqa: F401,F403

CRONTAB_COMMENT = "hodl-2025"
CRONTAB_DJANGO_MANAGE_PATH = "/home/ubuntu/monitoring/django/hodl/manage_monitored.py"

INSTALLED_APPS = list(INSTALLED_APPS) + ["monitoring"]  # noqa: F405
