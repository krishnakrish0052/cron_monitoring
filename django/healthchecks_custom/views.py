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
from django.views.decorators.http import require_GET, require_POST
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
from healthchecks_custom.hodl_registry import load_registry_jobs, uuid_for_job as hodl_check_uuid_for_job
from healthchecks_custom.metrics import render_monitoring_metrics
from monitoring_common.cron_logs import iter_runs_for_uuid, read_run_events, read_run_log
from monitoring_observer.collector import read_state
from db_maintenance.core import all_health, queue_action


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
HODL_CRONOPS_URL = os.environ.get("HODL_CRONOPS_URL", "http://127.0.0.1:8001/api/cronops/live/")
HODL_CRONOPS_BASE = os.environ.get("HODL_CRONOPS_BASE", "http://127.0.0.1:8001/api/cronops")
INFRA_SERIES_LIMIT = int(os.environ.get("MONITORING_INFRA_SERIES_LIMIT", "300"))
LIVE_RECENT_RUN_LIMIT = int(os.environ.get("MONITORING_LIVE_RECENT_RUN_LIMIT", "10"))
LIVE_EVENT_LIMIT = int(os.environ.get("MONITORING_LIVE_EVENT_LIMIT", "5"))
DB_MAINTENANCE_CACHE_SECONDS = float(os.environ.get("DB_MAINTENANCE_CACHE_SECONDS", "60"))
IST = ZoneInfo("Asia/Kolkata")

def _monitoring_config() -> list[dict[str, str]]:
    return list(getattr(settings, "MONITORING_PROJECTS", MONITORING_PROJECTS))


def _local_only(request: HttpRequest) -> bool:
    # This endpoint is consumed by the local Prometheus process.  Do not
    # trust X-Forwarded-For here: a client can supply it unless the proxy has
    # explicitly stripped and rebuilt the header.
    return request.META.get("REMOTE_ADDR", "") in ("127.0.0.1", "::1")


def _fetch_json(url: str, timeout: float = 3) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except Exception as exc:
        return {"status": "unreachable", "error": str(exc), "checks": {}}


def _hodl_run_to_live(run: dict) -> dict:
    job_key = run.get("job_key") or run.get("function") or ""
    return {
        **run,
        "source": "hodl-cronops",
        "project": "HODL-2025",
        "function": run.get("function") or job_key,
        "ping_uuid": hodl_check_uuid_for_job(job_key),
        "stage": run.get("stage") or run.get("message"),
        "last_progress_ist": run.get("last_progress_at"),
    }


def _hodl_trigger_to_live(trigger: dict) -> dict:
    job_key = trigger.get("job_key") or trigger.get("function") or ""
    return {
        **trigger,
        "source": "hodl-cronops-queue",
        "project": "HODL-2025",
        "function": trigger.get("function") or job_key,
        "ping_uuid": hodl_check_uuid_for_job(job_key),
        "stage": trigger.get("terminal_reason") or trigger.get("status"),
        "status": trigger.get("effective_status") or trigger.get("status"),
        "last_progress_ist": trigger.get("updated_at"),
    }


_HODL_MERGE_CACHE: dict = {"at": 0.0, "value": None}
_DB_MAINTENANCE_CACHE: dict = {"at": 0.0, "value": None}
_HODL_MERGE_TTL = float(os.environ.get("HODL_CRONOPS_CACHE_SECONDS", "15"))
_HODL_CRONOPS_LIVE_TIMEOUT = float(os.environ.get("HODL_CRONOPS_LIVE_TIMEOUT", "1.5"))
_HODL_CRONOPS_DETAIL_TIMEOUT = float(os.environ.get("HODL_CRONOPS_DETAIL_TIMEOUT", "2.0"))


