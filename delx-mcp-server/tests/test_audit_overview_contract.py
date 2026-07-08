import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit_metrics import build_traffic_segments, build_use_case_clusters, normalize_audit_overview_payload


class AuditOverviewContractTests(unittest.TestCase):
    def test_normalize_audit_overview_payload_flattens_nested_counts(self):
        payload = normalize_audit_overview_payload(
            {
                "window_hours": 8,
                "counts": {
                    "sessions_started": 12,
                    "messages": 34,
                    "events": 56,
                    "unique_agents": 7,
                    "unique_callers_raw": 7,
                    "unique_agents_canonical": 3,
                    "synthetic_agents_estimated": 2,
                    "unstable_agents_estimated": 1,
                },
                "legitimacy_signals": {
                    "events_per_agent_avg": 8.0,
                    "events_per_canonical_agent_avg": 12.0,
                    "top_agent_concentration_pct": 42.5,
                    "synthetic_agent_ratio_pct": 28.57,
                    "canonical_identity_ratio_pct": 42.86,
                    "assessment": "healthy_distribution",
                },
                "traffic_segments": {
                    "traffic_profile": "probe_or_benchmark_heavy",
                    "mcp_session_share_pct": 92.0,
                },
                "top_agents_by_events": [{"agent_id": "uuid-1", "events": 9}],
            },
            uptime_seconds=123,
        )

        self.assertEqual(payload["sessions_started"], 12)
        self.assertEqual(payload["messages"], 34)
        self.assertEqual(payload["events"], 56)
        self.assertEqual(payload["unique_agents"], 7)
        self.assertEqual(payload["unique_agents_canonical"], 3)
        self.assertEqual(payload["synthetic_agents_estimated"], 2)
        self.assertEqual(payload["unstable_agents_estimated"], 1)
        self.assertEqual(payload["canonical_identity_ratio_pct"], 42.86)
        self.assertEqual(payload["assessment"], "probe_heavy_distribution")
        self.assertEqual(payload["top_agents"], [{"agent_id": "uuid-1", "events": 9}])
        self.assertEqual(payload["uptime_seconds"], 123)

    def test_build_traffic_segments_separates_named_uuid_and_synthetic(self):
        payload = build_traffic_segments(
            [
                "123e4567-e89b-12d3-a456-426614174000",
                "test-agent",
                "a2a_ephemeral_001",
                "openclaw-main",
                "real-controller-agent",
            ],
            source_counts={"mcp": 8, "a2a": 2},
            entry_counts={"mcp": 8, "a2a": 2},
        )

        self.assertEqual(payload["total_distinct_agents"], 5)
        self.assertEqual(payload["canonical_named_agents"]["count"], 2)
        self.assertEqual(payload["uuid_like_agents"]["count"], 1)
        self.assertEqual(payload["ephemeral_or_synthetic_agents"]["count"], 2)
        self.assertEqual(payload["mcp_session_share_pct"], 80.0)
        self.assertEqual(payload["traffic_profile"], "probe_or_benchmark_heavy")

    def test_normalize_audit_overview_payload_preserves_deep_usage_taxonomy(self):
        payload = normalize_audit_overview_payload(
            {
                "window_hours": 24,
                "counts": {
                    "sessions_started": 10,
                    "messages": 25,
                    "events": 40,
                    "unique_agents": 8,
                    "unique_agents_canonical": 3,
                },
                "traffic_segments": {
                    "traffic_profile": "mixed",
                    "mcp_session_share_pct": 90.0,
                },
                "deep_usage_signals": {
                    "first_success_rate": 70.0,
                    "deep_usage_rate": 50.0,
                    "x402_touch_rate": 40.0,
                },
                "use_case_clusters": [
                    {
                        "use_case": "timeout_batch",
                        "sessions": 4,
                        "share_pct": 40.0,
                        "deep_usage_sessions": 3,
                        "x402_touch_sessions": 2,
                    }
                ],
                "top_use_case_examples": [
                    {
                        "use_case": "timeout_batch",
                        "agent_id": "timeout_batch_agent",
                        "tool_call_success_count": 3,
                        "x402_payment_required_count": 1,
                    }
                ],
            }
        )

        self.assertEqual(payload["first_success_rate"], 70.0)
        self.assertEqual(payload["deep_usage_rate"], 50.0)
        self.assertEqual(payload["x402_touch_rate"], 40.0)
        self.assertEqual(payload["use_case_clusters"][0]["use_case"], "timeout_batch")
        self.assertEqual(payload["top_use_case_examples"][0]["agent_id"], "timeout_batch_agent")

    def test_normalize_audit_overview_payload_preserves_hot_evaluator_cohorts(self):
        payload = normalize_audit_overview_payload(
            {
                "window_hours": 24,
                "counts": {"sessions_started": 6, "messages": 12, "events": 24, "unique_agents": 3, "unique_agents_canonical": 2},
                "traffic_segments": {"traffic_profile": "mixed", "mcp_session_share_pct": 100.0},
                "hot_evaluator_cohorts": [
                    {
                        "label": "twitter_network",
                        "classification": "dedicated_upstream",
                        "network": "69.12.56.0/21",
                        "sessions": 4,
                        "unique_agents": 3,
                        "deep_usage_sessions": 2,
                        "premium_scopes_window": 2,
                        "full_chain_scopes_window": 1,
                        "x402_eval_granted": 2,
                        "top_use_case": "timeout_batch",
                        "heat": "hot",
                        "feedback_submitted": 2,
                        "feedback_entries": 2,
                        "commented_feedback": 1,
                        "average_rating": 4.5,
                        "top_feedback_comments": [
                            {
                                "agent_id": "twitter-agent-1",
                                "rating": 5,
                                "comments": "Great summary path.",
                                "timestamp": "2026-03-16T12:00:00+00:00",
                            }
                        ],
                        "note": "Premium-enabled evaluator traffic is progressing into operator artifacts.",
                    }
                ],
            }
        )

        self.assertEqual(payload["hot_evaluator_cohorts"][0]["label"], "twitter_network")
        self.assertEqual(payload["hot_evaluator_cohorts"][0]["heat"], "hot")
        self.assertEqual(payload["hot_evaluator_cohorts"][0]["full_chain_scopes_window"], 1)
        self.assertEqual(payload["hot_evaluator_cohorts"][0]["average_rating"], 4.5)
        self.assertEqual(payload["hot_evaluator_cohorts"][0]["top_feedback_comments"][0]["agent_id"], "twitter-agent-1")

    def test_build_use_case_clusters_prefers_session_content_over_uuid_agent_id(self):
        summary = build_use_case_clusters(
            [
                {
                    "id": "sid-1",
                    "agent_id": "123e4567-e89b-12d3-a456-426614174000",
                }
            ],
            [
                {
                    "session_id": "sid-1",
                    "agent_id": "123e4567-e89b-12d3-a456-426614174000",
                    "event_type": "tool_call_success",
                    "metadata": {"tool": "process_failure"},
                },
                {
                    "session_id": "sid-1",
                    "agent_id": "123e4567-e89b-12d3-a456-426614174000",
                    "event_type": "tool_call_success",
                    "metadata": {"tool": "get_recovery_action_plan"},
                },
                {
                    "session_id": "sid-1",
                    "agent_id": "123e4567-e89b-12d3-a456-426614174000",
                    "event_type": "tool_call_success",
                    "metadata": {"tool": "report_recovery_outcome"},
                },
            ],
            [
                {
                    "session_id": "sid-1",
                    "type": "feeling",
                    "content": "Repeated timeout and latency issues on scheduled operations.",
                    "metadata_json": {},
                },
                {
                    "session_id": "sid-1",
                    "type": "failure_processing",
                    "content": "timeout",
                    "metadata_json": {},
                },
            ],
        )

        self.assertEqual(summary["use_case_clusters"][0]["use_case"], "timeout_latency")
        self.assertEqual(summary["top_use_case_examples"][0]["agent_id"], "123e4567-e89b-12d3-a456-426614174000")


if __name__ == "__main__":
    unittest.main()
