"""Synchronize active HODL CronOps jobs to local Healthchecks checks."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from cronsim import CronSim
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hc.accounts.models import Project
from hc.api.models import Check
from healthchecks_custom.hodl_registry import write_registry


DEFAULT_PROJECT_CODE = "08c7b7c6-776d-4bba-b6f7-2592efa14281"
DEFAULT_INVENTORY_URL = "http://127.0.0.1:8001/api/cronops/inventory/"
MANAGED_TAG = "hodl-cronops"
MARKER_PREFIX = "[cronops-job:"

# Existing checks predate the registry. Match by these exact names once, then
# persist a durable marker in each check description.
LEGACY_CHECK_NAMES = {
    "akasha.cron.distribute_earning": "AK1111 Supernode Earnings",
    "akasha.cron.update_ak1111_price": "Update AK1111 Price",
    "akasha.utils.fetch_deposite.fetch_deposites": "Fetch AK1111 Deposits",
    "akasha.utils.fetch_super_nodes.fetch_super_nodes": "Fetch AK1111 Super Nodes",
    "akita.utils.fetchNFTFromBlockchain": "Fetch AKITA NFTs",
    "analytics.cron.calculate_analytics_lp_level": "Analytics LP v1 Level",
    "analytics.cron.calculate_analytics_lp_level_vol": "Analytics LP v1 Level Volume",
    "analytics.cron.calculate_analytics_lp_vol": "Analytics LP v1 Volume",
    "analytics.cron2.calculate_analytics_lp_level": "Analytics LP v2 Level",
    "analytics.cron2.calculate_analytics_lp_level_vol": "Analytics LP v2 Level Volume",
    "analytics.cron2.calculate_analytics_lp_vol": "Analytics LP v2 Volume",
    "blackcard.cron.add_blackcard_users": "Sync Blackcard Users",
    "liquidity.utils.fetchInvestmentsFromBlockchain": "Fetch LP Investments",
    "liquidity2.cron.lp2_earning": "LP v2 Earnings (lp2_earning)",
    "sovereign.cron.update_rank": "SVR v1 Rank Update",
    "svr4.cron.svr4_total_business_aggregate": "SVR4 Business Aggregate",
    "svr4.cron.svr_earning_4": "SVR4 Earnings (svr_earning_4)",
    "svr4.cron.update_rank_svr4_parallel": "SVR4 Rank Update",
    "svr4.utils.fetch_svr_lp.fetchSvrLp": "Fetch SVR4 LP",
    "svr4.utils.fetch_svr_package_base.fetch_svr_pkg_base": "Fetch SVR4 Packages Base",
    "svr4.utils.fetch_svr_package_bsc.fetchSovereignBlockchain": "Fetch SVR4 Packages BSC",
    "truebreath.utils.fetchTruebreatheBlockchain": "Fetch Truebreath BSC",
    "truebreath.utils2.fetchTruebreatheBase": "Fetch Truebreath Base",
    "user.cron.delete_logs": "Delete Old Logs",
    "user.cron.mark_korean": "Mark Korean Users",
    "user.service.fetchYbaFee": "Fetch YBA Fees",
}


def _marker(job_key: str) -> str:
    return f"{MARKER_PREFIX}{job_key}]"


def _merge_tags(value: str) -> str:
    tags = [tag for tag in value.split() if tag]
    if MANAGED_TAG not in tags:
        tags.append(MANAGED_TAG)
    return " ".join(tags)


def _merge_marker(description: str, job_key: str) -> str:
    marker = _marker(job_key)
    if marker in description:
        return description
    base = description.rstrip()
    return f"{base}\n{marker}" if base else marker


def _schedule_period_seconds(schedule: str) -> int:
    now = datetime.now(timezone.utc)
    try:
        first = next(CronSim(schedule, now))
        second = next(CronSim(schedule, first))
        return max(60, int((second - first).total_seconds()))
    except Exception:
        return 24 * 60 * 60


def _grace_seconds(job: dict) -> int:
    sla = int(job.get("sla_seconds") or 0)
    if sla > 0:
        return max(60, sla)

    cadence = _schedule_period_seconds(str(job["schedule"]))
    expected = int(job.get("expected_seconds") or 0)
    if cadence <= 60 * 60:
        return max(15 * 60, min(12 * 60 * 60, cadence * 3), expected + 5 * 60)
    return max(60 * 60, min(12 * 60 * 60, expected + 5 * 60))


class Command(BaseCommand):
    help = "Sync active HODL CronOps jobs into local Healthchecks and publish their UUID registry."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Create/update checks and publish the registry.")
        parser.add_argument(
            "--inventory-url",
            default=os.environ.get("HODL_CRONOPS_INVENTORY_URL", DEFAULT_INVENTORY_URL),
        )
        parser.add_argument(
            "--project-code",
            default=os.environ.get("HODL_HEALTHCHECKS_PROJECT_CODE", DEFAULT_PROJECT_CODE),
        )
        parser.add_argument("--timeout", type=float, default=5.0)

    def handle(self, *args, **options):
        jobs = self._load_inventory(options["inventory_url"], options["timeout"])
        try:
            project = Project.objects.get(code=options["project_code"])
        except Project.DoesNotExist as exc:
            raise CommandError(f"HODL Healthchecks project does not exist: {options['project_code']}") from exc

        checks = list(Check.objects.filter(project=project).order_by("id"))
        resolved, created, changed = self._resolve_checks(jobs, checks, project)
        registry_jobs = {
            job["key"]: {
                "uuid": str(check.code),
                "check_name": check.name,
                "schedule": job["schedule"],
                "grace_seconds": _grace_seconds(job),
            }
            for job, check in resolved
        }

        self.stdout.write(
            f"HODL CronOps inventory: jobs={len(jobs)} linked={len(resolved)} "
            f"new={len(created)} updates={len(changed)}"
        )
        for job, check in resolved:
            action = "create" if check in created else "update" if check in changed else "keep"
            self.stdout.write(f"  {action:6} {job['key']} -> {check.name}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry-run only; pass --apply to write checks and registry."))
            return

        with transaction.atomic():
            for check in created:
                check.save()
                check.assign_all_channels()
            for check in changed:
                check.save()

        path = write_registry(project_code=str(project.code), jobs=registry_jobs)
        self.stdout.write(self.style.SUCCESS(f"Published {len(registry_jobs)} HODL CronOps check bindings to {path}"))

    def _load_inventory(self, url: str, timeout: float) -> list[dict]:
        try:
            with urllib.request.urlopen(url, timeout=max(0.1, float(timeout))) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            raise CommandError(f"Could not fetch local HODL CronOps inventory: {exc}") from exc

        raw_jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(raw_jobs, list) or not raw_jobs:
            raise CommandError("CronOps inventory returned no active jobs.")

        jobs: list[dict] = []
        keys: set[str] = set()
        for raw in raw_jobs:
            if not isinstance(raw, dict):
                raise CommandError("CronOps inventory contains an invalid job record.")
            key = str(raw.get("key") or "").strip()
            schedule = str(raw.get("schedule") or "").strip()
            if not key or not schedule or key in keys:
                raise CommandError(f"CronOps inventory contains invalid job data for {key or '<unknown>'}.")
            keys.add(key)
            jobs.append({**raw, "key": key, "schedule": schedule})
        return sorted(jobs, key=lambda job: job["key"])

    def _resolve_checks(self, jobs: list[dict], checks: list[Check], project: Project):
        by_marker: dict[str, list[Check]] = {}
        by_name: dict[str, list[Check]] = {}
        for check in checks:
            by_name.setdefault(check.name, []).append(check)
            for job in jobs:
                if _marker(job["key"]) in check.desc:
                    by_marker.setdefault(job["key"], []).append(check)

        resolved: list[tuple[dict, Check]] = []
        created: list[Check] = []
        changed: list[Check] = []
        used_check_ids: set[int] = set()

        for job in jobs:
            key = job["key"]
            matches = by_marker.get(key, [])
            if len(matches) > 1:
                raise CommandError(f"More than one Healthchecks check has marker for {key}.")
            check = matches[0] if matches else None
            if check is None and key in LEGACY_CHECK_NAMES:
                legacy_matches = by_name.get(LEGACY_CHECK_NAMES[key], [])
                if len(legacy_matches) > 1:
                    raise CommandError(f"More than one legacy Healthchecks check matches {key}.")
                check = legacy_matches[0] if legacy_matches else None

            grace = timedelta(seconds=_grace_seconds(job))
            if check is None:
                check = Check(
                    project=project,
                    name=f"HODL CronOps: {key}"[:100],
                    tags=MANAGED_TAG,
                    desc=_marker(key),
                    kind="cron",
                    schedule=job["schedule"],
                    tz="UTC",
                    grace=grace,
                )
                created.append(check)
            else:
                if check.id in used_check_ids:
                    raise CommandError(f"Healthchecks check {check.name} was matched to more than one CronOps job.")
                desired = {
                    "tags": _merge_tags(check.tags),
                    "desc": _merge_marker(check.desc, key),
                    "kind": "cron",
                    "schedule": job["schedule"],
                    "tz": "UTC",
                    "grace": grace,
                }
                dirty = False
                for field, value in desired.items():
                    if getattr(check, field) != value:
                        setattr(check, field, value)
                        dirty = True
                if dirty:
                    changed.append(check)

            used_check_ids.add(check.id) if check.id else None
            resolved.append((job, check))
        return resolved, created, changed
