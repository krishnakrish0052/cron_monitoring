"""Local registry linking active HODL CronOps jobs to Healthchecks checks."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import UUID


DEFAULT_REGISTRY_PATH = Path("/home/ubuntu/monitoring/runtime/hodl-cronops-checks.json")
_CACHE = {"path": None, "mtime_ns": None, "jobs": {}}


def registry_path() -> Path:
    return Path(os.environ.get("HODL_HEALTHCHECKS_REGISTRY_PATH", DEFAULT_REGISTRY_PATH))


def load_registry_jobs() -> dict[str, dict]:
    """Load a validated mapping; a missing or partial file is safe to ignore."""
    path = registry_path()
    try:
        stat = path.stat()
    except OSError:
        return {}

    if _CACHE["path"] == path and _CACHE["mtime_ns"] == stat.st_mtime_ns:
        return _CACHE["jobs"]

    jobs: dict[str, dict] = {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key, record in (payload.get("jobs") or {}).items():
            if not isinstance(key, str) or not isinstance(record, dict):
                continue
            value = record.get("uuid")
            jobs[key] = {**record, "uuid": str(UUID(str(value)))}
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        jobs = {}

    _CACHE.update({"path": path, "mtime_ns": stat.st_mtime_ns, "jobs": jobs})
    return jobs


def uuid_for_job(job_key: str) -> str:
    return str((load_registry_jobs().get(job_key) or {}).get("uuid") or "")


def write_registry(*, project_code: str, jobs: dict[str, dict]) -> Path:
    """Atomically publish the registry with restrictive permissions."""
    path = registry_path()
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "project_code": str(project_code),
        "jobs": jobs,
    }
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o640)
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass

    _CACHE.update({"path": None, "mtime_ns": None, "jobs": {}})
    return path
