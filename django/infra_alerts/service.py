from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
SLACK_WEBHOOK_URL = os.environ.get("MONITORING_SLACK_WEBHOOK_URL", "")
RUNTIME_ROOT = Path(os.environ.get("MONITORING_INFRA_ALERT_RUNTIME", "/home/ubuntu/monitoring/runtime/infra-alerts"))
OBSERVER_STATE_PATH = Path(os.environ.get("MONITORING_OBSERVER_STATE_PATH", "/home/ubuntu/monitoring/runtime/observer/state.json"))
HODL_CRONOPS_LIVE_URL = os.environ.get("HODL_CRONOPS_LIVE_URL", "http://127.0.0.1:8001/api/cronops/live/")
INTERVAL_SECONDS = float(os.environ.get("MONITORING_INFRA_ALERT_INTERVAL_SECONDS", "30"))
REMINDER_SECONDS = float(os.environ.get("MONITORING_INFRA_ALERT_REMINDER_SECONDS", "1800"))
DRY_RUN = os.environ.get("MONITORING_INFRA_ALERT_DRY_RUN", "0") == "1"
HEALTHCHECKS_ROOT = Path(os.environ.get("MONITORING_HEALTHCHECKS_ROOT", "/home/ubuntu/monitoring/healthchecks"))
HEALTHCHECKS_SERVER_HEALTH_NAMES = [
    item.strip()
    for item in os.environ.get(
        "MONITORING_HEALTHCHECKS_SERVER_HEALTH_NAMES",
        "Server Health Check,HODL-2025 Server Health",
    ).split(",")
    if item.strip()
]
HEALTHCHECKS_SERVER_PING_SECONDS = float(os.environ.get("MONITORING_HEALTHCHECKS_SERVER_PING_SECONDS", "300"))

running = True
_last_healthchecks_ping = 0.0


@dataclass(frozen=True)
class RuleResult:
    key: str
    name: str
    severity: str
    active: bool
    value: float | None = None
    threshold: str = ""
    hold_seconds: float = 0
    description: str = ""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat().replace("+00:00", "Z")


def stop(signum: int, frame: Any) -> None:
    global running
    running = False


def load_json(path: Path) -> dict[str, Any]:
    with contextlib.suppress(Exception):
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def ping_healthchecks_server_health() -> None:
    global _last_healthchecks_ping
    if not HEALTHCHECKS_SERVER_HEALTH_NAMES:
        return
    now = time.time()
    if now - _last_healthchecks_ping < HEALTHCHECKS_SERVER_PING_SECONDS:
        return
    _last_healthchecks_ping = now
    close_connections = None
    try:
        if str(HEALTHCHECKS_ROOT) not in sys.path:
            sys.path.insert(0, str(HEALTHCHECKS_ROOT))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hc.settings")
        import django
        from django.apps import apps
        from django.db import close_old_connections
        close_connections = close_old_connections

        if not apps.ready:
            django.setup()
        from hc.api.models import Check

        close_connections()
        checks = list(Check.objects.filter(name__in=HEALTHCHECKS_SERVER_HEALTH_NAMES))
        for check in checks:
            close_connections()
            check.ping(
                remote_addr="127.0.0.1",
                scheme="http",
                method="POST",
                ua="infra-alert-worker",
                body=b"infra-alert-worker heartbeat",
                action="",
                rid=None,
            )
    except Exception as exc:
        print(f"Healthchecks server-health ping failed: {exc}", flush=True)
    finally:
        if close_connections:
            with contextlib.suppress(Exception):
                close_connections()


def prom_query(query: str) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"query": query})
    url = f"{PROMETHEUS_URL}/api/v1/query?{params}"
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload!r}")
    return list(payload.get("data", {}).get("result") or [])


def prom_scalar(query: str) -> float | None:
    result = prom_query(query)
    if not result:
        return None
    with contextlib.suppress(Exception):
        return float(result[0]["value"][1])
    return None


def read_observer_stale_count() -> int | None:
    payload = load_json(OBSERVER_STATE_PATH)
    if not payload:
        return None
    totals = payload.get("totals") or {}
    with contextlib.suppress(Exception):
        return int(totals.get("stale") or len(payload.get("stale_crons") or []))
    return None


