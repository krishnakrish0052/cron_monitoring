from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import timezone
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from cronsim import CronSim
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils.timezone import now

from hc.accounts.http import AuthenticatedHttpRequest
from hc.accounts.models import Profile, Project
from hc.api.models import Check, Flip, prepare_durations
from hc.front.views import _get_check_for_user
from healthchecks_custom.metrics import render_monitoring_metrics
from monitoring_common.cron_logs import iter_runs_for_uuid, read_run_events, read_run_log
from monitoring_observer.collector import read_state


MONITORING_PROJECTS = (
    {
        "name": "ak1111-backend",
        "code": "ba51525e-fd39-4fc0-8967-1d7f7a235db5",
        "health_url": "http://127.0.0.1:8000/api/monitoring/health/",
    },
    {
        "name": "HODL-2025",
        "code": "08c7b7c6-776d-4bba-b6f7-2592efa14281",
        "health_url": "http://127.0.0.1:8001/api/monitoring/health/",
    },
)

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090")
IST = ZoneInfo("Asia/Kolkata")


def _monitoring_config() -> list[dict[str, str]]:
    return list(getattr(settings, "MONITORING_PROJECTS", MONITORING_PROJECTS))


def _local_only(request: HttpRequest) -> bool:
    host = request.META.get("REMOTE_ADDR", "")
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    return host in ("127.0.0.1", "::1") or forwarded in ("127.0.0.1", "::1")


def _fetch_json(url: str, timeout: float = 3) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except Exception as exc:
        return {"status": "unreachable", "error": str(exc), "checks": {}}


def _prom_query(query: str) -> list[dict]:
    params = urlencode({"query": query})
    url = f"{PROMETHEUS_URL}/api/v1/query?{params}"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            payload = json.loads(response.read().decode())
    except Exception:
        return []

    if payload.get("status") != "success":
        return []
    return payload.get("data", {}).get("result", [])


def _prom_query_range(query: str, seconds: int = 3600, step: int = 60) -> list[dict]:
    end = int(time.time())
    params = urlencode(
        {
            "query": query,
            "start": end - seconds,
            "end": end,
            "step": step,
        }
    )
    url = f"{PROMETHEUS_URL}/api/v1/query_range?{params}"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            payload = json.loads(response.read().decode())
    except Exception:
        return []

    if payload.get("status") != "success":
        return []
    return payload.get("data", {}).get("result", [])


def _series_values(result: list[dict]) -> list[dict]:
    if not result:
        return []
    values = result[0].get("values", [])
    points = []
    for ts, value in values:
        try:
            timestamp = int(float(ts))
            points.append(
                {
                    "ts": timestamp,
                    "ts_ist": datetime_from_ts(timestamp).astimezone(IST).isoformat(),
                    "value": round(float(value), 4),
                }
            )
        except (TypeError, ValueError):
            continue
    return points


