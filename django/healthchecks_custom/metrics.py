from __future__ import annotations

import json
import os
import urllib.request
from collections import Counter

from django.http import HttpResponse

from hc.accounts.models import Project
from hc.api.models import Check


HODL_CRONOPS_URL = os.environ.get("HODL_CRONOPS_URL", "http://127.0.0.1:8001/api/cronops/live/")


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
        ("hodl_cronops_queue_total", "HODL cronops queue counts by status."),
        ("hodl_cronops_queue_by_queue_total", "HODL cronops queue counts by lane and status."),
        ("hodl_cronops_spool_files", "HODL cronops DB-outage spool files by state."),
        ("hodl_cronops_resource_deferred_24h", "HODL cronops resource deferred runs in 24h."),
        ("hodl_cronops_failed_24h", "HODL cronops failed runs in 24h."),
        ("hodl_cronops_timeouts_24h", "HODL cronops timeout runs in 24h."),
        ("hodl_cronops_stale_count_24h", "HODL cronops stale runs in 24h."),
        ("hodl_cronops_workers", "HODL cronops workers by status."),
        ("hodl_cronops_workers_by_queue", "HODL cronops workers by queue lane and status."),
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

    hodl = _fetch_hodl_cronops()
    if hodl:
        summary = hodl.get("queue_summary") or {}
        for status, value in summary.items():
            if isinstance(value, (int, float)):
                lines.append(f'hodl_cronops_queue_total{{status="{esc(str(status))}"}} {value}\n')
        for row in hodl.get("queue_by_queue", []) or []:
            queue = row.get("queue_name") or row.get("queue") or "unknown"
            status = row.get("status") or "unknown"
            value = row.get("total", row.get("count", 0)) or 0
            if isinstance(value, (int, float)):
                lines.append(
                    f'hodl_cronops_queue_by_queue_total{{queue="{esc(str(queue))}",'
                    f'status="{esc(str(status))}"}} {value}\n'
                )
        spool = hodl.get("spool_summary") or {}
        for state in ("pending", "processed", "failed"):
            value = spool.get(state, 0) or 0
            if isinstance(value, (int, float)):
                lines.append(f'hodl_cronops_spool_files{{state="{state}"}} {value}\n')
        for key, metric in (
            ("resource_deferred_24h", "hodl_cronops_resource_deferred_24h"),
            ("failed_24h", "hodl_cronops_failed_24h"),
            ("timeouts_24h", "hodl_cronops_timeouts_24h"),
            ("stale_count_24h", "hodl_cronops_stale_count_24h"),
        ):
            lines.append(f"{metric} {hodl.get(key, 0) or 0}\n")
        worker_counts = Counter((worker.get("status") or "unknown") for worker in hodl.get("workers", []))
        for status, count in worker_counts.items():
            lines.append(f'hodl_cronops_workers{{status="{esc(status)}"}} {count}\n')
        worker_lane_counts = Counter()
        for worker in hodl.get("workers", []):
            status = worker.get("status") or "unknown"
            metadata = worker.get("metadata") or {}
            queues = metadata.get("queues") or ["*"]
            for queue in queues:
                worker_lane_counts[(str(queue or "*"), status)] += 1
        for (queue, status), count in worker_lane_counts.items():
            lines.append(
                f'hodl_cronops_workers_by_queue{{queue="{esc(queue)}",status="{esc(status)}"}} {count}\n'
            )

    return HttpResponse(lines, content_type="text/plain; version=0.0.4")


def _fetch_hodl_cronops() -> dict:
    try:
        with urllib.request.urlopen(HODL_CRONOPS_URL, timeout=1.5) as response:
            return json.loads(response.read().decode())
    except Exception:
        return {}
