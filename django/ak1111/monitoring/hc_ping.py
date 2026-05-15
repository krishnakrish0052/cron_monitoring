"""
Healthchecks.io ping integration for cron job monitoring.

This module provides a decorator that wraps cron functions to send
start/success/failure pings to a self-hosted Healthchecks instance.
"""

import functools
import importlib
import traceback

import requests
from monitoring_common.cron_logs import capture_cron_run

# Base URL for the Healthchecks ping API (local instance on port 9000)
HC_PING_BASE = "http://localhost:9000/ping"
PROJECT_NAME = "ak1111-backend"

# Map of function dotted paths -> Healthchecks ping UUIDs.
# Fill these in after creating checks in the Healthchecks dashboard.
PING_UUIDS = {
    "lplock.cron.dist_lp_earning": "8d8ca3ce-f0af-4c77-b122-ec783e4db51b",
    "dex.cron.dist_dex_earning": "c4faada6-25d9-46ff-8ad0-e7b05d9b54d1",
    "lplock.utils.fetch_data.fetchInvestmentsFromBlockchain": "1363e4b9-e22d-4a4e-942c-17d6111e5809",
    "dex.utils.fetch_investments.fetchTokenInvestments": "e3fad1a5-00d7-46b6-992a-5345a74e3e98",
    "lplock.utils.ak1111_price.update_ak1111_price": "b0f0ed6e-6f51-4d17-a180-3d82c0344898",
    "lplock.utils.ak1111_price.update_lp_token_price": "89391953-a76c-4c74-8b9b-f867f2c55d39",
}

# Functions to monkey-patch with monitoring
FUNCTIONS_TO_PATCH = [
    ("lplock.cron", "dist_lp_earning"),
    ("dex.cron", "dist_dex_earning"),
    ("lplock.utils.fetch_data", "fetchInvestmentsFromBlockchain"),
    ("dex.utils.fetch_investments", "fetchTokenInvestments"),
    ("lplock.utils.ak1111_price", "update_ak1111_price"),
    ("lplock.utils.ak1111_price", "update_lp_token_price"),
]


def monitored_cron(func):
    """Decorator that sends start/success/failure pings to Healthchecks."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        dotted = f"{func.__module__}.{func.__qualname__}"
        uuid = PING_UUIDS.get(dotted, "")
        if not uuid:
            # No UUID configured yet, run the function without monitoring
            return func(*args, **kwargs)

        with capture_cron_run(PROJECT_NAME, dotted, uuid) as run:
            # Ping /start to mark job as running
            try:
                requests.get(f"{HC_PING_BASE}/{uuid}/start", timeout=5)
            except Exception:
                pass

            try:
                result = func(*args, **kwargs)
                run.mark_success()
                # Ping success
                try:
                    requests.get(f"{HC_PING_BASE}/{uuid}", timeout=5)
                except Exception:
                    pass
                return result
            except Exception:
                error = traceback.format_exc()
                if run.is_warning_only_error(error):
                    run.mark_warning(error)
                    # Keep Healthchecks up for known external API plan issues;
                    # the monitoring dashboard still records the warning details.
                    try:
                        requests.post(f"{HC_PING_BASE}/{uuid}", data=error, timeout=5)
                    except Exception:
                        pass
                    return None
                else:
                    run.mark_failure(error)
                    # Ping failure with traceback details
                    try:
                        requests.post(
                            f"{HC_PING_BASE}/{uuid}/fail",
                            data=error,
                            timeout=5,
                        )
                    except Exception:
                        pass
                    raise

    return wrapper


def patch_all_crons():
    """Monkey-patch all cron functions with the monitored_cron decorator."""
    for module_path, func_name in FUNCTIONS_TO_PATCH:
        try:
            mod = importlib.import_module(module_path)
            original = getattr(mod, func_name)
            # Avoid double-patching
            if not getattr(original, "_hc_patched", False):
                patched = monitored_cron(original)
                patched._hc_patched = True
                setattr(mod, func_name, patched)
        except (ImportError, AttributeError):
            pass
