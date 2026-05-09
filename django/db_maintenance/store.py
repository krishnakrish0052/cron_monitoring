from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


RUNTIME_ROOT = Path(os.environ.get("DB_MAINTENANCE_RUNTIME", "/home/ubuntu/monitoring/runtime/db-maintenance"))
DB_PATH = RUNTIME_ROOT / "jobs.sqlite3"


def connect() -> sqlite3.Connection:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            project_label TEXT NOT NULL,
            schema_name TEXT NOT NULL,
            table_name TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            duration_seconds REAL,
            confirmation TEXT,
            output TEXT,
            error TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_project ON jobs(status, project)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_requested_at ON jobs(requested_at DESC)")
    conn.commit()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
    except (TypeError, ValueError):
        data["metadata"] = {}
    return data


def recent_jobs(limit: int = 25) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY requested_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def active_jobs(project: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM jobs WHERE status IN ('queued', 'running')"
    params: tuple[Any, ...] = ()
    if project:
        sql += " AND project = ?"
        params = (project,)
    sql += " ORDER BY requested_at ASC, id ASC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [row_to_dict(row) for row in rows]


def enqueue_job(
    *,
    project: str,
    project_label: str,
    schema_name: str,
    table_name: str,
    action: str,
    requested_by: str,
    requested_at: str,
    confirmation: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    with connect() as conn:
        running = conn.execute(
            "SELECT id FROM jobs WHERE project = ? AND status IN ('queued', 'running') LIMIT 1",
            (project,),
        ).fetchone()
        if running:
            raise ValueError(f"{project_label} already has an active maintenance job")
        cursor = conn.execute(
            """
            INSERT INTO jobs (
                project, project_label, schema_name, table_name, action, status,
                requested_by, requested_at, confirmation, metadata_json
            ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                project,
                project_label,
                schema_name,
                table_name,
                action,
                requested_by,
                requested_at,
                confirmation,
                json.dumps(metadata, sort_keys=True),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_to_dict(row)


def claim_next_job() -> dict[str, Any] | None:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY requested_at ASC, id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
    return row_to_dict(row)


def finish_job(job_id: int, status: str, output: str = "", error: str = "", duration_seconds: float | None = None) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, output = ?, error = ?, duration_seconds = ?,
                finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (status, output[-20000:], error[-10000:], duration_seconds, job_id),
        )
        conn.commit()

