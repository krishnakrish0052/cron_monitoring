import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from monitoring_observer import collector


class RecentRunIndexTests(SimpleTestCase):
    def test_bootstrap_indexes_latest_metadata_without_rescanning_raw_logs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "logs" / "HODL-2025" / "ping-uuid"
            run_dir.mkdir(parents=True)
            older = run_dir / "20260730T000000Z-1-old.json"
            latest = run_dir / "20260731T000000Z-2-new.json"
            older.write_text(json.dumps({"project": "HODL-2025", "ping_uuid": "ping-uuid", "started_at": "2026-07-30T00:00:00Z"}))
            latest.write_text(json.dumps({"project": "HODL-2025", "ping_uuid": "ping-uuid", "started_at": "2026-07-31T00:00:00Z"}))

            original_cache = collector._RECENT_CACHE
            original_cache_at = collector._RECENT_CACHE_AT
            original_bootstrapped = collector._RECENT_INDEX_BOOTSTRAPPED
            try:
                with (
                    patch.object(collector, "LOG_ROOT", root / "logs"),
                    patch.object(collector, "RECENT_RUN_ROOT", root / "runtime" / "recent-runs"),
                ):
                    collector._RECENT_CACHE = []
                    collector._RECENT_CACHE_AT = 0
                    collector._RECENT_INDEX_BOOTSTRAPPED = False
                    runs = collector.recent_runs()

                    indexed = root / "runtime" / "recent-runs" / "HODL-2025" / "ping-uuid.json"
                    self.assertTrue(indexed.exists())
                    self.assertEqual(json.loads(indexed.read_text(encoding="utf-8"))["started_at"], "2026-07-31T00:00:00Z")
                    self.assertEqual(runs[0]["started_at"], "2026-07-31T00:00:00Z")
            finally:
                collector._RECENT_CACHE = original_cache
                collector._RECENT_CACHE_AT = original_cache_at
                collector._RECENT_INDEX_BOOTSTRAPPED = original_bootstrapped
