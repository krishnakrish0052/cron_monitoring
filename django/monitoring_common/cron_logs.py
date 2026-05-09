from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


LOG_ROOT = Path(os.environ.get("MONITORING_CRON_LOG_ROOT", "/home/ubuntu/monitoring/logs/crons"))
RUNTIME_ROOT = Path(os.environ.get("MONITORING_RUNTIME_ROOT", "/home/ubuntu/monitoring/runtime/observer"))
MAX_LOG_BYTES = int(os.environ.get("MONITORING_CRON_LOG_TAIL_BYTES", "200000"))
HEARTBEAT_INTERVAL_SECONDS = float(os.environ.get("MONITORING_HEARTBEAT_INTERVAL_SECONDS", "1"))
SLOW_QUERY_SECONDS = float(os.environ.get("MONITORING_SLOW_QUERY_SECONDS", "2"))
STUCK_SECONDS = float(os.environ.get("MONITORING_STUCK_SECONDS", "180"))
IST = ZoneInfo("Asia/Kolkata")
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _iso_ist(dt: datetime) -> str:
    return dt.astimezone(IST).isoformat()


def _safe(value: str) -> str:
    cleaned = _SAFE.sub("_", value.strip())
    return cleaned[:120] or "unknown"


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _proc_snapshot(pid: int, previous: dict | None = None) -> dict:
    snapshot = {
        "pid": pid,
        "exists": False,
        "cpu_percent": None,
        "rss_bytes": None,
        "threads": None,
        "open_files": None,
    }
    proc = Path("/proc") / str(pid)
    try:
        stat = (proc / "stat").read_text(encoding="utf-8").split()
        statm = (proc / "statm").read_text(encoding="utf-8").split()
        uptime = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
        total_jiffies = sum(int(value) for value in uptime)
        proc_jiffies = int(stat[13]) + int(stat[14])
        page_size = os.sysconf("SC_PAGE_SIZE")

        snapshot.update(
            {
                "exists": True,
                "rss_bytes": int(statm[1]) * page_size,
                "threads": int(stat[19]),
            }
        )

        with contextlib.suppress(Exception):
            snapshot["open_files"] = len(list((proc / "fd").iterdir()))

        if previous and previous.get("proc_jiffies") is not None:
            total_delta = total_jiffies - previous.get("total_jiffies", total_jiffies)
            proc_delta = proc_jiffies - previous.get("proc_jiffies", proc_jiffies)
            cpu_count = os.cpu_count() or 1
            if total_delta > 0:
                snapshot["cpu_percent"] = round((proc_delta / total_delta) * cpu_count * 100, 2)

        snapshot["proc_jiffies"] = proc_jiffies
        snapshot["total_jiffies"] = total_jiffies
    except Exception:
        pass
    return snapshot


def _stack_summary() -> list[dict]:
    frames = sys._current_frames()
    current_thread = threading.get_ident()
    items = []
    for thread in threading.enumerate():
        frame = frames.get(thread.ident)
        if not frame:
            continue
        stack = []
        while frame and len(stack) < 8:
            filename = frame.f_code.co_filename
            if "/monitoring_common/" not in filename:
                stack.append(
                    {
                        "file": filename,
                        "line": frame.f_lineno,
                        "function": frame.f_code.co_name,
                    }
                )
            frame = frame.f_back
        items.append(
            {
                "thread": thread.name,
                "current": thread.ident == current_thread,
                "stack": stack,
            }
        )
    return items


class _Tee:
    def __init__(self, original, log_file, label: str):
        self.original = original
        self.log_file = log_file
        self.label = label
        self._at_line_start = True

    def write(self, data):
        if not isinstance(data, str):
            data = str(data)
        self.original.write(data)
        for chunk in data.splitlines(True):
            if self._at_line_start and chunk.strip():
                self.log_file.write(f"[{self.label}] ")
            self.log_file.write(chunk)
            if chunk.strip() and hasattr(self.log_file, "progress_callback"):
                self.log_file.progress_callback(chunk.strip(), self.label)
            self._at_line_start = chunk.endswith("\n")
        self.flush()
        return len(data)

    def flush(self):
        self.original.flush()
        self.log_file.flush()

    def isatty(self):
        return False


