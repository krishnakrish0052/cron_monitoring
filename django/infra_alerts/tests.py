from unittest import TestCase
from unittest.mock import patch

from infra_alerts import service


def healthy_payload():
    return {
        "stale_count_24h": 0,
        "queue_summary": {"missed_sla_24h": 0},
        "spool_summary": {"pending": 0, "failed": 0},
        "worker_coverage": [
            {"queue_name": queue_name, "status": "running"}
            for queue_name in service.HODL_CRONOPS_EXPECTED_WORKER_QUEUES
        ],
    }


class CronOpsAlertRuleTests(TestCase):
    def collect(self, payload, prom_side_effect=None):
        with patch.object(service, "fetch_hodl_live", return_value=payload), patch.object(
            service, "read_observer_stale_count", return_value=0
        ), patch.object(service, "monitoring_down_targets", return_value=[]):
            if prom_side_effect is None:
                with patch.object(service, "prom_scalar", return_value=0.0):
                    return {rule.key: rule for rule in service.collect_rules()}
            with patch.object(service, "prom_scalar", side_effect=prom_side_effect):
                return {rule.key: rule for rule in service.collect_rules()}

    def test_healthy_live_payload_keeps_cronops_rules_clear(self):
        rules = self.collect(healthy_payload())

        self.assertFalse(rules["cronops_live_unavailable"].active)
        self.assertFalse(rules["cronops_worker_lane_missing"].active)
        self.assertFalse(rules["cronops_missed_sla"].active)
        self.assertFalse(rules["cronops_spool_backlog"].active)

    def test_missing_financial_lane_is_critical(self):
        payload = healthy_payload()
        payload["worker_coverage"] = [
            row for row in payload["worker_coverage"] if row["queue_name"] != "financial"
        ]

        rules = self.collect(payload)

        self.assertTrue(rules["cronops_worker_lane_missing"].active)
        self.assertEqual(rules["cronops_worker_lane_missing"].description, "financial")

    def test_unavailable_live_endpoint_is_critical_without_marking_lanes_missing(self):
        rules = self.collect(None)

        self.assertTrue(rules["cronops_live_unavailable"].active)
        self.assertNotIn("cronops_worker_lane_missing", rules)

    def test_cronops_lane_alert_survives_a_prometheus_outage(self):
        payload = healthy_payload()
        payload["worker_coverage"][0]["status"] = "stale"

        rules = self.collect(payload, prom_side_effect=RuntimeError("prometheus unavailable"))

        self.assertTrue(rules["cronops_worker_lane_missing"].active)
        self.assertTrue(rules["prometheus_unavailable"].active)
