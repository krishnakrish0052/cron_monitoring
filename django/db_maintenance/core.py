from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg import sql

from db_maintenance.config import PROJECTS, db_params, project_payload
from db_maintenance.store import active_jobs, enqueue_job, recent_jobs
from monitoring_observer.collector import read_state


ACTIONS = {
    "vacuum_analyze": {
        "label": "VACUUM ANALYZE",
        "blocking": False,
        "confirmation": "table",
        "description": "Clean reusable dead tuples where possible and refresh planner stats.",
    },
    "reindex_concurrently": {
        "label": "REINDEX CONCURRENTLY",
        "blocking": False,
        "confirmation": "table",
        "description": "Rebuild table indexes online where supported.",
    },
    "vacuum_full": {
        "label": "VACUUM FULL",
        "blocking": True,
        "confirmation": "strong",
        "description": "Rewrite the table to reclaim disk. Blocks writers and should run only during quiet windows.",
    },
    "truncate_empty": {
        "label": "TRUNCATE EMPTY",
        "blocking": True,
        "confirmation": "strong",
        "description": "Remove storage from an empty table. Allowed only when live rows are zero.",
    },
}


def _conn(project: str) -> psycopg.Connection:
    return psycopg.connect(**db_params(project), autocommit=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_table_ref(schema_name: str, table_name: str) -> str:
    return f"{schema_name}.{table_name}"


def _recommendation(table: dict[str, Any], active_crons: int) -> dict[str, Any]:
    live = int(table.get("live") or 0)
    dead = int(table.get("dead") or 0)
    size_bytes = int(table.get("size_bytes") or 0)
    dead_ratio = float(table.get("dead_ratio") or 0)
    tags: list[str] = []
    severity = "ok"
    action = "none"
    message = "Healthy enough; no maintenance needed now."

    if live == 0 and size_bytes >= 100 * 1024 * 1024:
        tags.append("empty_bloated")
        severity = "blocking-risk"
        action = "truncate_empty"
        message = "Table has no live rows but still uses disk; verify business safety before truncate or VACUUM FULL."
    elif dead_ratio >= 0.4 or dead >= 500000:
        tags.append("critical")
        severity = "critical"
        action = "vacuum_analyze"
        message = "High dead-row pressure; run VACUUM ANALYZE first, then reassess bloat/index size."
    elif dead_ratio >= 0.2 or dead >= 100000:
        tags.append("warning")
        severity = "warning"
        action = "vacuum_analyze"
        message = "Dead rows are elevated; schedule VACUUM ANALYZE during a quieter window."

    if size_bytes >= 1024 * 1024 * 1024:
        tags.append("large_table")
    if active_crons and severity in ("critical", "blocking-risk"):
        tags.append("active_crons")
        if action in ("vacuum_full", "truncate_empty"):
            message += " Blocking actions are disabled while crons are active."

    return {
        "severity": severity,
        "recommended_action": action,
        "message": message,
        "tags": tags,
    }


def project_health(project: str) -> dict[str, Any]:
    project_info = PROJECTS[project]
    with _conn(project) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT state, count(*)
                FROM pg_stat_activity
                WHERE datname = current_database()
                GROUP BY state
                ORDER BY count(*) DESC
                """
            )
            states = [{"state": state or "unknown", "count": int(count)} for state, count in cur.fetchall()]

            cur.execute(
                """
                SELECT
                    schemaname,
                    relname,
                    n_live_tup,
                    n_dead_tup,
                    pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                    pg_total_relation_size(relid) AS total_bytes,
                    last_vacuum,
                    last_autovacuum,
                    last_analyze,
                    last_autoanalyze,
                    vacuum_count,
                    autovacuum_count
                FROM pg_stat_user_tables
                ORDER BY pg_total_relation_size(relid) DESC
                LIMIT 25
                """
            )
            tables = []
            for row in cur.fetchall():
                live = int(row[2] or 0)
                dead = int(row[3] or 0)
                tables.append(
                    {
                        "schema": row[0],
                        "table": row[1],
                        "live": live,
                        "dead": dead,
                        "size_pretty": row[4],
                        "size_bytes": int(row[5] or 0),
                        "dead_ratio": round(dead / max(1, live + dead), 3),
                        "last_vacuum": row[6].isoformat() if row[6] else None,
                        "last_autovacuum": row[7].isoformat() if row[7] else None,
                        "last_analyze": row[8].isoformat() if row[8] else None,
                        "last_autoanalyze": row[9].isoformat() if row[9] else None,
                        "vacuum_count": int(row[10] or 0),
                        "autovacuum_count": int(row[11] or 0),
                    }
                )

            cur.execute(
                """
                SELECT pid, mode, locktype, relation::regclass::text, granted
                FROM pg_locks
                WHERE NOT granted
                ORDER BY pid
                LIMIT 50
                """
            )
            locks = [
                {"pid": row[0], "mode": row[1], "locktype": row[2], "relation": row[3], "granted": row[4]}
                for row in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT pid, state, now() - xact_start AS age, query
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND xact_start IS NOT NULL
                  AND now() - xact_start > interval '5 minutes'
                ORDER BY xact_start ASC
                LIMIT 20
                """
            )
            long_tx = [
                {
                    "pid": row[0],
                    "state": row[1] or "unknown",
                    "age_seconds": int(row[2].total_seconds()),
                    "query": (row[3] or "")[:400],
                }
                for row in cur.fetchall()
            ]

            cur.execute(
                "SELECT sum(blks_hit)::float / nullif(sum(blks_hit)+sum(blks_read),0) FROM pg_stat_database WHERE datname = current_database()"
            )
            hit = cur.fetchone()[0]
            cur.execute("SELECT pg_size_pretty(pg_database_size(current_database())), pg_database_size(current_database())")
            size_pretty, size_bytes = cur.fetchone()

    active_crons = _active_cron_count(project_info.label)
    for table in tables:
        table["recommendation"] = _recommendation(table, active_crons)

    return {
        "project": project_info.key,
        "project_label": project_info.label,
        "connections_by_state": states,
        "top_tables": tables,
        "ungranted_locks": locks,
        "long_transactions": long_tx,
        "cache_hit_ratio": round(hit, 4) if hit is not None else None,
        "database_size": size_pretty,
        "database_size_bytes": int(size_bytes or 0),
        "active_crons": active_crons,
    }


