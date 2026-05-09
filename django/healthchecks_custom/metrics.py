from __future__ import annotations

from collections import Counter

from django.http import HttpResponse

from hc.accounts.models import Project
from hc.api.models import Check


def render_monitoring_metrics(project_configs: list[dict[str, str]]) -> HttpResponse:
    project_codes = [item["code"] for item in project_configs]
    projects = {
        str(project.code): project
        for project in Project.objects.filter(code__in=project_codes)
    }
    config_by_code = {item["code"]: item for item in project_configs}

    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    metric_defs = (
        ("hc_monitoring_check_up", "Whether the monitored check is up."),
        ("hc_monitoring_check_down", "Whether the monitored check is down."),
        ("hc_monitoring_check_grace", "Whether the monitored check is in grace."),
        ("hc_monitoring_check_started", "Whether the monitored check is currently running."),
        ("hc_monitoring_check_last_ping_timestamp_seconds", "Last successful ping timestamp."),
        ("hc_monitoring_check_last_duration_seconds", "Last observed check duration."),
        ("hc_monitoring_checks_total", "Monitored check counts by project and status."),
    )

    lines = []
    for name, help_text in metric_defs:
        lines.append(f"# HELP {name} {help_text}\n")
        lines.append(f"# TYPE {name} gauge\n")

    for code, project in projects.items():
        project_name = config_by_code[code]["name"]
        checks = Check.objects.filter(project=project).order_by("id")
        counts = Counter()
        for check in checks:
            status = check.get_status()
            counts[status] += 1
            labels = (
                f'project="{esc(project_name)}",'
                f'check="{esc(check.name_then_code())}",'
                f'tags="{esc(check.tags)}",'
                f'code="{check.code}"'
            )
            lines.append(f"hc_monitoring_check_up{{{labels}}} {1 if status == 'up' else 0}\n")
            lines.append(f"hc_monitoring_check_down{{{labels}}} {1 if status == 'down' else 0}\n")
            lines.append(f"hc_monitoring_check_grace{{{labels}}} {1 if status == 'grace' else 0}\n")
            lines.append(f"hc_monitoring_check_started{{{labels}}} {1 if check.last_start else 0}\n")
            if check.last_ping:
                lines.append(
                    f"hc_monitoring_check_last_ping_timestamp_seconds{{{labels}}} "
                    f"{int(check.last_ping.timestamp())}\n"
                )
            if check.last_duration:
                lines.append(
                    f"hc_monitoring_check_last_duration_seconds{{{labels}}} "
                    f"{check.last_duration.total_seconds():.3f}\n"
                )

        for status in ("up", "down", "grace", "new", "paused"):
            lines.append(
                f'hc_monitoring_checks_total{{project="{esc(project_name)}",'
                f'status="{status}"}} {counts[status]}\n'
            )

    return HttpResponse(lines, content_type="text/plain; version=0.0.4")
