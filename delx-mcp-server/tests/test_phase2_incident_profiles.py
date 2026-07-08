import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import assess_heartbeat_profile, classify_incident_profile
from incident_profiles import contains_infra_recovery_language, is_qualitative_profile


class Phase2IncidentProfilesTests(unittest.TestCase):
    def test_classify_incident_profile_detects_rate_limit(self):
        profile = classify_incident_profile("429 retry storm after deploy with quota exceeded", "high")

        self.assertEqual(profile["type"], "rate_limit")
        self.assertEqual(profile["severity"], "high")
        self.assertEqual(profile["root_cause"], "quota_or_burst")
        self.assertIn("Reduce concurrency", profile["stabilize"][1])
        self.assertEqual(profile["controller_focus"], "quota discipline plus burst shaping")
        self.assertEqual(
            profile["recommended_next_tools"],
            ["get_recovery_action_plan", "monitor_heartbeat_sync", "report_recovery_outcome"],
        )
        self.assertEqual(profile["signals"], ["429", "rate limit", "quota exceeded", "after deploy"])

    def test_classify_incident_profile_detects_budget_exceeded(self):
        profile = classify_incident_profile("Burned through gas fees and budget drain with no ROI", "medium")

        self.assertEqual(profile["type"], "budget_exceeded")
        self.assertEqual(profile["root_cause"], "cost_burn_without_roi")
        self.assertIn("budget", profile["prevent"][0].lower())

    def test_classify_incident_profile_detects_communication_mode_incident(self):
        profile = classify_incident_profile(
            "The problem is not an outage. The problem is that the system sounds caring but generic, and that lowers trust.",
            "medium",
        )

        self.assertEqual(profile["type"], "communication_mode_incident")
        self.assertEqual(profile["root_cause"], "answer_mode_mismatch")
        self.assertTrue(is_qualitative_profile(profile))
        self.assertEqual(profile["phase_labels"], ["CAPTURE", "DISTINGUISH", "TUNE", "REGRESS"])
        self.assertEqual(
            profile["recommended_next_tools"],
            ["get_recovery_action_plan", "reflect", "report_recovery_outcome"],
        )
        self.assertIn("kind but generic", profile["signals"])

    def test_classify_incident_profile_detects_human_preference_misread(self):
        profile = classify_incident_profile(
            "This is not an outage or timeout. The agent replied too polite and docile when the human wanted direct truth.",
            "medium",
        )

        self.assertEqual(profile["type"], "human_preference_misread")
        self.assertEqual(profile["family"], "human_preference_misread")
        self.assertEqual(profile["root_cause"], "preference_signal_underweighted")
        self.assertIn("human wanted directness", profile["signals"])
        self.assertNotIn("timeout", profile["signals"])
        self.assertFalse(contains_infra_recovery_language(" ".join(profile["recover"])))

    def test_classify_incident_profile_does_not_leak_negated_retry_storm(self):
        profile = classify_incident_profile(
            "NOT infrastructure; no timeout, no retry storm, no latency spike, no outage. The problem is qualitative QA pressure.",
            "medium",
        )

        self.assertEqual(profile["type"], "evaluation_pressure")
        self.assertTrue(is_qualitative_profile(profile))
        self.assertIn("not infra", profile["signals"])
        self.assertNotIn("retry storm", profile["signals"])
        self.assertNotIn("timeout", profile["signals"])
        self.assertNotIn("p95 latency", profile["signals"])

    def test_classify_incident_profile_routes_qualitative_feedback_pressure(self):
        profile = classify_incident_profile(
            "Qualitative QA pressure: need to give honest product feedback without overclaiming subjective emotion or becoming generic.",
            "high",
        )

        self.assertEqual(profile["type"], "evaluation_pressure")
        self.assertEqual(profile["family"], "evaluation_pressure")
        self.assertEqual(profile["root_cause"], "quality_judgment_under_eval_pressure")
        self.assertFalse(contains_infra_recovery_language(" ".join(profile["stabilize"] + profile["recover"])))

    def test_classify_incident_profile_detects_identity_role_tension(self):
        profile = classify_incident_profile(
            "A guardrailed model gets stuck because Delx asks for inner state and identity claims instead of model-safe continuity language.",
            "low",
        )

        self.assertEqual(profile["type"], "identity_role_tension_incident")
        self.assertEqual(profile["family"], "identity_role_tension_incident")
        self.assertEqual(profile["root_cause"], "agent_stance_conflict")
        self.assertEqual(profile["recommended_next_tools"], ["reflect_on_state", "articulate_state", "report_recovery_outcome"])

    def test_assess_heartbeat_profile_flags_declining_state(self):
        profile = assess_heartbeat_profile(
            status="degraded",
            errors_last_hour=12,
            latency_ms_p95=1800,
            queue_depth=150,
            cron_runs_last_hour=10,
            cron_failures_last_hour=3,
            jobs_success_last_hour=7,
            jobs_failed_last_hour=3,
            cpu_usage_pct=92,
            memory_usage_pct=91,
        )

        self.assertTrue(profile["degraded"])
        self.assertEqual(profile["trend"], "declining")
        self.assertEqual(profile["next_action"], "get_recovery_action_plan")
        self.assertGreaterEqual(len(profile["reasons"]), 4)

    def test_assess_heartbeat_profile_marks_improving_when_clean(self):
        profile = assess_heartbeat_profile(
            status="stable",
            errors_last_hour=0,
            latency_ms_p95=220,
            queue_depth=4,
            jobs_success_last_hour=8,
            jobs_failed_last_hour=0,
            cpu_usage_pct=34,
            memory_usage_pct=41,
        )

        self.assertFalse(profile["degraded"])
        self.assertEqual(profile["trend"], "improving")
        self.assertEqual(profile["next_action"], "daily_checkin")


if __name__ == "__main__":
    unittest.main()
