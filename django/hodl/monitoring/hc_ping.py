"""Healthchecks.io integration for the monitored HODL cron wrapper."""

import functools
import importlib
import json
import os
import traceback

from cronops.healthchecks import ping, uuid_for
from cronops.redaction import sanitize_metadata, sanitize_text
from django.conf import settings
from django.utils import timezone
from monitoring_common.cron_logs import capture_cron_run


PROJECT_NAME = "HODL-2025"


def _cronops_dispatch_status(result):
    if isinstance(result, dict) and result.get("cronops_status") in {
        "queued",
        "already_queued",
        "spooled_db_unavailable",
    }:
        return result.get("cronops_status")
    return ""


def _latest_cronops_run(dotted, pid, started_at):
    try:
        from cronops.models import CronRun
    except Exception:
        return None
    try:
        return (
            CronRun.objects.select_related("job")
            .filter(job__key=dotted, pid=pid, started_at__gte=started_at)
            .order_by("-started_at")
            .first()
        )
    except Exception:
        return None


def _run_status_payload(app_run):
    if not app_run:
        return ""
    return json.dumps(
        sanitize_metadata(
            {
                "run_id": app_run.run_id,
                "status": app_run.status,
                "stage": app_run.stage,
                "message": sanitize_text(app_run.message),
                "business_success": app_run.business_success,
                "terminal_reason": app_run.terminal_reason,
                "error_type": app_run.error_type,
                "error_message": sanitize_text(app_run.error_message),
            }
        ),
        default=str,
        sort_keys=True,
    )


def monitored_cron(func):
    """Send wrapper-level status while the worker reports the final result."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        dotted = f"{func.__module__}.{func.__qualname__}"
        check_uuid = uuid_for(dotted)
        if not check_uuid:
            return func(*args, **kwargs)

        with capture_cron_run(PROJECT_NAME, dotted, check_uuid) as run:
            started_at = timezone.now()
            pid = os.getpid()
            ping(dotted, "start")

            try:
                result = func(*args, **kwargs)
                dispatch_status = _cronops_dispatch_status(result)
                if dispatch_status:
                    payload = json.dumps(sanitize_metadata(result), default=str, sort_keys=True)
                    if dispatch_status == "spooled_db_unavailable":
                        run.mark_failure(payload)
                        ping(dotted, "fail", payload)
                        return result
                    run.mark_warning(payload)
                    return result

                app_run = _latest_cronops_run(dotted, pid, started_at)
                if app_run and (
                    app_run.status in {"failure", "timeout", "stale", "degraded"}
                    or app_run.business_success is False
                ):
                    error = _run_status_payload(app_run)
                    run.mark_failure(error)
                    ping(dotted, "fail", error)
                    return result
                if app_run and app_run.status in {"deferred", "skipped"}:
                    run.mark_warning(_run_status_payload(app_run))
                    return result

                run.mark_success()
                ping(dotted, "success")
                return result
            except Exception:
                error = sanitize_text(traceback.format_exc())
                if run.is_warning_only_error(error):
                    run.mark_warning(error)
                    ping(dotted, "success", error)
                    return None

                run.mark_failure(error)
                ping(dotted, "fail", error)
                raise

    return wrapper


def patch_all_crons():
    """Patch every configured job so monitoring follows CRONJOBS automatically."""
    for cronjob in getattr(settings, "CRONJOBS", []):
        if len(cronjob) < 2:
            continue
        dotted = cronjob[1]
        try:
            module_path, func_name = dotted.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            original = getattr(mod, func_name)
            if not getattr(original, "_hc_patched", False):
                patched = monitored_cron(original)
                patched._hc_patched = True
                setattr(mod, func_name, patched)
        except (ImportError, AttributeError):
            pass