def read_hodl_stale_count() -> int | None:
    if not HODL_CRONOPS_LIVE_URL:
        return None
    try:
        with urllib.request.urlopen(HODL_CRONOPS_LIVE_URL, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    with contextlib.suppress(Exception):
        return int(payload.get("stale_count_24h") or len(payload.get("stale") or []))
    return None


def monitoring_down_targets() -> list[str]:
    targets = []
    for item in prom_query("up == 0"):
        labels = item.get("metric") or {}
        job = labels.get("job", "unknown")
        instance = labels.get("instance", "unknown")
        targets.append(f"{job}/{instance}")
    return sorted(targets)


def collect_rules() -> list[RuleResult]:
    rules: list[RuleResult] = []
    cpu = prom_scalar('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m])))')
    memory_available = prom_scalar("100 * node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes")
    disk_used = prom_scalar(
        '100 * (1 - node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"} '
        '/ node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"})'
    )
    stale_count = read_hodl_stale_count()
    if stale_count is None:
        stale_count = read_observer_stale_count()

    rules.append(RuleResult("cpu_warning", "CPU high", "warning", cpu is not None and cpu > 90, cpu, "> 90%", 300))
    rules.append(RuleResult("cpu_critical", "CPU critical", "critical", cpu is not None and cpu > 95, cpu, "> 95%", 600))
    rules.append(
        RuleResult(
            "memory_warning",
            "Memory available low",
            "warning",
            memory_available is not None and memory_available < 20,
            memory_available,
            "< 20% available",
            300,
        )
    )
    rules.append(
        RuleResult(
            "memory_critical",
            "Memory available critical",
            "critical",
            memory_available is not None and memory_available < 10,
            memory_available,
            "< 10% available",
            300,
        )
    )
    rules.append(
        RuleResult("disk_warning", "Disk usage high", "warning", disk_used is not None and disk_used > 85, disk_used, "> 85% used", 0)
    )
    rules.append(
        RuleResult("disk_critical", "Disk usage critical", "critical", disk_used is not None and disk_used > 92, disk_used, "> 92% used", 0)
    )
    rules.append(
        RuleResult(
            "cron_stale_critical",
            "Stale cron detected",
            "critical",
            stale_count is not None and stale_count > 0,
            float(stale_count) if stale_count is not None else None,
            "> 0 stale crons",
            180,
        )
    )

    down_targets = monitoring_down_targets()
    rules.append(
        RuleResult(
            "monitoring_services_down",
            "Monitoring service down",
            "critical",
            bool(down_targets),
            float(len(down_targets)),
            "up == 0",
            0,
            ", ".join(down_targets[:8]),
        )
    )
    return rules


def send_slack(text: str) -> bool:
    if DRY_RUN or not SLACK_WEBHOOK_URL:
        print(text, flush=True)
        return False

    data = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            response.read()
        return True
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Slack alert send failed: {exc}", flush=True)
        return False


def value_text(value: float | None) -> str:
    if value is None:
        return "unknown"
    if abs(value - round(value)) < 0.01:
        return str(int(round(value)))
    return f"{value:.2f}"


def format_message(prefix: str, rule: RuleResult, active_for: float | None = None) -> str:
    parts = [
        f"{prefix} [{rule.severity.upper()}] {rule.name}",
        f"value={value_text(rule.value)}",
        f"threshold={rule.threshold}",
    ]
    if active_for is not None:
        parts.append(f"active_for={int(active_for)}s")
    if rule.description:
        parts.append(f"details={rule.description}")
    return " | ".join(parts)


def evaluate(state: dict[str, Any], rules: list[RuleResult]) -> dict[str, Any]:
    now = time.time()
    active_state = dict(state.get("active") or {})
    seen_keys = {rule.key for rule in rules}

    for key in list(active_state):
        if key not in seen_keys:
            active_state.pop(key, None)

    for rule in rules:
        entry = active_state.get(rule.key)
        if rule.active:
            if not entry:
                entry = {"first_seen": now, "last_sent": 0, "firing": False}
                active_state[rule.key] = entry
            active_for = now - float(entry.get("first_seen") or now)
            if active_for >= rule.hold_seconds:
                last_sent = float(entry.get("last_sent") or 0)
                firing = bool(entry.get("firing"))
                if not firing:
                    send_slack(format_message("ALERT", rule, active_for))
                    entry["last_sent"] = now
                    entry["firing"] = True
                    entry["last_fired_at"] = iso_now()
                elif now - last_sent >= REMINDER_SECONDS:
                    send_slack(format_message("REMINDER", rule, active_for))
                    entry["last_sent"] = now
            entry["last_seen"] = now
            entry["value"] = rule.value
            entry["severity"] = rule.severity
            continue

        if entry:
            if entry.get("firing"):
                send_slack(format_message("RESOLVED", rule))
            active_state.pop(rule.key, None)

    return {
        "updated_at_utc": iso_now(),
        "active": active_state,
    }


def loop_once() -> None:
    state_path = RUNTIME_ROOT / "state.json"
    state = load_json(state_path)
    try:
        rules = collect_rules()
    except Exception as exc:
        rules = [
            RuleResult(
                "prometheus_unavailable",
                "Prometheus unavailable",
                "critical",
                True,
                None,
                "query succeeds",
                60,
                str(exc),
            )
        ]
    write_json(state_path, evaluate(state, rules))
    ping_healthchecks_server_health()


def main() -> int:
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while running:
        loop_once()
        time.sleep(INTERVAL_SECONDS)
    loop_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
