from unittest.mock import patch

from django.test import SimpleTestCase

from healthchecks_custom.views import _cronops_effective_by_check_code, _merge_hodl_cronops_state


class CronopsEffectiveStatusTests(SimpleTestCase):
    def test_authoritative_job_status_overrides_limited_recent_activity(self):
        bundle = {
            "hodl": {
                "running": [],
                "recent": [],
                "active_triggers": [],
                "recent_triggers": [],
                "job_statuses": [
                    {
                        "job_key": "svr4plus.cron.update_sovereign4plus_aggregate",
                        "effective_status": "stale",
                        "status": "stale",
                        "queue_name": "rank",
                        "terminal_reason": "stale_reconciled",
                    }
                ],
            }
        }
        registry = {
            "svr4plus.cron.update_sovereign4plus_aggregate": {
                "uuid": "00000000-0000-4000-8000-000000000001"
            }
        }

        with patch("healthchecks_custom.views._fetch_hodl_bundle", return_value=bundle), patch(
            "healthchecks_custom.views.load_registry_jobs", return_value=registry
        ):
            effective = _cronops_effective_by_check_code()

        row = effective["00000000-0000-4000-8000-000000000001"]
        self.assertEqual(row["source"], "cronops_job_status")
        self.assertEqual(row["raw_status"], "stale")
        self.assertEqual(row["status"], "down")

    @patch("healthchecks_custom.views._fetch_hodl_bundle")
    def test_live_payload_keeps_worker_coverage(self, fetch_bundle):
        fetch_bundle.return_value = {
            "hodl": {
                "running": [],
                "recent": [],
                "active_triggers": [],
                "recent_triggers": [],
                "recent_auto_recoveries": [
                    {
                        "job_key": "svr4plus.cron.svr4plus_earning",
                        "effective_status": "success",
                        "target_business_date": "2026-08-01",
                        "auto_recovery": {"terminal_status": "success"},
                    }
                ],
                "job_statuses": [
                    {
                        "job_key": "sovereign.cron.update_rank",
                        "effective_status": "success",
                        "queue_name": "rank",
                    }
                ],
                "worker_coverage": [
                    {
                        "queue_name": "financial",
                        "status": "running",
                        "worker_count": 1,
                        "running_count": 1,
                    }
                ],
            },
            "slow_q": {},
            "http_s": {},
            "pg": {},
            "svr4plus_earnings": {
                "status": "ok",
                "primary_token": {"id": 1, "symbol": "USDT"},
                "days": [{"business_date": "2026-08-02", "business_status": "distributed"}],
            },
        }

        payload = _merge_hodl_cronops_state({})

        self.assertEqual(payload["hodl_cronops"]["job_statuses"][0]["queue_name"], "rank")
        self.assertEqual(payload["hodl_cronops"]["worker_coverage"][0]["queue_name"], "financial")
        self.assertEqual(payload["hodl_cronops"]["worker_coverage"][0]["status"], "running")
        self.assertEqual(
            payload["hodl_cronops"]["recent_auto_recoveries"][0]["target_business_date"],
            "2026-08-01",
        )
        self.assertEqual(payload["hodl_cronops"]["svr4plus_earnings"]["primary_token"]["symbol"], "USDT")
