from __future__ import annotations

import contextlib
import json
import os
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
    return bool(pid) and Path("/proc") .joinpath(str(pid)).exists()


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


def recent_runs(limit: int = 25) -> list[dict]:
    items = []
    for path in LOG_ROOT.glob("*/*/*.json"):
        payload = load_json(path)
        if payload:
            items.append(payload)
    items.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    return items[:limit]


def collect_heartbeats() -> tuple[list[dict], list[dict]]:
    current = utcnow()
    active = []
    stale = []
    for path in (RUNTIME_ROOT / "heartbeats").glob("*/*/*.json"):
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
            active.append(payload)
        elif payload.get("status") == "running":
            payload["stale"] = True
            stale.append(payload)

    active.sort(key=lambda item: item.get("elapsed_seconds") or 0, reverse=True)
    stale.sort(key=lambda item: item.get("updated_at_utc") or "", reverse=True)
    return active, stale[:20]


def collect_state() -> dict:
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
    return {
        "generated_at_utc": iso(now),
        "generated_at_ist": iso_ist(now),
        "active_crons": active,
        "stale_crons": stale,
        "recent_runs": recent_runs(),
        "totals": {
            "running": len(active),
            "stale": len(stale),
            "cpu_percent": round(total_cpu, 2),
            "rss_bytes": total_rss,
            "db_queries": total_queries,
            "slow_db_queries": total_slow,
        },
        "server": {
            "cpu_percent": prom_query('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[2m])))'),
            "memory_percent": prom_query(
                "100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))"
            ),
            "disk_percent": prom_query(
                '100 * (1 - (node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs"} / '
                'node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs"}))'
            ),
            "nginx_requests_per_second": prom_query("rate(nginx_http_requests_total[2m])"),
        },
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
    return collect_state()