class CronRunCapture:
    def __init__(self, project: str, dotted_path: str, ping_uuid: str):
        self.project = project
        self.dotted_path = dotted_path
        self.ping_uuid = ping_uuid
        self.status = "running"
        self.error = ""
        self.started_at = _utcnow()
        self.ended_at = None
        self.run_id = f"{self.started_at.strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}-{uuid4().hex[:8]}"
        self.run_dir = LOG_ROOT / _safe(project) / _safe(ping_uuid)
        self.heartbeat_dir = RUNTIME_ROOT / "heartbeats" / _safe(project) / _safe(ping_uuid)
        self.log_path = self.run_dir / f"{self.run_id}.log"
        self.meta_path = self.run_dir / f"{self.run_id}.json"
        self.heartbeat_path = self.heartbeat_dir / f"{self.run_id}.json"
        self._log_file = None
        self._stdout = None
        self._stderr = None
        self._handler = None
        self._db_stack = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._proc_previous = None
        self._lock = threading.Lock()
        self._last_progress_at = self.started_at
        self._last_output = ""
        self._stage = "starting"
        self._db_query_count = 0
        self._db_total_seconds = 0.0
        self._db_slow_count = 0
        self._db_latest_query = None
        self._db_latest_slow_query = None

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or _utcnow()
        return round((end - self.started_at).total_seconds(), 3)

    def metadata(self) -> dict:
        return {
            "run_id": self.run_id,
            "project": self.project,
            "function": self.dotted_path,
            "ping_uuid": self.ping_uuid,
            "status": self.status,
            "started_at": _iso(self.started_at),
            "ended_at": _iso(self.ended_at) if self.ended_at else None,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "log_path": str(self.log_path),
            "heartbeat_path": str(self.heartbeat_path),
        }

    def __enter__(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.heartbeat_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self.log_path.open("a", encoding="utf-8", buffering=1)
        self._log_file.progress_callback = self._record_output
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = _Tee(sys.stdout, self._log_file, "stdout")
        sys.stderr = _Tee(sys.stderr, self._log_file, "stderr")

        self._handler = logging.StreamHandler(self._log_file)
        self._handler.setFormatter(
            logging.Formatter("[logging] %(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(self._handler)
        self._enter_db_wrappers()

        self._log_file.write(
            f"[monitoring] START project={self.project} function={self.dotted_path} "
            f"uuid={self.ping_uuid} run_id={self.run_id} at={_iso(self.started_at)}\n"
        )
        _write_json(self.meta_path, self.metadata())
        self._write_heartbeat()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"cron-heartbeat-{self.run_id}",
            daemon=True,
        )
        self._heartbeat_thread.start()
        return self

    def _enter_db_wrappers(self):
        try:
            from django.conf import settings
            from django.db import connections
        except Exception:
            return

        if not settings.configured:
            return

        self._db_stack = ExitStack()
        with contextlib.suppress(Exception):
            for connection in connections.all():
                with contextlib.suppress(Exception):
                    self._db_stack.enter_context(connection.execute_wrapper(self._db_execute_wrapper))

    def _db_execute_wrapper(self, execute, sql, params, many, context):
        started = time.monotonic()
        ok = False
        error = ""
        try:
            result = execute(sql, params, many, context)
            ok = True
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            elapsed = time.monotonic() - started
            sql_text = " ".join(str(sql).split())
            fingerprint = hashlib.sha1(sql_text.encode("utf-8", errors="ignore")).hexdigest()[:12]
            event = {
                "at_utc": _iso(_utcnow()),
                "duration_seconds": round(elapsed, 4),
                "many": bool(many),
                "ok": ok,
                "error": error[:300],
                "fingerprint": fingerprint,
                "sql": sql_text[:500],
            }
            with self._lock:
                self._db_query_count += 1
                self._db_total_seconds += elapsed
                self._db_latest_query = event
                self._last_progress_at = _utcnow()
                self._stage = f"db query {self._db_query_count}"
                if elapsed >= SLOW_QUERY_SECONDS:
                    self._db_slow_count += 1
                    self._db_latest_slow_query = event
                    if self._log_file:
                        self._log_file.write(
                            f"[monitoring] SLOW_DB_QUERY duration={elapsed:.3f}s "
                            f"fingerprint={fingerprint} sql={sql_text[:300]}\n"
                        )

    def _record_output(self, text: str, label: str):
        with self._lock:
            self._last_output = text[-1000:]
            self._last_progress_at = _utcnow()
            self._stage = f"{label}: {text[:120]}"

    def _heartbeat_loop(self):
        while not self._heartbeat_stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            self._write_heartbeat()

    def _write_heartbeat(self):
        current = _utcnow()
        with self._lock:
            progress_age = round((current - self._last_progress_at).total_seconds(), 3)
            stuck = self.status == "running" and progress_age >= STUCK_SECONDS
            proc = _proc_snapshot(os.getpid(), self._proc_previous)
            self._proc_previous = proc
            payload = {
                "run_id": self.run_id,
                "project": self.project,
                "function": self.dotted_path,
                "ping_uuid": self.ping_uuid,
                "pid": os.getpid(),
                "status": self.status,
                "stage": self._stage,
                "started_at_utc": _iso(self.started_at),
                "started_at_ist": _iso_ist(self.started_at),
                "updated_at_utc": _iso(current),
                "updated_at_ist": _iso_ist(current),
                "elapsed_seconds": self.duration_seconds,
                "last_progress_at_utc": _iso(self._last_progress_at),
                "last_progress_at_ist": _iso_ist(self._last_progress_at),
                "seconds_since_progress": progress_age,
                "stuck": stuck,
                "warning": "No cron progress detected" if stuck else "",
                "last_output": self._last_output,
                "db": {
                    "query_count": self._db_query_count,
                    "total_seconds": round(self._db_total_seconds, 4),
                    "slow_count": self._db_slow_count,
                    "latest_query": self._db_latest_query,
                    "latest_slow_query": self._db_latest_slow_query,
                },
                "process": {
                    key: value
                    for key, value in proc.items()
                    if key not in ("proc_jiffies", "total_jiffies")
                },
                "stack": _stack_summary(),
                "log_path": str(self.log_path),
            }
        _write_json(self.heartbeat_path, payload)

    def mark_success(self):
        with self._lock:
            self.status = "success"
            self._stage = "completed successfully"

    def mark_failure(self, error: str):
        with self._lock:
            self.status = "failure"
            self.error = error[-4000:]
            self._stage = "failed"
        if self._log_file and error:
            self._log_file.write("[monitoring] ERROR\n")
            self._log_file.write(error)
            if not error.endswith("\n"):
                self._log_file.write("\n")

    def __exit__(self, exc_type, exc, tb):
        if exc_type and self.status == "running":
            self.mark_failure("".join(traceback.format_exception(exc_type, exc, tb)))
        elif self.status == "running":
            self.mark_success()

        self.ended_at = _utcnow()
        self._heartbeat_stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2)
        if self._log_file:
            self._log_file.write(
                f"[monitoring] END status={self.status} duration={self.duration_seconds}s "
                f"at={_iso(self.ended_at)}\n"
            )

        _write_json(self.meta_path, self.metadata())
        self._write_heartbeat()

        if self._db_stack:
            self._db_stack.close()
        if self._handler:
            logging.getLogger().removeHandler(self._handler)
            self._handler.close()
        if self._stdout:
            sys.stdout = self._stdout
        if self._stderr:
            sys.stderr = self._stderr
        if self._log_file:
            self._log_file.close()
        return False


def capture_cron_run(project: str, dotted_path: str, ping_uuid: str):
    return CronRunCapture(project, dotted_path, ping_uuid)


def iter_runs_for_uuid(ping_uuid: str, limit: int = 20) -> list[dict]:
    uuid_part = _safe(ping_uuid)
    runs = []
    for meta_path in LOG_ROOT.glob(f"*/*/{uuid_part}/*.json"):
        with contextlib.suppress(Exception):
            runs.append(json.loads(meta_path.read_text(encoding="utf-8")))

    # The common layout is project/uuid/run.json. Keep the broader glob for
    # future migration safety, then sort by timestamp.
    if not runs:
        for meta_path in LOG_ROOT.glob(f"*/{uuid_part}/*.json"):
            with contextlib.suppress(Exception):
                runs.append(json.loads(meta_path.read_text(encoding="utf-8")))

    runs.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    return runs[:limit]


def read_run_log(ping_uuid: str, run_id: str, max_bytes: int = MAX_LOG_BYTES) -> dict:
    uuid_part = _safe(ping_uuid)
    run_part = _safe(run_id)
    candidates = list(LOG_ROOT.glob(f"*/{uuid_part}/{run_part}.log"))
    if not candidates:
        return {"found": False, "content": "", "truncated": False}

    path = candidates[0]
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            content = handle.read().decode("utf-8", errors="replace")
            truncated = True
        else:
            content = handle.read().decode("utf-8", errors="replace")
            truncated = False

    return {
        "found": True,
        "content": content,
        "truncated": truncated,
        "size": size,
        "path": str(path),
    }