def datetime_from_ts(timestamp: int):
    from datetime import datetime

    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _instant_value(query: str) -> float | None:
    result = _prom_query(query)
    if not result:
        return None
    value = result[0].get("value", [None, None])[1]
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stats(points: list[dict]) -> dict:
    values = [point["value"] for point in points]
    if not values:
        return {"current": None, "min": None, "max": None, "avg": None}
    return {
        "current": round(values[-1], 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "avg": round(sum(values) / len(values), 2),
    }


def _series_from_state(key: str, state: dict) -> list[dict]:
    points = []
    for point in state.get("server_series", []):
        value = point.get(key)
        if value is None:
            continue
        points.append(
            {
                "ts": point.get("ts"),
                "ts_ist": point.get("at_ist"),
                "value": round(float(value), 4),
            }
        )
    return points[-3600:]


def _bytes_payload(used_query: str, total_query: str, free_query: str) -> dict:
    used = _instant_value(used_query)
    total = _instant_value(total_query)
    free = _instant_value(free_query)
    return {
        "used_bytes": round(used) if used is not None else None,
        "total_bytes": round(total) if total is not None else None,
        "free_bytes": round(free) if free is not None else None,
    }


def _visible_monitoring_projects(profile: Profile) -> list[tuple[Project, dict[str, str]]]:
    configs = {item["code"]: item for item in _monitoring_config()}
    projects = []
    for project in profile.projects().filter(code__in=configs.keys()):
        projects.append((project, configs[str(project.code)]))
    return projects


def _check_payload(check: Check) -> dict:
    status = check.get_status()
    next_due = None
    if check.kind == "cron" and check.schedule:
        try:
            start = now().astimezone(ZoneInfo(check.tz))
            next_due = next(CronSim(check.schedule, start)).astimezone(timezone.utc)
        except Exception:
            next_due = None

    status_label = "waiting first run" if status == "new" else status
    last_ping = check.last_ping.astimezone(IST) if check.last_ping else None
    next_due_ist = next_due.astimezone(IST) if next_due else None
    return {
        "code": str(check.code),
        "name": check.name_then_code(),
        "tags": check.tags,
        "status": status,
        "status_label": status_label,
        "started": check.last_start is not None,
        "last_ping": check.last_ping.isoformat() if check.last_ping else None,
        "last_ping_ist": last_ping.isoformat() if last_ping else None,
        "next_due": next_due.isoformat() if next_due else None,
        "next_due_ist": next_due_ist.isoformat() if next_due_ist else None,
        "last_duration": (
            round(check.last_duration.total_seconds(), 2)
            if check.last_duration
            else None
        ),
        "n_pings": check.n_pings,
        "schedule": check.schedule if check.kind == "cron" else "",
        "details_url": reverse("hc-details", args=[check.code]),
        "log_url": reverse("hc-log", args=[check.code]),
    }


@login_required
def monitoring_dashboard(request: AuthenticatedHttpRequest) -> HttpResponse:
    return render(request, "front/monitoring.html", {"page": "monitoring"})


@login_required
def monitoring_overview(request: AuthenticatedHttpRequest) -> HttpResponse:
    projects = []
    totals = {"total": 0, "up": 0, "down": 0, "grace": 0, "new": 0, "paused": 0}

    for project, config in _visible_monitoring_projects(request.profile):
        checks = list(Check.objects.filter(project=project).order_by("name", "id"))
        summary = {"total": len(checks), "up": 0, "down": 0, "grace": 0, "new": 0, "paused": 0}
        check_rows = []
        for check in checks:
            row = _check_payload(check)
            summary[row["status"]] = summary.get(row["status"], 0) + 1
            check_rows.append(row)

        status_order = {"down": 0, "grace": 1, "up": 2, "new": 3, "paused": 4}
        check_rows.sort(key=lambda row: (status_order.get(row["status"], 5), row["name"]))

        for key, value in summary.items():
            totals[key] = totals.get(key, 0) + value

        projects.append(
            {
                "name": config["name"],
                "code": str(project.code),
                "health": _fetch_json(config["health_url"]),
                "summary": summary,
                "checks": check_rows,
                "checks_url": reverse("hc-checks", args=[project.code]),
            }
        )

    current = now()
    return JsonResponse(
        {
            "projects": projects,
            "totals": totals,
            "generated_at": current.isoformat(),
            "generated_at_ist": current.astimezone(IST).isoformat(),
        }
    )


@login_required
def monitoring_check_series(request: AuthenticatedHttpRequest, code: UUID) -> HttpResponse:
    check, rw = _get_check_for_user(request, code, preload_owner_profile=True)

    pings = list(check.visible_pings.order_by("-id")[:100])
    prepare_durations(pings)
    pings.reverse()

    ping_points = []
    duration_points = []
    for ping in pings:
        event_type = ping.kind or "success"
        ts = int(ping.created.timestamp())
        ping_points.append({"ts": ts, "ts_ist": ping.created.astimezone(IST).isoformat(), "type": event_type})
        if ping.duration:
            duration_points.append(
                {
                    "ts": ts,
                    "ts_ist": ping.created.astimezone(IST).isoformat(),
                    "value": round(ping.duration.total_seconds(), 2),
                }
            )

    flips = list(Flip.objects.filter(owner=check).order_by("-id")[:100])
    flips.reverse()
    flip_points = [
        {
            "ts": int(flip.created.timestamp()),
            "ts_ist": flip.created.astimezone(IST).isoformat(),
            "up": 1 if flip.new_status == "up" else 0,
            "status": flip.new_status,
        }
        for flip in flips
    ]

    return JsonResponse(
        {
            "check": _check_payload(check),
            "pings": ping_points,
            "durations": duration_points,
            "flips": flip_points,
        }
    )


@login_required
def monitoring_infrastructure(request: AuthenticatedHttpRequest) -> HttpResponse:
    window = 3600
    step = 60
    state = read_state()
    server = state.get("server", {})
    cpu_points = _series_from_state("cpu_percent", state) or _series_values(
        _prom_query_range('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))', window, step)
    )
    memory_points = _series_from_state("memory_percent", state) or _series_values(
        _prom_query_range("100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))", window, step)
    )
    disk_points = _series_from_state("disk_percent", state) or _series_values(
        _prom_query_range(
            '100 * (1 - (node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs"} / '
            'node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs"}))',
            window,
            step,
        )
    )
    nginx_points = _series_from_state("nginx_requests_per_second", state) or _series_values(
        _prom_query_range("rate(nginx_http_requests_total[5m])", window, step)
    )
    current = now()

    payload = {
        "window_seconds": window,
        "generated_at": current.isoformat(),
        "generated_at_ist": current.astimezone(IST).isoformat(),
        "source": "cron-observer direct 1s samples with Prometheus fallback",
        "metrics": {
            "cpu": {
                "label": "CPU live",
                "unit": "%",
                "series": cpu_points,
                **_stats(cpu_points),
                "details": {
                    "load1": server.get("load1") or _instant_value("node_load1"),
                    "cores": server.get("cores") or _instant_value('count(count by (cpu) (node_cpu_seconds_total{mode="idle"}))'),
                    "live_window": "1s",
                    "history_window": "1h",
                },
            },
            "memory": {
                "label": "Memory",
                "unit": "%",
                "series": memory_points,
                **_stats(memory_points),
                "details": server.get("memory")
                or _bytes_payload(
                    "node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes",
                    "node_memory_MemTotal_bytes",
                    "node_memory_MemAvailable_bytes",
                ),
            },
            "disk": {
                "label": "Disk",
                "unit": "%",
                "series": disk_points,
                **_stats(disk_points),
                "details": server.get("disk")
                or _bytes_payload(
                    'node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs"} - '
                    'node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs"}',
                    'node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs"}',
                    'node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs"}',
                ),
            },
            "nginx_requests": {
                "label": "NGINX requests",
                "unit": "req/s",
                "series": nginx_points,
                **_stats(nginx_points),
                "details": {
                    "active_connections": server.get("nginx_active") or _instant_value("nginx_connections_active"),
                    "total_requests_window": _instant_value(
                        f"increase(nginx_http_requests_total[{window}s])"
                    ),
                },
            },
        },
    }
    return JsonResponse(payload)


@login_required
def monitoring_live(request: AuthenticatedHttpRequest) -> HttpResponse:
    return JsonResponse(read_state())


@login_required
def monitoring_check_live(request: AuthenticatedHttpRequest, code: UUID) -> HttpResponse:
    check, rw = _get_check_for_user(request, code, preload_owner_profile=True)
    state = read_state()
    code_text = str(check.code)
    active = [
        item
        for item in state.get("active_crons", [])
        if item.get("ping_uuid") == code_text
    ]
    stale = [
        item
        for item in state.get("stale_crons", [])
        if item.get("ping_uuid") == code_text
    ]
    runs = iter_runs_for_uuid(str(check.code), limit=1)
    last_run = runs[0] if runs else None
    if last_run:
        events = read_run_events(str(check.code), last_run["run_id"], limit=40)
        last_run["recent_events"] = events.get("events", [])
    return JsonResponse(
        {
            "check": _check_payload(check),
            "active": active,
            "stale": stale,
            "last_run": last_run,
        }
    )


@login_required
def monitoring_check_runs(request: AuthenticatedHttpRequest, code: UUID) -> HttpResponse:
    check, rw = _get_check_for_user(request, code, preload_owner_profile=True)
    return JsonResponse({"check": _check_payload(check), "runs": iter_runs_for_uuid(str(check.code))})


@login_required
def monitoring_check_log(request: AuthenticatedHttpRequest, code: UUID) -> HttpResponse:
    check, rw = _get_check_for_user(request, code, preload_owner_profile=True)
    run_id = request.GET.get("run")
    runs = iter_runs_for_uuid(str(check.code), limit=1)
    if not run_id and runs:
        run_id = runs[0]["run_id"]

    if not run_id:
        return JsonResponse(
            {"check": _check_payload(check), "found": False, "content": "", "message": "No execution runs found."}
        )

    payload = read_run_log(str(check.code), run_id)
    payload["events"] = read_run_events(str(check.code), run_id, limit=120).get("events", [])
    for run in iter_runs_for_uuid(str(check.code), limit=50):
        if run.get("run_id") == run_id and run.get("error") and run["error"] not in payload.get("content", ""):
            payload["content"] = f"{payload.get('content', '')}\n[metadata traceback]\n{run['error']}"
            break
    payload["check"] = _check_payload(check)
    payload["run_id"] = run_id
    return JsonResponse(payload)


def monitoring_metrics(request: HttpRequest) -> HttpResponse:
    if not _local_only(request):
        return HttpResponseForbidden()

    return render_monitoring_metrics(_monitoring_config())
