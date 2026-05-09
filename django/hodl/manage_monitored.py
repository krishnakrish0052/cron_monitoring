#!/usr/bin/env python
"""Wrapper manage.py for HODL-2025 that uses monitored_settings."""
import os
import sys

def main():
    os.environ["DJANGO_SETTINGS_MODULE"] = "monitored_settings"
    shared_monitoring_path = "/home/ubuntu/monitoring/django"
    if shared_monitoring_path not in sys.path:
        sys.path.insert(0, shared_monitoring_path)

    monitoring_path = os.path.dirname(os.path.abspath(__file__))
    if monitoring_path not in sys.path:
        sys.path.insert(0, monitoring_path)

    # Ensure the HODL-2025 project root is on sys.path so config.settings is importable
    project_root = "/home/ubuntu/hodlbackend2/HODL-2025"
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()