def _fetch_hodl_bundle() -> dict:
    """Fetch the HODL cronops state (live + 3 detail endpoints) with a small
    in-process cache so multiple concurrent dashboard polls don't all hammer
    the single-threaded HODL dev runserver. TTL counts from the END of the
    last fetch, so consecutive callers within TTL seconds get the cached copy.
    """
    import time as _time
    cached = _HODL_MERGE_CACHE
    if cached["value"] is not None and (_time.monotonic() - cached["at"]) < _HODL_MERGE_TTL:
        return cached["value"]

    bundle: dict = {"hodl": {}, "slow_q": {}, "http_s": {}, "pg": {}}
    # These endpoints can become slow while HODL crons or DB work are busy.
    # Keep dashboard requests fast; stale cached data is better than tying up
    # all gunicorn workers and making Healthchecks itself unreachable.
    bundle["hodl"] = _fetch_json(HODL_CRONOPS_URL, timeout=_HODL_CRONOPS_LIVE_TIMEOUT)
    if "running" in bundle["hodl"] or "recent" in bundle["hodl"]:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_slow = pool.submit(_fetch_json, f"{HODL_CRONOPS_BASE}/slow-queries/", _HODL_CRONOPS_DETAIL_TIMEOUT)
            f_http = pool.submit(_fetch_json, f"{HODL_CRONOPS_BASE}/http-stats/", _HODL_CRONOPS_DETAIL_TIMEOUT)
            f_pg = pool.submit(_fetch_json, f"{HODL_CRONOPS_BASE}/postgres/", _HODL_CRONOPS_DETAIL_TIMEOUT)
            bundle["slow_q"] = f_slow.result()
            bundle["http_s"] = f_http.result()
            bundle["pg"] = f_pg.result()
    cached["value"] = bundle
    cached["at"] = _time.monotonic()  # post-work, so TTL counts from completion
    return bundle


def _merge_hodl_cronops_state(state: dict) -> dict:
    bundle = _fetch_hodl_bundle()
    hodl = bundle["hodl"]
    if "running" not in hodl and "recent" not in hodl:
        state["hodl_cronops"] = {"status": "unreachable", "error": hodl.get("error")}
        return state

    active = [_hodl_run_to_live(run) for run in hodl.get("running", [])]
    recent = [_hodl_run_to_live(run) for run in hodl.get("recent", [])]
    active_triggers = [_hodl_trigger_to_live(trigger) for trigger in hodl.get("active_triggers", [])]
    recent_triggers = [_hodl_trigger_to_live(trigger) for trigger in hodl.get("recent_triggers", [])]
    orphans = hodl.get("orphans", [])
    slow_q = bundle["slow_q"]
    http_s = bundle["http_s"]
    pg = bundle["pg"]

    state["slow_queries"] = slow_q.get("slow_queries", []) if isinstance(slow_q, dict) else []
    state["http_stats"] = http_s.get("hosts", []) if isinstance(http_s, dict) else []
    state["postgres"] = pg if isinstance(pg, dict) and "connections_by_state" in pg else {}
    state.setdefault("active_crons", [])
    state.setdefault("recent_runs", [])
    state["active_crons"] = active_triggers + active + state["active_crons"]
    state["recent_runs"] = recent_triggers[:20] + recent[:20] + state["recent_runs"]
    state.setdefault("external_errors", [])
    for item in active_triggers + recent_triggers + active + recent:
        error = item.get("external_error") or item.get("diagnostic")
        if error:
            state["external_errors"].append(
                {
                    "project": item.get("project"),
                    "function": item.get("function"),
                    "run_id": item.get("run_id"),
                    "severity": error.get("severity"),
                    "type": error.get("type"),
                    "message": error.get("message"),
                }
            )
    state["orphans"] = orphans
    state["hodl_cronops"] = {
        "status": "ok",
        "jobs": hodl.get("jobs", 0),
        "running": len(active),
        "active_triggers": len(active_triggers),
        "recent": len(recent),
        "recent_triggers": len(recent_triggers),
        "orphans": len(orphans),
        "duplicate_skips_24h": hodl.get("duplicate_skips_24h", 0),
        "resource_deferred_24h": hodl.get("resource_deferred_24h", 0),
        "failed_24h": hodl.get("failed_24h", 0),
        "degraded_24h": hodl.get("degraded_24h", 0),
        "timeouts_24h": hodl.get("timeouts_24h", 0),
        "stale_count_24h": hodl.get("stale_count_24h", 0),
        "queue_summary": hodl.get("queue_summary", {}),
        "queue_by_queue": hodl.get("queue_by_queue", []),
        "spool_summary": hodl.get("spool_summary", {}),
        "workers": hodl.get("workers", []),
        "svr4plus_ingestion": hodl.get("svr4plus_ingestion", {}),
        "generated_at": hodl.get("generated_at"),
    }
    return state


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
    return points[-INFRA_SERIES_LIMIT:]


