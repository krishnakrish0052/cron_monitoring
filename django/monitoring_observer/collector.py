from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


MONITORING_ROOT = Path(os.environ.get("MONITORING_ROOT", "/home/ubuntu/monitoring"))
RUNTIME_ROOT = Path(os.environ.get("MONITORING_RUNTIME_ROOT", str(MONITORING_ROOT / "runtime/observer")))
LOG_ROOT = Path(os.environ.get("MONITORING_CRON_LOG_ROOT", str(MONITORING_ROOT / "logs/crons")))
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090")
STALE_SECONDS = int(os.environ.get("MONITORING_OBSERVER_STALE_SECONDS", "90"))
IST = ZoneInfo("Asia/Kolkata")
SERVER_SERIES_LIMIT = int(os.environ.get("MONITORING_SERVER_SERIES_LIMIT", "3600"))
HEARTBEAT_SCAN_WINDOW_SECONDS = int(os.environ.get("MONITORING_HEARTBEAT_SCAN_WINDOW_SECONDS", "900"))
HEARTBEAT_SCAN_LIMIT = int(os.environ.get("MONITORING_HEARTBEAT_SCAN_LIMIT", "500"))
RECENT_RUN_SCAN_LIMIT = int(os.environ.get("MONITORING_RECENT_RUN_SCAN_LIMIT", "250"))
INLINE_COLLECT_FALLBACK = os.environ.get("MONITORING_ALLOW_INLINE_COLLECT", "0") == "1"
_PREVIOUS_CPU = None
_RECENT_CACHE_AT = 0.0
_RECENT_CACHE = []


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def iso_ist(dt: datetime) -> str:
    return dt.astimezone(IST).isoformat()


def load_json(path: Path) -> dict | None:
    with contextlib.suppress(Exception):
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def pid_exists(pid: int | None) -> bool:
    return bool(pid) and Path("/proc").joinpath(str(pid)).exists()


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    with contextlib.suppress(Exception):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def prom_query(query: str) -> float | None:
    params = urllib.parse.urlencode({"query": query})
    url = f"{PROMETHEUS_URL}/api/v1/query?{params}"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode())
    except Exception:
        return None
    if payload.get("status") != "success":
        return None
    result = payload.get("data", {}).get("result", [])
    if not result:
        return None
    with contextlib.suppress(Exception):
        return float(result[0]["value"][1])
    return None


def read_events(path_value: str | None, limit: int = 20) -> list[dict]:
    if not path_value:
        return []
    path = Path(path_value)
    if not path.exists():
        return []
    with contextlib.suppress(Exception):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        events = []
        for line in lines[-limit:]:
            with contextlib.suppress(Exception):
                events.append(json.loads(line))
        return events
    return []


def read_cpu_snapshot() -> dict | None:
    global _PREVIOUS_CPU
    with contextlib.suppress(Exception):
        values = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
        numbers = [int(item) for item in values]
        idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
        total = sum(numbers)
        sample = {"idle": idle, "total": total}
        percent = None
        if _PREVIOUS_CPU:
            total_delta = total - _PREVIOUS_CPU["total"]
            idle_delta = idle - _PREVIOUS_CPU["idle"]
            if total_delta > 0:
                percent = round(100 * (1 - idle_delta / total_delta), 2)
        _PREVIOUS_CPU = sample
        return {"percent": percent}
    return None


def read_memory_snapshot() -> dict:
    data = {}
    with contextlib.suppress(Exception):
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            data[key] = int(value.strip().split()[0]) * 1024
    total = data.get("MemTotal")
    available = data.get("MemAvailable")
    used = total - available if total is not None and available is not None else None
    percent = round((used / total) * 100, 2) if used is not None and total else None
    return {
        "percent": percent,
        "used_bytes": used,
        "total_bytes": total,
        "free_bytes": available,
    }


def read_disk_snapshot() -> dict:
    usage = shutil.disk_usage("/")
    used = usage.total - usage.free
    return {
        "percent": round((used / usage.total) * 100, 2),
        "used_bytes": used,
        "total_bytes": usage.total,
        "free_bytes": usage.free,
    }


def server_snapshot(previous_state: dict | None = None) -> dict:
    cpu = read_cpu_snapshot() or {}
    memory = read_memory_snapshot()
    disk = read_disk_snapshot()
    if cpu.get("percent") is None:
        cpu["percent"] = prom_query('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m])))')

    return {
        "cpu_percent": cpu.get("percent"),
        "memory_percent": memory.get("percent"),
        "disk_percent": disk.get("percent"),
        "nginx_requests_per_second": prom_query("rate(nginx_http_requests_total[1m])"),
        "nginx_active": prom_query("nginx_connections_active"),
        "load1": prom_query("node_load1"),
        "cores": prom_query('count(count by (cpu) (node_cpu_seconds_total{mode="idle"}))'),
        "memory": memory,
        "disk": disk,
    }


def append_server_series(previous_state: dict | None, now: datetime, server: dict) -> list[dict]:
    series = []
    if previous_state:
        series = list(previous_state.get("server_series") or [])
    series.append(
        {
            "ts": int(now.timestamp()),
            "at_ist": iso_ist(now),
            "cpu_percent": server.get("cpu_percent"),
            "memory_percent": server.get("memory_percent"),
            "disk_percent": server.get("disk_percent"),
            "nginx_requests_per_second": server.get("nginx_requests_per_second"),
        }
    )
    return series[-SERVER_SERIES_LIMIT:]


