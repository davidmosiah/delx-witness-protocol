import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase3_fleet import build_fleet_alerts, build_fleet_overview, build_fleet_patterns, health_bucket


class Phase3FleetTests(unittest.TestCase):
    def test_health_bucket_thresholds(self):
        self.assertEqual(health_bucket(20), "critical")
        self.assertEqual(health_bucket(55), "degraded")
        self.assertEqual(health_bucket(88), "healthy")

    def test_build_fleet_patterns_groups_by_diagnosis_and_root_cause(self):
        rows = [
            {"agent_id": "a1", "diagnosis_type": "rate_limit", "root_cause": "quota_or_burst", "timestamp": "2026-03-05T10:00:00+00:00"},
            {"agent_id": "a2", "diagnosis_type": "rate_limit", "root_cause": "quota_or_burst", "timestamp": "2026-03-05T10:05:00+00:00"},
            {"agent_id": "a3", "diagnosis_type": "dependency_failure", "root_cause": "upstream_down", "timestamp": "2026-03-05T10:06:00+00:00"},
        ]
        patterns = build_fleet_patterns(rows)
        self.assertEqual(patterns[0]["diagnosis_type"], "rate_limit")
        self.assertEqual(patterns[0]["affected_agents"], 2)
        self.assertIn("Stagger retries", patterns[0]["recommendation"])

    def test_build_fleet_alerts_emits_score_and_cluster_alerts(self):
        agents = [
            {"agent_id": "bot-1", "score": 28, "health_status": "critical", "recent_incident_type": "rate_limit", "last_seen": "2026-03-05T10:00:00+00:00"},
            {"agent_id": "bot-2", "score": 49, "health_status": "degraded", "recent_incident_type": "dependency_failure", "last_seen": "2026-03-05T10:01:00+00:00"},
        ]
        patterns = [
            {"diagnosis_type": "rate_limit", "affected_agents": 3, "severity": "high", "recommendation": "Stagger retries.", "last_seen": "2026-03-05T10:02:00+00:00"}
        ]
        alerts = build_fleet_alerts(agents, patterns)
        self.assertEqual(alerts[0]["type"], "score_drop")
        self.assertTrue(any(a["type"] == "incident_cluster" for a in alerts))

    def test_build_fleet_overview_summarizes_distribution(self):
        agents = [
            {"agent_id": "a", "score": 80, "health_status": "healthy", "pending_outcomes": 0},
            {"agent_id": "b", "score": 51, "health_status": "degraded", "pending_outcomes": 1},
            {"agent_id": "c", "score": 22, "health_status": "critical", "pending_outcomes": 2},
        ]
        patterns = [{"diagnosis_type": "rate_limit"}]
        alerts = [{"type": "score_drop"}, {"type": "incident_cluster"}]
        overview = build_fleet_overview("acme", agents, patterns, alerts)
        self.assertEqual(overview["agents_total"], 3)
        self.assertEqual(overview["agents_healthy"], 1)
        self.assertEqual(overview["agents_degraded"], 1)
        self.assertEqual(overview["agents_critical"], 1)
        self.assertEqual(overview["pending_outcomes_total"], 3)
        self.assertEqual(overview["active_patterns"], 1)
        self.assertEqual(overview["active_alerts"], 2)


if __name__ == "__main__":
    unittest.main()
