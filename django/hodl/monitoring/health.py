"""Server health check utilities for HODL-2025."""

import shutil
import time

from django.db import connection


def check_database():
    try:
        start = time.monotonic()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return {"ok": True, "latency_ms": latency_ms}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_disk():
    try:
        usage = shutil.disk_usage("/")
        usage_percent = round(usage.used / usage.total * 100, 1)
        free_gb = round(usage.free / (1024**3), 2)
        return {"ok": usage_percent < 90, "usage_percent": usage_percent, "free_gb": free_gb}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_memory():
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                meminfo[parts[0].rstrip(":")] = int(parts[1])
        total_mb = meminfo.get("MemTotal", 0) / 1024
        available_mb = meminfo.get("MemAvailable", 0) / 1024
        usage_percent = round((1 - available_mb / total_mb) * 100, 1) if total_mb > 0 else 0
        return {
            "ok": available_mb > (total_mb * 0.1),
            "usage_percent": usage_percent,
            "available_mb": round(available_mb, 0),
            "total_mb": round(total_mb, 0),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_full_health():
    checks = {
        "database": check_database(),
        "disk": check_disk(),
        "memory": check_memory(),
    }
    all_ok = all(c["ok"] for c in checks.values())
    return {"status": "ok" if all_ok else "degraded", "checks": checks}