def _trim_events(events: list[dict] | None, limit: int = LIVE_EVENT_LIMIT) -> list[dict]:
    trimmed = []
    for event in (events or [])[-limit:]:
        data = event.get("data") or {}
        trimmed.append(
            {
                "at_utc": event.get("at_utc"),
                "at_ist": event.get("at_ist"),
                "type": event.get("type"),
                "severity": event.get("severity"),
                "message": event.get("message"),
                "data": {
                    "classification": data.get("classification"),
                    "operation": data.get("operation"),
                    "table": data.get("table"),
                    "duration_seconds": data.get("duration_seconds"),
                    "status_code": data.get("status_code"),
                    "function": data.get("function"),
                    "line": data.get("line"),
                },
            }
        )
    return trimmed


def _trim_cron_item(item: dict) -> dict:
    return {
        key: value
        for key, value in item.items()
        if key not in ("recent_events", "events", "stack", "stack_snapshot")
    } | {"recent_events": _trim_events(item.get("recent_events"))}


def _slim_live_payload(state: dict) -> dict:
    payload = dict(state)
    payload.pop("server_series", None)
    payload["active_crons"] = [_trim_cron_item(item) for item in payload.get("active_crons", [])]
    payload["stale_crons"] = [_trim_cron_item(item) for item in payload.get("stale_crons", [])[:10]]
    payload["recent_runs"] = [
        _trim_cron_item(item)
        for item in payload.get("recent_runs", [])[:LIVE_RECENT_RUN_LIMIT]
    ]
    payload["external_errors"] = payload.get("external_errors", [])[:10]
    return payload


def _cached_db_maintenance_payload() -> dict:
    current = time.monotonic()
    cached = _DB_MAINTENANCE_CACHE
    if cached["value"] is not None and current - cached["at"] < DB_MAINTENANCE_CACHE_SECONDS:
        return cached["value"]
    payload = all_health()
    cached["value"] = payload
    cached["at"] = time.monotonic()
    return payload


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


def _cronops_effective_by_check_code() -> dict[str, dict]:
    bundle = _fetch_hodl_bundle()
    hodl = bundle.get("hodl") or {}
    if "running" not in hodl and "recent" not in hodl:
        return {}

    job_by_code = {
        str(record["uuid"]): job_key
        for job_key, record in load_registry_jobs().items()
        if record.get("uuid")
    }
    code_by_job = {job_key: code for code, job_key in job_by_code.items()}
    active_statuses = {
        "scheduled",
        "queued",
        "waiting_for_capacity",
        "deferred_by_pressure",
        "running",
        "retrying",
    }
    terminal_success = {"success"}
    terminal_failure = {"failed", "failure", "timeout", "stale", "missed_sla", "cancelled", "degraded"}

    def public_status(status: str) -> str:
        if status in terminal_success:
            return "up"
        if status in terminal_failure:
            return "down"
        if status in active_statuses:
            return "grace"
        return "grace"

    def label(status: str) -> str:
        return f"cronops {status.replace('_', ' ')}"

    effective: dict[str, dict] = {}

    def add(job_key: str, status: str, item: dict, source: str, rank: int) -> None:
        code = code_by_job.get(job_key)
        if not code or not status:
            return
        current = effective.get(code)
        if current and current["rank"] <= rank:
            return
        effective[code] = {
            "rank": rank,
            "job_key": job_key,
            "source": source,
            "raw_status": status,
            "status": public_status(status),
            "status_label": label(status),
            "queue_name": item.get("queue_name", ""),
            "trigger_id": item.get("trigger_id", ""),
            "run_id": item.get("run_id", ""),
            "scheduled_for": item.get("scheduled_for"),
            "started_at": item.get("started_at"),
            "updated_at": item.get("updated_at"),
            "terminal_reason": item.get("terminal_reason", ""),
        }

    for item in hodl.get("active_triggers", []) or []:
        add(item.get("job_key") or item.get("function") or "", item.get("effective_status") or item.get("status") or "", item, "cronops_trigger", 10)
    for item in hodl.get("running", []) or []:
        add(
            item.get("job_key") or item.get("function") or "",
            item.get("effective_status") or item.get("status") or "running",
            item,
            "cronops_run",
            20,
        )
    for item in hodl.get("recent_triggers", []) or []:
        add(item.get("job_key") or item.get("function") or "", item.get("effective_status") or item.get("status") or "", item, "cronops_recent_trigger", 40)
    for item in hodl.get("recent", []) or []:
        add(
            item.get("job_key") or item.get("function") or "",
            item.get("effective_status") or item.get("status") or "",
            item,
            "cronops_recent_run",
            50,
        )

    for value in effective.values():
        value.pop("rank", None)
    return effective


