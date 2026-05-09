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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4
from zoneinfo import ZoneInfo


LOG_ROOT = Path(os.environ.get("MONITORING_CRON_LOG_ROOT", "/home/ubuntu/monitoring/logs/crons"))
RUNTIME_ROOT = Path(os.environ.get("MONITORING_RUNTIME_ROOT", "/home/ubuntu/monitoring/runtime/observer"))
MAX_LOG_BYTES = int(os.environ.get("MONITORING_CRON_LOG_TAIL_BYTES", "200000"))
HEARTBEAT_INTERVAL_SECONDS = float(os.environ.get("MONITORING_HEARTBEAT_INTERVAL_SECONDS", "1"))
SLOW_QUERY_SECONDS = float(os.environ.get("MONITORING_SLOW_QUERY_SECONDS", "2"))
STUCK_SECONDS = float(os.environ.get("MONITORING_STUCK_SECONDS", "180"))
MAX_EVENTS = int(os.environ.get("MONITORING_CRON_MAX_EVENTS", "30000"))
MAX_TRACE_EVENTS = int(os.environ.get("MONITORING_CRON_MAX_TRACE_EVENTS", "8000"))
MAX_RECENT_EVENTS = int(os.environ.get("MONITORING_CRON_RECENT_EVENTS", "80"))
TRACE_ENABLED = os.environ.get("MONITORING_CRON_TRACE", "1") != "0"
IST = ZoneInfo("Asia/Kolkata")
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_SQL_OP_TABLE = re.compile(
    r"^\s*(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([`\".\w]+)?",
    re.IGNORECASE,
)
_SECRET_QUERY_KEYS = {
    "apikey",
    "api_key",
    "key",
    "token",
    "access_token",
    "password",
    "secret",
    "signature",
}


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


def _sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except Exception:
        return str(url)[:500]

    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SECRET_QUERY_KEYS:
            query.append((key, "***"))
        else:
            query.append((key, value[:180]))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))[:700]


def _summarize_sql(sql: str) -> dict:
    sql_text = " ".join(str(sql).split())
    match = _SQL_OP_TABLE.search(sql_text)
    operation = ""
    table = ""
    if match:
        operation = match.group(1).upper().replace(" INTO", "").replace(" FROM", "")
        table = (match.group(2) or "").strip('`"')
    return {
        "operation": operation or "SQL",
        "table": table,
        "fingerprint": hashlib.sha1(sql_text.encode("utf-8", errors="ignore")).hexdigest()[:12],
        "sql": sql_text[:500],
    }


def _classify_external_payload(url: str, payload: object) -> dict:
    classification = {"type": "", "severity": "info", "message": ""}
    host = ""
    with contextlib.suppress(Exception):
        host = urlsplit(url).netloc.lower()

    if isinstance(payload, dict):
        status = str(payload.get("status", ""))
        message = str(payload.get("message", ""))
        result = payload.get("result")
        result_text = result if isinstance(result, str) else ""
        combined = f"{message} {result_text}".lower()
        if status == "0" and "deprecated v1 endpoint" in combined:
            classification.update(
                {
                    "type": "etherscan_v1_deprecated",
                    "severity": "error",
                    "message": "External explorer API returned deprecated V1 endpoint error; app code expects a transaction list.",
                }
            )
        elif status == "0" and (
            "free api access is not supported" in combined
            or "upgrade your api plan" in combined
        ):
            classification.update(
                {
                    "type": "etherscan_paid_tier_required",
                    "severity": "error",
                    "message": "Etherscan V2 rejected this chain for the current API plan; Base chain API access requires a plan/key with full chain coverage.",
                }
            )
        elif status == "0" and isinstance(result, str):
            classification.update(
                {
                    "type": "explorer_string_result",
                    "severity": "error" if "notok" in message.lower() else "warning",
                    "message": "External explorer API returned a string result; cron code may fail if it iterates it as transactions.",
                }
            )
        elif status == "0" and "no transactions found" in combined:
            classification.update(
                {
                    "type": "explorer_no_transactions",
                    "severity": "info",
                    "message": "External explorer API returned no transactions.",
                }
            )
    elif any(name in host for name in ("bscscan", "basescan", "etherscan")):
        classification.update(
            {
                "type": "explorer_unparsed_response",
                "severity": "warning",
                "message": "External explorer API response was not JSON object shaped.",
            }
        )

    return classification