def _active_cron_count(project_label: str) -> int:
    state = read_state()
    return sum(1 for item in state.get("active_crons", []) if item.get("project") == project_label)


def all_health() -> dict[str, Any]:
    projects = []
    for key in PROJECTS:
        try:
            projects.append(project_health(key))
        except Exception as exc:
            projects.append({"project": key, "project_label": PROJECTS[key].label, "status": "error", "error": str(exc)})
    return {
        "generated_at": _now_iso(),
        "projects": projects,
        "allowed_actions": ACTIONS,
        "active_jobs": active_jobs(),
        "recent_jobs": recent_jobs(30),
        "known_projects": project_payload(),
    }


def get_table(project: str, schema_name: str, table_name: str) -> dict[str, Any] | None:
    health = project_health(project)
    for table in health.get("top_tables", []):
        if table.get("schema") == schema_name and table.get("table") == table_name:
            table["_health"] = health
            return table
    with _conn(project) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    schemaname, relname, n_live_tup, n_dead_tup,
                    pg_size_pretty(pg_total_relation_size(relid)),
                    pg_total_relation_size(relid)
                FROM pg_stat_user_tables
                WHERE schemaname = %s AND relname = %s
                """,
                (schema_name, table_name),
            )
            row = cur.fetchone()
    if row:
        live = int(row[2] or 0)
        dead = int(row[3] or 0)
        return {
            "schema": row[0],
            "table": row[1],
            "live": live,
            "dead": dead,
            "size_pretty": row[4],
            "size_bytes": int(row[5] or 0),
            "dead_ratio": round(dead / max(1, live + dead), 3),
            "recommendation": _recommendation(
                {"live": live, "dead": dead, "size_bytes": int(row[5] or 0), "dead_ratio": round(dead / max(1, live + dead), 3)},
                health.get("active_crons", 0),
            ),
            "_health": health,
        }
    return None


def expected_confirmation(project: str, schema_name: str, table_name: str, action: str) -> str:
    if action in ("vacuum_full", "truncate_empty"):
        return f"{ACTIONS[action]['label']} {project}.{schema_name}.{table_name}"
    return table_name


def validate_action(
    project: str,
    schema_name: str,
    table_name: str,
    action: str,
    confirmation: str,
    current_job_id: int | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    if project not in PROJECTS:
        return False, "Unknown project", {}
    if action not in ACTIONS:
        return False, "Unknown action", {}
    table = get_table(project, schema_name, table_name)
    if not table:
        return False, "Table was not found in pg_stat_user_tables top table snapshot", {}
    expected = expected_confirmation(project, schema_name, table_name, action)
    if confirmation.strip() != expected:
        return False, f"Confirmation must exactly equal: {expected}", {"expected_confirmation": expected}

    health = table.pop("_health", {})
    other_active_jobs = [job for job in active_jobs(project) if job.get("id") != current_job_id]
    if other_active_jobs:
        return False, "This database already has an active maintenance job", {}
    if action == "truncate_empty" and int(table.get("live") or 0) != 0:
        return False, "TRUNCATE is allowed only for tables with zero live rows", {}
    if ACTIONS[action]["blocking"]:
        if health.get("active_crons", 0):
            return False, "Blocking maintenance is refused while project crons are running", {}
        if health.get("ungranted_locks"):
            return False, "Blocking maintenance is refused while ungranted locks exist", {}
        if health.get("long_transactions"):
            return False, "Blocking maintenance is refused while long transactions exceed 5 minutes", {}

    return True, "ok", {"table": table, "expected_confirmation": expected}


def queue_action(project: str, schema_name: str, table_name: str, action: str, confirmation: str, actor: str) -> dict[str, Any]:
    ok, message, meta = validate_action(project, schema_name, table_name, action, confirmation)
    if not ok:
        raise ValueError(message)
    project_info = PROJECTS[project]
    return enqueue_job(
        project=project,
        project_label=project_info.label,
        schema_name=schema_name,
        table_name=table_name,
        action=action,
        requested_by=actor,
        requested_at=_now_iso(),
        confirmation=confirmation,
        metadata=meta,
    )


def execute_job(job: dict[str, Any]) -> str:
    ok, message, _meta = validate_action(
        job["project"],
        job["schema_name"],
        job["table_name"],
        job["action"],
        job.get("confirmation") or "",
        current_job_id=job["id"],
    )
    if not ok:
        raise RuntimeError(message)

    action = job["action"]
    schema_name = job["schema_name"]
    table_name = job["table_name"]
    started = time.monotonic()
    with _conn(job["project"]) as conn:
        table_ident = sql.SQL(".").join([sql.Identifier(schema_name), sql.Identifier(table_name)])
        with conn.cursor() as cur:
            if action == "vacuum_analyze":
                cur.execute(sql.SQL("VACUUM (ANALYZE, VERBOSE) {}").format(table_ident))
            elif action == "reindex_concurrently":
                cur.execute(sql.SQL("REINDEX TABLE CONCURRENTLY {}").format(table_ident))
            elif action == "vacuum_full":
                cur.execute(sql.SQL("VACUUM (FULL, VERBOSE, ANALYZE) {}").format(table_ident))
            elif action == "truncate_empty":
                cur.execute(sql.SQL("TRUNCATE TABLE {}").format(table_ident))
            else:
                raise RuntimeError(f"Unsupported action: {action}")
    elapsed = time.monotonic() - started
    return f"{ACTIONS[action]['label']} completed for {_safe_table_ref(schema_name, table_name)} in {elapsed:.2f}s"
