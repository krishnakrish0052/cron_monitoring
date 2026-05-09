#!/usr/bin/env python
"""Wrapper manage.py that uses monitored_settings instead of config.settings."""
import os
import sys

def main():
    os.environ["DJANGO_SETTINGS_MODULE"] = "monitored_settings"
    shared_monitoring_path = "/home/ubuntu/monitoring/django"
    if shared_monitoring_path not in sys.path:
        sys.path.insert(0, shared_monitoring_path)

    # Add project-specific monitoring wrapper path so monitored_settings can be found
    monitoring_path = os.path.dirname(os.path.abspath(__file__))
    if monitoring_path not in sys.path:
        sys.path.insert(0, monitoring_path)

    # Ensure the ak1111-backend project root is on sys.path so config.settings is importable
    project_root = "/home/ubuntu/ak1111-backend"
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()
