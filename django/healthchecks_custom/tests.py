from unittest.mock import patch

from django.test import SimpleTestCase

from healthchecks_custom.views import _cronops_effective_by_check_code


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