def recent_runs(limit: int = 25) -> list[dict]:
    global _RECENT_CACHE_AT, _RECENT_CACHE
    current = time.monotonic()
    if _RECENT_CACHE and current - _RECENT_CACHE_AT < 10:
        return _RECENT_CACHE[:limit]

    paths = sorted(
        LOG_ROOT.glob("*/*/*.json"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )[:RECENT_RUN_SCAN_LIMIT]
    items = []
    for path in paths:
        payload = load_json(path)
        if payload:
            payload.setdefault("events_path", str(path.with_suffix(".events.jsonl")))
            items.append(payload)
    items.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    selected = items[:limit]
    for payload in selected:
        events = read_events(payload.get("events_path"), 12)
        payload["recent_events"] = events
        payload["external_error"] = payload.get("external_error") or next(
            (
                event.get("data", {}).get("classification")
                for event in reversed(events)
                if event.get("data", {}).get("classification", {}).get("severity") in ("warning", "error")
            ),
            None,
        )
    _RECENT_CACHE = selected
    _RECENT_CACHE_AT = current
    return _RECENT_CACHE[:limit]


def collect_heartbeats() -> tuple[list[dict], list[dict]]:
    current = utcnow()
    min_mtime = time.time() - max(HEARTBEAT_SCAN_WINDOW_SECONDS, STALE_SECONDS * 2)
    active = []
    stale = []
    paths = []
    for path in (RUNTIME_ROOT / "heartbeats").glob("*/*/*.json"):
        with contextlib.suppress(OSError):
            stat = path.stat()
            if stat.st_mtime >= min_mtime:
                paths.append((stat.st_mtime, path))
    paths.sort(reverse=True)
    for _, path in paths[:HEARTBEAT_SCAN_LIMIT]:
        payload = load_json(path)
        if not payload:
            continue
        updated = parse_ts(payload.get("updated_at_utc"))
        age = (current - updated).total_seconds() if updated else 999999
        payload["heartbeat_age_seconds"] = round(age, 3)
        payload["active"] = (
            payload.get("status") == "running"
            and age <= STALE_SECONDS
            and pid_exists(payload.get("pid"))
        )
        if payload["active"]:
            payload["recent_events"] = read_events(payload.get("events_path"), 20)
            active.append(payload)
        elif payload.get("status") == "running":
            payload["recent_events"] = read_events(payload.get("events_path"), 8)
            payload["stale"] = True
            stale.append(payload)

    active.sort(key=lambda item: item.get("elapsed_seconds") or 0, reverse=True)
    stale.sort(key=lambda item: item.get("updated_at_utc") or "", reverse=True)
    return active, stale[:20]


def collect_state() -> dict:
    previous_state = load_json(state_path())
    active, stale = collect_heartbeats()
    total_cpu = 0.0
    total_rss = 0
    total_queries = 0
    total_slow = 0
    for item in active:
        process = item.get("process") or {}
        db = item.get("db") or {}
        total_cpu += float(process.get("cpu_percent") or 0)
        total_rss += int(process.get("rss_bytes") or 0)
        total_queries += int(db.get("query_count") or 0)
        total_slow += int(db.get("slow_count") or 0)

    now = utcnow()
    server = server_snapshot(previous_state)
    server_series = append_server_series(previous_state, now, server)
    recent = recent_runs()
    external_errors = []
    for item in active + stale + recent:
        error = (item.get("http") or {}).get("external_error") or item.get("external_error")
        if error:
            external_errors.append(
                {
                    "project": item.get("project"),
                    "function": item.get("function"),
                    "run_id": item.get("run_id"),
                    "severity": error.get("severity"),
                    "type": error.get("type"),
                    "message": error.get("message"),
                }
            )
    return {
        "generated_at_utc": iso(now),
        "generated_at_ist": iso_ist(now),
        "active_crons": active,
        "stale_crons": stale,
        "recent_runs": recent,
        "external_errors": external_errors[:20],
        "server_series": server_series,
        "totals": {
            "running": len(active),
            "stale": len(stale),
            "cpu_percent": round(total_cpu, 2),
            "rss_bytes": total_rss,
            "db_queries": total_queries,
            "slow_db_queries": total_slow,
            "processes": len(active),
        },
        "server": server,
    }


def state_path() -> Path:
    return RUNTIME_ROOT / "state.json"


def write_state(payload: dict) -> None:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = state_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(state_path())


def read_state(max_age_seconds: int = 5) -> dict:
    path = state_path()
    payload = load_json(path)
    if payload:
        generated = parse_ts(payload.get("generated_at_utc"))
        if generated and (utcnow() - generated).total_seconds() <= max_age_seconds:
            return payload
        payload["state_stale"] = True
        payload["state_age_seconds"] = (
            round((utcnow() - generated).total_seconds(), 3) if generated else None
        )
        return payload
    if INLINE_COLLECT_FALLBACK:
        return collect_state()
    current = utcnow()
    return {
        "generated_at_utc": iso(current),
        "generated_at_ist": iso_ist(current),
        "state_unavailable": True,
        "active_crons": [],
        "stale_crons": [],
        "recent_runs": [],
        "external_errors": [],
        "server_series": [],
        "totals": {
            "running": 0,
            "stale": 0,
            "cpu_percent": 0,
            "rss_bytes": 0,
            "db_queries": 0,
            "slow_db_queries": 0,
            "processes": 0,
        },
        "server": server_snapshot(None),
    }
