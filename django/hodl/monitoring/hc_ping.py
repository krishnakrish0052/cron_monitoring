"""
Healthchecks.io ping integration for HODL-2025 cron job monitoring.
"""

import functools
import importlib
import traceback

import requests
from monitoring_common.cron_logs import capture_cron_run

HC_PING_BASE = "http://localhost:9000/ping"
PROJECT_NAME = "HODL-2025"

# Map of function dotted paths -> Healthchecks ping UUIDs.
# Fill in after creating checks in the Healthchecks dashboard.
PING_UUIDS = {
    # Daily earnings
    "liquidity.cron_in.earning_in": "bb1cfdd1-bd68-4ab1-b581-d857b64e5a33",
    "liquidity2.cron.lp2_earning": "0069c15c-91f7-408c-8b2e-a9eaf5f78c74",
    "svr4.cron.svr_earning_4": "0298cd62-ebd8-4943-8db7-d8c1e5de3961",
    "akasha.cron.distribute_earning": "7526f6ef-ef07-488a-8f10-a5f4f92c5e10",
    # Blockchain fetchers (every 5 min)
    "akita.utils.fetchNFTFromBlockchain": "1b184af8-3366-4bbe-9458-a0e496e2408a",
    "liquidity.utils.fetchInvestmentsFromBlockchain": "84f6ba40-7cbc-4149-95ff-51adea8964cf",
    "user.service.fetchYbaFee": "76f2b38b-aa6a-4e7b-944d-f5478f19ebdd",
    "truebreath.utils.fetchTruebreatheBlockchain": "9012a580-a5e2-410c-bf28-9811fb0b8c9b",
    "truebreath.utils2.fetchTruebreatheBase": "493c5494-bffa-4289-b89a-601b8e0b031c",
    "akasha.utils.fetch_deposite.fetch_deposites": "847cfcce-6e78-4916-bb5b-262df6241cd5",
    "akasha.utils.fetch_super_nodes.fetch_super_nodes": "a29c56d5-054e-4827-b231-e5b923ad2686",
    # SVR4 blockchain fetchers (every 9 min)
    "svr4.utils.fetch_svr_lp.fetchSvrLp": "45f22d15-fbea-45cb-acca-5b7b13a4093c",
    "svr4.utils.fetch_svr_package_base.fetch_svr_pkg_base": "3c813025-839a-449a-a095-6891ad6ef889",
    "svr4.utils.fetch_svr_package_bsc.fetchSovereignBlockchain": "e360e926-d391-492a-97e9-d328a440937c",
    # Analytics (daily midnight)
    "analytics.cron.calculate_analytics_lp_vol": "e2b8463d-7b43-4100-94e9-3fa135e454d6",
    "analytics.cron.calculate_analytics_lp_level_vol": "23cafe3d-ed35-471d-a2db-42d016ce8a3f",
    "analytics.cron.calculate_analytics_lp_level": "de896294-c631-4547-b659-d60832868cd6",
    "analytics.cron2.calculate_analytics_lp_vol": "df66c9ea-d48f-4567-b38b-5c1bc69f1fbc",
    "analytics.cron2.calculate_analytics_lp_level_vol": "b7853048-5d54-40ea-944c-6ca1af5b8673",
    "analytics.cron2.calculate_analytics_lp_level": "fce7513d-6f2c-4757-8fb3-701d4d683895",
    # Maintenance (daily midnight)
    "user.cron.delete_logs": "a7f02dd3-924d-4b6b-8cea-24301f218237",
    "user.cron.mark_korean": "9241fa2e-d091-4741-a104-7b16c4af6657",
    # Ranks (daily 00:01)
    "sovereign.cron.update_rank": "beb278d9-d6da-4341-b1ad-a8c08348c232",
    "svr4.cron.update_rank_svr4_parallel": "63c88700-b0c7-4cc1-81ef-0fe294f9a1d1",
    "svr4.cron.svr4_total_business_aggregate": "b659c4c5-1cd7-45d9-9c1f-adc83ec51c69",
    # Other periodic
    "blackcard.cron.add_blackcard_users": "0c65cc53-3fae-413f-aef9-5775e63eac69",
    "akasha.cron.update_ak1111_price": "ad8bcfd4-9b13-4c9a-8213-0ffab1b09547",
    "akasha.cron.mint_deposit": "25f779fa-cc5a-4d86-8bc9-a9b9f4bb89b1",
}

# Functions to monkey-patch
FUNCTIONS_TO_PATCH = [
    ("liquidity.cron_in", "earning_in"),
    ("liquidity2.cron", "lp2_earning"),
    ("svr4.cron", "svr_earning_4"),
    ("akasha.cron", "distribute_earning"),
    ("akita.utils", "fetchNFTFromBlockchain"),
    ("liquidity.utils", "fetchInvestmentsFromBlockchain"),
    ("user.service", "fetchYbaFee"),
    ("truebreath.utils", "fetchTruebreatheBlockchain"),
    ("truebreath.utils2", "fetchTruebreatheBase"),
    ("akasha.utils.fetch_deposite", "fetch_deposites"),
    ("akasha.utils.fetch_super_nodes", "fetch_super_nodes"),
    ("svr4.utils.fetch_svr_lp", "fetchSvrLp"),
    ("svr4.utils.fetch_svr_package_base", "fetch_svr_pkg_base"),
    ("svr4.utils.fetch_svr_package_bsc", "fetchSovereignBlockchain"),
    ("analytics.cron", "calculate_analytics_lp_vol"),
    ("analytics.cron", "calculate_analytics_lp_level_vol"),
    ("analytics.cron", "calculate_analytics_lp_level"),
    ("analytics.cron2", "calculate_analytics_lp_vol"),
    ("analytics.cron2", "calculate_analytics_lp_level_vol"),
    ("analytics.cron2", "calculate_analytics_lp_level"),
    ("user.cron", "delete_logs"),
    ("user.cron", "mark_korean"),
    ("sovereign.cron", "update_rank"),
    ("svr4.cron", "update_rank_svr4_parallel"),
    ("svr4.cron", "svr4_total_business_aggregate"),
    ("blackcard.cron", "add_blackcard_users"),
    ("akasha.cron", "update_ak1111_price"),
    ("akasha.cron", "mint_deposit"),
]


def monitored_cron(func):
    """Decorator that sends start/success/failure pings to Healthchecks."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        dotted = f"{func.__module__}.{func.__qualname__}"
        uuid = PING_UUIDS.get(dotted, "")
        if not uuid:
            return func(*args, **kwargs)

        with capture_cron_run(PROJECT_NAME, dotted, uuid) as run:
            try:
                requests.get(f"{HC_PING_BASE}/{uuid}/start", timeout=5)
            except Exception:
                pass

            try:
                result = func(*args, **kwargs)
                run.mark_success()
                try:
                    requests.get(f"{HC_PING_BASE}/{uuid}", timeout=5)
                except Exception:
                    pass
                return result
            except Exception:
                error = traceback.format_exc()
                run.mark_failure(error)
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
            if not getattr(original, "_hc_patched", False):
                patched = monitored_cron(original)
                patched._hc_patched = True
                setattr(mod, func_name, patched)
        except (ImportError, AttributeError):
            pass
