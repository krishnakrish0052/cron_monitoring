from contextlib import AbstractContextManager
from unittest.mock import patch

from django.test import SimpleTestCase

from monitoring.hc_ping import monitored_cron


class _Capture(AbstractContextManager):
    def __init__(self):
        self.events = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def mark_success(self):
        self.events.append("success")

    def mark_failure(self, error):
        self.events.append(("failure", error))

    def mark_warning(self, error):
        self.events.append(("warning", error))

    def is_warning_only_error(self, error):
        return False


class MonitoredCronTests(SimpleTestCase):
    def test_wrapper_executes_job_and_sends_success_ping(self):
        capture = _Capture()

        @monitored_cron
        def sample_job():
            return {"business_success": True}

        with (
            patch("monitoring.hc_ping.uuid_for", return_value="00000000-0000-4000-8000-000000000001"),
            patch("monitoring.hc_ping.capture_cron_run", return_value=capture),
            patch("monitoring.hc_ping._latest_cronops_run", return_value=None),
            patch("monitoring.hc_ping.ping") as ping,
        ):
            result = sample_job()

        self.assertEqual(result, {"business_success": True})
        self.assertEqual(capture.events, ["success"])
        self.assertEqual(ping.call_args_list[0].args[1], "start")
        self.assertEqual(ping.call_args_list[-1].args[1], "success")

    def test_snapshot_capture_failure_is_not_reported_as_a_green_handoff(self):
        capture = _Capture()

        @monitored_cron
        def sample_job():
            return {
                "cronops_status": "queued_snapshot_unavailable",
                "job_key": "svr4plus.cron.svr4plus_earning",
            }

        with (
            patch("monitoring.hc_ping.uuid_for", return_value="00000000-0000-4000-8000-000000000001"),
            patch("monitoring.hc_ping.capture_cron_run", return_value=capture),
            patch("monitoring.hc_ping.ping") as ping,
        ):
            result = sample_job()

        self.assertEqual(result["cronops_status"], "queued_snapshot_unavailable")
        self.assertEqual(capture.events[0][0], "failure")
        self.assertEqual(ping.call_args_list[-1].args[1], "fail")