def _jsonl_tail(path: Path, limit: int = MAX_RECENT_EVENTS) -> list[dict]:
    if not path.exists():
        return []
    lines = []
    with contextlib.suppress(Exception):
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - MAX_LOG_BYTES))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    events = []
    for line in lines[-limit:]:
        with contextlib.suppress(Exception):
            events.append(json.loads(line))
    return events


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
        self.events_path = self.run_dir / f"{self.run_id}.events.jsonl"
        self.heartbeat_path = self.heartbeat_dir / f"{self.run_id}.json"
        self._log_file = None
        self._events_file = None
        self._stdout = None
        self._stderr = None
        self._handler = None
        self._db_stack = None
        self._original_request = None
        self._original_trace = None
        self._original_thread_trace = None
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
        self._http_count = 0
        self._http_error_count = 0
        self._latest_http = None
        self._external_error = None
        self._last_trace = None
        self._event_count = 0
        self._trace_event_count = 0
        self._trace_dropped = 0
        self._recent_events = []

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
            "events_path": str(self.events_path),
            "heartbeat_path": str(self.heartbeat_path),
            "started_at_ist": _iso_ist(self.started_at),
            "ended_at_ist": _iso_ist(self.ended_at) if self.ended_at else None,
            "external_error": self._external_error,
        }

    def __enter__(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.heartbeat_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self.log_path.open("a", encoding="utf-8", buffering=1)
        self._events_file = self.events_path.open("a", encoding="utf-8", buffering=1)
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
        self._enter_http_wrapper()
        self._enter_python_trace()

        self._log_file.write(
            f"[monitoring] START project={self.project} function={self.dotted_path} "
            f"uuid={self.ping_uuid} run_id={self.run_id} at_utc={_iso(self.started_at)} "
            f"at_ist={_iso_ist(self.started_at)}\n"
        )
        self._emit_event("run_start", "info", "Cron run started")
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

    def _enter_http_wrapper(self):
        try:
            import requests
        except Exception:
            return

        self._original_request = requests.sessions.Session.request
        capture = self

        def monitored_request(session, method, url, **kwargs):
            started = time.monotonic()
            clean_url = _sanitize_url(str(url))
            capture._emit_event(
                "http_start",
                "info",
                f"HTTP {str(method).upper()} {clean_url}",
                {"method": str(method).upper(), "url": clean_url},
            )
            ok = False
            status_code = None
            response_summary = {}
            classification = {"type": "", "severity": "info", "message": ""}
            try:
                response = capture._original_request(session, method, url, **kwargs)
                ok = True
                status_code = getattr(response, "status_code", None)
                content_type = getattr(response, "headers", {}).get("content-type", "")
                parsed = None
                if "json" in content_type.lower():
                    with contextlib.suppress(Exception):
                        parsed = response.json()
                if isinstance(parsed, dict):
                    result = parsed.get("result")
                    response_summary = {
                        "status": parsed.get("status"),
                        "message": str(parsed.get("message", ""))[:300],
                        "result_type": type(result).__name__,
                        "result_count": len(result) if isinstance(result, list) else None,
                        "result_preview": result[:500] if isinstance(result, str) else "",
                    }
                    classification = _classify_external_payload(str(url), parsed)
                return response
            except Exception as exc:
                response_summary = {"error": str(exc)[:500]}
                classification = {"type": "http_exception", "severity": "error", "message": str(exc)[:500]}
                raise
            finally:
                elapsed = time.monotonic() - started
                event = {
                    "method": str(method).upper(),
                    "url": clean_url,
                    "status_code": status_code,
                    "duration_seconds": round(elapsed, 4),
                    "ok": ok and (status_code is None or int(status_code) < 400),
                    "response": response_summary,
                    "classification": classification,
                }
                with capture._lock:
                    capture._http_count += 1
                    capture._latest_http = event
                    if classification.get("severity") in ("warning", "error"):
                        capture._http_error_count += 1
                        capture._external_error = classification
                    capture._last_progress_at = _utcnow()
                    capture._stage = f"http {event['method']} {status_code or '-'} {clean_url[:90]}"
                capture._emit_event(
                    "http_response",
                    classification.get("severity") or "info",
                    classification.get("message") or f"HTTP {event['method']} completed",
                    event,
                )

        requests.sessions.Session.request = monitored_request

    def _trace_roots(self) -> list[str]:
        roots = [os.getcwd(), "/home/ubuntu/ak1111-backend", "/home/ubuntu/hodlbackend2/HODL-2025"]
        configured = os.environ.get("MONITORING_TRACE_ROOTS", "")
        roots.extend(item for item in configured.split(":") if item)
        return [str(Path(item).resolve()) for item in roots if item]

    def _enter_python_trace(self):
        if not TRACE_ENABLED:
            return

        roots = self._trace_roots()
        capture = self

        def should_trace(filename: str) -> bool:
            if not filename:
                return False
            if filename.startswith("<"):
                return False
            path = str(Path(filename).resolve())
            if not path.endswith(".py"):
                return False
            if "/venv/" in path or "/site-packages/" in path or "/home/ubuntu/monitoring/" in path:
                return False
            return any(path.startswith(root) for root in roots)

        def tracer(frame, event, arg):
            if event not in ("call", "line", "return", "exception"):
                return tracer
            filename = frame.f_code.co_filename
            if not should_trace(filename):
                return tracer
            if capture._trace_event_count >= MAX_TRACE_EVENTS:
                capture._trace_dropped += 1
                return tracer
            payload = {
                "file": filename,
                "line": frame.f_lineno,
                "function": frame.f_code.co_name,
                "event": event,
            }
            if event == "exception" and arg:
                exc_type, exc, _tb = arg
                payload["exception"] = f"{getattr(exc_type, '__name__', str(exc_type))}: {exc}"
            capture._trace_event_count += 1
            capture._last_trace = payload
            if event != "line" or capture._trace_event_count <= 250 or capture._trace_event_count % 25 == 0:
                capture._emit_event("python_trace", "debug", f"{event} {Path(filename).name}:{frame.f_lineno}", payload)
            return tracer

        self._original_trace = sys.gettrace()
        self._original_thread_trace = threading.gettrace()
        sys.settrace(tracer)
        threading.settrace(tracer)

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
                "at_ist": _iso_ist(_utcnow()),
                "duration_seconds": round(elapsed, 4),
                "many": bool(many),
                "ok": ok,
                "error": error[:300],
                **_summarize_sql(sql_text),
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
            self._emit_event(
                "db_query",
                "warning" if elapsed >= SLOW_QUERY_SECONDS or not ok else "debug",
                f"DB {event['operation']} {event.get('table') or ''} {elapsed:.3f}s".strip(),
                event,
            )

    def _record_output(self, text: str, label: str):
        with self._lock:
            self._last_output = text[-1000:]
            self._last_progress_at = _utcnow()
            self._stage = f"{label}: {text[:120]}"
        self._emit_event(label, "info", text[:500], {"stream": label})

    def _emit_event(self, event_type: str, severity: str, message: str, data: dict | None = None):
        current = _utcnow()
        payload = {
            "at_utc": _iso(current),
            "at_ist": _iso_ist(current),
            "elapsed_seconds": self.duration_seconds,
            "type": event_type,
            "severity": severity,
            "message": message[:1000],
            "data": data or {},
        }
        with self._lock:
            if self._event_count >= MAX_EVENTS and event_type not in ("run_end", "failure", "http_response"):
                return
            self._event_count += 1
            self._recent_events.append(payload)
            self._recent_events = self._recent_events[-MAX_RECENT_EVENTS:]
        if self._events_file:
            with contextlib.suppress(Exception):
                self._events_file.write(json.dumps(payload, sort_keys=True) + "\n")

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
                "event_count": self._event_count,
                "trace_event_count": self._trace_event_count,
                "trace_dropped": self._trace_dropped,
                "recent_events": list(self._recent_events),
                "db": {
                    "query_count": self._db_query_count,
                    "total_seconds": round(self._db_total_seconds, 4),
                    "slow_count": self._db_slow_count,
                    "latest_query": self._db_latest_query,
                    "latest_slow_query": self._db_latest_slow_query,
                },
                "http": {
                    "request_count": self._http_count,
                    "error_count": self._http_error_count,
                    "latest": self._latest_http,
                    "external_error": self._external_error,
                },
                "process": {
                    key: value
                    for key, value in proc.items()
                    if key not in ("proc_jiffies", "total_jiffies")
                },
                "stack": _stack_summary(),
                "latest_trace": self._last_trace,
                "log_path": str(self.log_path),
                "events_path": str(self.events_path),
            }
        _write_json(self.heartbeat_path, payload)

    def mark_success(self):
        with self._lock:
            self.status = "success"
            self._stage = "completed successfully"
        self._emit_event("run_success", "success", "Cron run completed successfully")

    def mark_failure(self, error: str):
        with self._lock:
            self.status = "failure"
            self.error = error[-4000:]
            self._stage = "failed"
        self._emit_event(
            "failure",
            "error",
            "Cron function raised an exception. This is app cron code, not a ping failure.",
            {"traceback": self.error, "external_error": self._external_error},
        )
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
                f"at_utc={_iso(self.ended_at)} at_ist={_iso_ist(self.ended_at)}\n"
            )
        self._emit_event("run_end", self.status, f"Cron run ended with status {self.status}", self.metadata())

        _write_json(self.meta_path, self.metadata())
        self._write_heartbeat()

        if self._original_request:
            with contextlib.suppress(Exception):
                import requests

                requests.sessions.Session.request = self._original_request
        if TRACE_ENABLED:
            with contextlib.suppress(Exception):
                sys.settrace(self._original_trace)
            with contextlib.suppress(Exception):
                threading.settrace(self._original_thread_trace)
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
        if self._events_file:
            self._events_file.close()
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


def read_run_events(ping_uuid: str, run_id: str, limit: int = MAX_RECENT_EVENTS) -> dict:
    uuid_part = _safe(ping_uuid)
    run_part = _safe(run_id)
    candidates = list(LOG_ROOT.glob(f"*/{uuid_part}/{run_part}.events.jsonl"))
    if not candidates:
        return {"found": False, "events": []}
    return {"found": True, "events": _jsonl_tail(candidates[0], limit), "path": str(candidates[0])}


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
