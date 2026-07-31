import json
from io import StringIO
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from monitoring_common import cron_logs
from monitoring_common.cron_logs import _Tee, _ensure_private_dir, _sanitize_text, _sanitize_value, _write_json


class CronLogRedactionTests(SimpleTestCase):
    def test_redacts_query_values_and_url_userinfo(self):
        value = _sanitize_text(
            "postgres://db-user:private-password@example.test:5432/akita?sslmode=require "
            "https://api.example.test/?apikey=private-key"
        )

        self.assertNotIn("db-user", value)
        self.assertNotIn("private-password", value)
        self.assertNotIn("private-key", value)
        self.assertIn("example.test:5432", value)

    def test_log_tee_keeps_sensitive_output_out_of_capture(self):
        original = StringIO()
        captured = StringIO()
        tee = _Tee(original, captured, "stdout")
        tee.write("authorization=private-value\n")

        self.assertIn("private-value", original.getvalue())
        self.assertNotIn("private-value", captured.getvalue())

    def test_metadata_redacts_sensitive_keys_recursively(self):
        payload = _sanitize_value({"api_key": "private-value", "nested": {"token": "private-token"}})

        self.assertEqual(payload["api_key"], "***")
        self.assertEqual(payload["nested"]["token"], "***")

    def test_new_artifacts_are_owner_only(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "private" / "nested"
            artifact = directory / "state.json"
            _ensure_private_dir(directory)
            _write_json(artifact, {"api_key": "private-value"})

            self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(artifact).st_mode), 0o600)


class CronLogRecentIndexTests(SimpleTestCase):
    def test_capture_publishes_a_single_latest_run_index(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(cron_logs, "LOG_ROOT", root / "logs"),
                patch.object(cron_logs, "RUNTIME_ROOT", root / "runtime"),
                patch.object(cron_logs, "RECENT_RUN_ROOT", root / "runtime" / "recent-runs"),
            ):
                capture = cron_logs.CronRunCapture(
                    "HODL-2025",
                    "sample.cron.job",
                    "00000000-0000-4000-8000-000000000001",
                )
                capture._publish_recent_run()

                payload = json.loads(capture.recent_run_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["function"], "sample.cron.job")
            self.assertEqual(payload["status"], "running")