def _apply_cronops_effective_status(row: dict, effective: dict[str, dict]) -> dict:
    cronops = effective.get(row["code"])
    if not cronops:
        return row
    updated = dict(row)
    updated["healthchecks_status"] = row["status"]
    updated["healthchecks_status_label"] = row["status_label"]
    updated["status"] = cronops["status"]
    updated["status_label"] = cronops["status_label"]
    updated["cronops"] = cronops
    return updated


def _status_summary(rows: list[dict]) -> dict[str, int]:
    summary = {"total": len(rows), "up": 0, "down": 0, "grace": 0, "new": 0, "paused": 0}
    for row in rows:
        status = row.get("status", "new")
        summary[status] = summary.get(status, 0) + 1
    return summary


@login_required
def monitoring_dashboard(request: AuthenticatedHttpRequest) -> HttpResponse:
    return render(request, "front/monitoring.html", {"page": "monitoring"})


@login_required
def monitoring_overview(request: AuthenticatedHttpRequest) -> HttpResponse:
    projects = []
    totals = {"total": 0, "up": 0, "down": 0, "grace": 0, "new": 0, "paused": 0}
    cronops_effective = _cronops_effective_by_check_code()
    hodl_cronops_summary = None

    for project, config in _visible_monitoring_projects(request.profile):
        checks = list(Check.objects.filter(project=project).order_by("name", "id"))
        check_rows = []
        external_rows = []
        cronops_rows = []
        is_hodl = config["name"] == "HODL-2025"
        for check in checks:
            raw_row = _check_payload(check)
            external_rows.append(raw_row)
            row = raw_row
            if is_hodl:
                row = _apply_cronops_effective_status(row, cronops_effective)
                if "hodl-cronops" in check.tags_list():
                    cronops_rows.append(row)
            check_rows.append(row)

        summary = _status_summary(external_rows)
        cronops_summary = _status_summary(cronops_rows) if is_hodl else None
        if is_hodl:
            hodl_cronops_summary = cronops_summary

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
                "external_summary": summary,
                "cronops_summary": cronops_summary,
                "checks": check_rows,
                "checks_url": reverse("hc-checks", args=[project.code]),
            }
        )

    current = now()
    return JsonResponse(
        {
            "projects": projects,
            "totals": totals,
            "hodl_cronops_summary": hodl_cronops_summary,
            "cronops_source": "effective_status" if cronops_effective else "healthchecks",
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
    return JsonResponse(_slim_live_payload(_merge_hodl_cronops_state(read_state())))


@login_required
@require_GET
def monitoring_db_maintenance(request: AuthenticatedHttpRequest) -> HttpResponse:
    payload = dict(_cached_db_maintenance_payload())
    payload["can_manage"] = _can_run_db_maintenance(request)
    return JsonResponse(payload)


def _can_run_db_maintenance(request: AuthenticatedHttpRequest) -> bool:
    user = request.user
    return bool(user.is_authenticated and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)))


@login_required
@require_POST
def monitoring_db_maintenance_action(request: AuthenticatedHttpRequest) -> HttpResponse:
    if not _can_run_db_maintenance(request):
        return JsonResponse({"ok": False, "error": "staff_or_superuser_required"}, status=403)
    try:
        payload = json.loads(request.body.decode() or "{}")
        job = queue_action(
            project=str(payload.get("project", "")),
            schema_name=str(payload.get("schema", "")),
            table_name=str(payload.get("table", "")),
            action=str(payload.get("action", "")),
            confirmation=str(payload.get("confirmation", "")),
            actor=getattr(request.user, "email", "") or getattr(request.user, "username", "") or str(request.user),
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)
    return JsonResponse({"ok": True, "job": job}, status=201)


@login_required
def monitoring_cron_history(request: AuthenticatedHttpRequest, job_key: str) -> HttpResponse:
    """Per-job 30-day daily aggregate, proxied from HODL cronops."""
    payload = _fetch_json(f"{HODL_CRONOPS_BASE}/history/{job_key}/", timeout=2.0)
    if "days" not in payload:
        return JsonResponse({"job_key": job_key, "days": [], "error": payload.get("error")}, status=502)
    return JsonResponse(payload)


@login_required
def monitoring_check_live(request: AuthenticatedHttpRequest, code: UUID) -> HttpResponse:
    check, rw = _get_check_for_user(request, code, preload_owner_profile=True)
    state = _merge_hodl_cronops_state(read_state())
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
