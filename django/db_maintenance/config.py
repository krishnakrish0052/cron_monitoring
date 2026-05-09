from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DBProject:
    key: str
    label: str
    env_path: Path


PROJECTS = {
    "hodl": DBProject(
        key="hodl",
        label="HODL-2025",
        env_path=Path("/home/ubuntu/hodlbackend2/HODL-2025/.env"),
    ),
    "ak1111": DBProject(
        key="ak1111",
        label="ak1111-backend",
        env_path=Path("/home/ubuntu/ak1111-backend/.env"),
    ),
}


def _strip_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_value(value)
    return values


def db_params(project_key: str) -> dict[str, str | int]:
    project = PROJECTS[project_key]
    env = read_env(project.env_path)
    port = env.get("DB_PORT") or env.get("DB_DB_PORT") or os.environ.get("DB_PORT") or "5432"
    return {
        "dbname": env.get("DB_NAME", ""),
        "user": env.get("DB_USER", ""),
        "password": env.get("DB_USER_PASSWORD", ""),
        "host": env.get("DB_HOST") or "localhost",
        "port": int(port),
        "connect_timeout": 5,
    }


def project_payload() -> list[dict[str, str]]:
    return [{"key": item.key, "label": item.label} for item in PROJECTS.values()]

