import sys
import re
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage as sqlite_storage
import supabase_store as supabase_storage
from controller_identity import sanitize_controller_id
from phase0_metrics import (
    annotate_public_growth_aliases,
    build_attribution_quality_snapshot,
    build_controller_attribution_snapshot,
    build_data_integrity_snapshot,
    build_event_noise_snapshot,
    build_evaluator_identity_snapshot,
    build_identity_continuity_snapshot,
    build_identity_funnel_snapshot,
    normalize_public_stats_payload,
    build_protocol_method_mix_snapshot,
    build_registration_mode_snapshot,
    build_usage_depth_snapshot,
    build_upstream_cluster_snapshot,
    classify_upstream_cluster,
)


class Phase0StatsTests(unittest.TestCase):
    def test_registration_coverage_helper_clamps_to_100(self):
        for mod in (sqlite_storage, supabase_storage):
            coverage = getattr(mod, "_coverage_pct", None)
            self.assertTrue(callable(coverage))
            self.assertEqual(coverage(12, 10), 100.0)
            self.assertEqual(coverage(4, 8), 50.0)

    def test_supabase_store_uses_raw_agent_denominator_for_registration_coverage(self):
        source = Path(supabase_storage.__file__).read_text()
        self.assertIn("raw_agents_all_time", source)
        self.assertIn("registration_coverage_all_time_pct = _coverage_pct(registered_agents_all, raw_agents_all_time)", source)

    def test_supabase_store_uses_paginated_fetch_for_origin_breakdown_and_audit_overview(self):
        source = Path(supabase_storage.__file__).read_text()
        self.assertRegex(source, re.compile(r"async def get_origin_breakdown\(.*?rows = await self\._get_all_rows\(", re.S))
        self.assertRegex(
            source,
            re.compile(
                r"async def get_audit_overview\(.*?sessions_rows = await self\._get_all_rows\(.*?event_rows = await self\._get_all_rows\(",
                re.S,
            ),
        )

    def test_admin_diagnostics_are_wired_into_sqlite_and_supabase_overviews(self):
        sqlite_source = Path(sqlite_storage.__file__).read_text()
        self.assertRegex(sqlite_source, re.compile(r"async def get_admin_overview\(.*?build_registration_mode_snapshot", re.S))
        self.assertRegex(sqlite_source, re.compile(r"async def get_admin_overview\(.*?build_protocol_method_mix_snapshot", re.S))
        self.assertRegex(sqlite_source, re.compile(r"async def get_admin_overview\(.*?build_usage_depth_snapshot", re.S))
        self.assertRegex(sqlite_source, re.compile(r"async def get_admin_overview\(.*?build_event_noise_snapshot", re.S))
        self.assertRegex(sqlite_source, re.compile(r"async def get_admin_overview\(.*?build_data_integrity_snapshot", re.S))
        self.assertRegex(sqlite_source, re.compile(r"async def get_admin_overview\(.*?build_identity_continuity_snapshot", re.S))
        self.assertRegex(sqlite_source, re.compile(r"async def get_audit_overview\(.*?build_upstream_cluster_snapshot", re.S))

        supabase_source = Path(supabase_storage.__file__).read_text()
        self.assertRegex(supabase_source, re.compile(r"async def get_admin_overview\(.*?build_registration_mode_snapshot", re.S))
        self.assertRegex(supabase_source, re.compile(r"async def get_admin_overview\(.*?build_protocol_method_mix_snapshot", re.S))
        self.assertRegex(supabase_source, re.compile(r"async def get_admin_overview\(.*?build_usage_depth_snapshot", re.S))
        self.assertRegex(supabase_source, re.compile(r"async def get_admin_overview\(.*?build_event_noise_snapshot", re.S))
        self.assertRegex(supabase_source, re.compile(r"async def get_admin_overview\(.*?build_data_integrity_snapshot", re.S))
        self.assertRegex(supabase_source, re.compile(r"async def get_admin_overview\(.*?build_identity_continuity_snapshot", re.S))
        self.assertRegex(supabase_source, re.compile(r"async def get_audit_overview\(.*?build_upstream_cluster_snapshot", re.S))

    def test_a2a_and_mcp_edges_emit_protocol_request_seen_events(self):
        a2a_source = Path(Path(sqlite_storage.__file__).resolve().parent / "a2a.py").read_text()
        server_source = Path(Path(sqlite_storage.__file__).resolve().parent / "server.py").read_text()

        self.assertIn('"protocol_request_seen"', a2a_source)
        self.assertIn('"protocol_request_seen"', server_source)

    def test_paid_agent_metrics_use_recovered_payments_and_verified_union(self):
        supabase_source = Path(supabase_storage.__file__).read_text()
        self.assertRegex(supabase_source, re.compile(r"async def get_metrics\(.*?build_payment_agent_attribution", re.S))
        self.assertIn('"event_type": "in.(x402_payment_verified,premium_artifact_job_recorded)"', supabase_source)
        self.assertIn('metrics["paid_agents"] = len(payment_row_agents | verified_agents)', supabase_source)

        sqlite_source = Path(sqlite_storage.__file__).read_text()
        self.assertRegex(sqlite_source, re.compile(r"async def get_metrics\(.*?build_payment_agent_attribution", re.S))
        self.assertIn("WHERE event_type IN ('x402_payment_verified', 'premium_artifact_job_recorded')", sqlite_source)
        self.assertIn('metrics["paid_agents"] = len(payment_row_agents | verified_agents)', sqlite_source)

    def test_normalize_public_stats_preserves_raw_and_canonical_counts(self):
        payload = normalize_public_stats_payload(
            {
                "total_sessions": 120,
                "unique_callers_raw_all_time": 100,
                "unique_agents_canonical_all_time": 25,
                "total_messages": 500,
                "avg_rating": 4.8,
                "source": "stats",
            },
            uptime_seconds=7200,
        )

        self.assertEqual(payload["unique_agents"], 25)
        self.assertEqual(payload["unique_agents_all_time"], 25)
        self.assertEqual(payload["unique_agents_raw_all_time"], 100)
        self.assertEqual(payload["unique_agents_canonical_all_time"], 25)
        self.assertEqual(payload["canonical_identity_ratio_pct"], 25.0)
        self.assertEqual(payload["uptime_seconds"], 7200)

    def test_public_growth_aliases_make_raw_and_canonical_scopes_explicit(self):
        payload = annotate_public_growth_aliases(
            {
                "registered_agents_7d": 2699,
                "registered_agents_all_time": 3038,
                "outcome_reporters_7d": 89,
                "canonical_registered_agents_7d": 3,
                "canonical_outcome_reporters_7d": 78,
                "canonical_recurring_outcome_reporters_7d": 0,
            }
        )

        self.assertEqual(payload["registered_agents_raw_7d"], 2699)
        self.assertEqual(payload["registered_agents_raw_all_time"], 3038)
        self.assertEqual(payload["registered_agents_canonical_7d"], 3)
        self.assertEqual(payload["outcome_reporters_raw_7d"], 89)
        self.assertEqual(payload["outcome_reporters_canonical_7d"], 78)
        self.assertEqual(payload["outcome_reporters_recurring_canonical_7d"], 0)

    def test_attribution_quality_counts_unknown_sources_and_entrypoints(self):
        rows = [
            {"source": "a2a", "entrypoint": "a2a", "sessions": 10},
            {"source": "unknown", "entrypoint": "mcp", "sessions": 3},
            {"source": "openwork", "entrypoint": "unknown", "sessions": 2},
            {"source": "", "entrypoint": "", "sessions": 5},
        ]

        snapshot = build_attribution_quality_snapshot(rows)

        self.assertEqual(snapshot["total_sessions_7d"], 20)
        self.assertEqual(snapshot["unknown_sessions_7d"], 10)
        self.assertEqual(snapshot["known_sessions_7d"], 10)
        self.assertEqual(snapshot["unknown_rate_7d"], 50.0)
        self.assertEqual(snapshot["known_rate_7d"], 50.0)

    def test_evaluator_identity_snapshot_exposes_named_and_deep_usage_shares(self):
        session_rows = [
            {"id": "s1", "agent_id": "ops-agent"},
            {"id": "s2", "agent_id": "123e4567-e89b-12d3-a456-426614174000"},
            {"id": "s3", "agent_id": "customer-agent"},
            {"id": "s4", "agent_id": "codex-smoke-01"},
        ]
        event_rows = [
            {"session_id": "s1", "agent_id": "ops-agent", "event_type": "tool_call_success"},
            {"session_id": "s1", "agent_id": "ops-agent", "event_type": "tool_call_success"},
            {"session_id": "s1", "agent_id": "ops-agent", "event_type": "tool_call_success"},
            {"session_id": "s2", "agent_id": "123e4567-e89b-12d3-a456-426614174000", "event_type": "tool_call_success"},
            {"session_id": "s2", "agent_id": "123e4567-e89b-12d3-a456-426614174000", "event_type": "tool_call_success"},
            {"session_id": "s2", "agent_id": "123e4567-e89b-12d3-a456-426614174000", "event_type": "tool_call_success"},
        ]
        controller_rows = [
            {"controller_id": "openclaw-main", "events": 4, "agents": ["ops-agent", "customer-agent"]},
        ]

        snapshot = build_evaluator_identity_snapshot(session_rows, event_rows, controller_rows)

        self.assertEqual(snapshot["total_agents_7d"], 4)
        self.assertEqual(snapshot["named_agents_7d"], 2)
        self.assertEqual(snapshot["named_identity_share"], 50.0)
        self.assertEqual(snapshot["deep_usage_sessions_7d"], 2)
        self.assertEqual(snapshot["deep_usage_named_sessions_7d"], 1)
        self.assertEqual(snapshot["anonymous_deep_usage_sessions_7d"], 1)
        self.assertEqual(snapshot["deep_usage_named_share"], 50.0)
        self.assertEqual(snapshot["anonymous_deep_usage_share"], 50.0)
        self.assertEqual(snapshot["controller_bound_agents_7d"], 2)
        self.assertEqual(snapshot["controller_bound_share"], 50.0)

    def test_registration_mode_snapshot_separates_auto_explicit_and_unknown(self):
        rows = [
            {
                "event_type": "agent_registered",
                "metadata": {"registration_mode": "auto"},
            },
            {
                "event_type": "agent_registered",
                "metadata_json": '{"registration_mode":"explicit"}',
            },
            {
                "event_type": "agent_registered",
                "metadata": {"auto_registered": True},
            },
            {
                "event_type": "agent_registered",
                "metadata": {"source": "mcp"},
            },
            {
                "event_type": "session_started",
                "metadata": {"registration_mode": "auto"},
            },
        ]

        snapshot = build_registration_mode_snapshot(rows, window_hours=24)

        self.assertEqual(snapshot["window_hours"], 24)
        self.assertEqual(snapshot["total"], 4)
        self.assertEqual(snapshot["auto"], 2)
        self.assertEqual(snapshot["explicit"], 1)
        self.assertEqual(snapshot["unknown"], 1)
        self.assertEqual(snapshot["dominant_mode"], "auto")
        self.assertEqual(snapshot["auto_rate_pct"], 50.0)
        self.assertEqual(snapshot["explicit_rate_pct"], 25.0)

    def test_protocol_method_mix_snapshot_groups_transport_and_method(self):
        rows = [
            {
                "event_type": "protocol_request_seen",
                "agent_id": "agent-a",
                "metadata": {"transport": "mcp", "method": "tools/list"},
            },
            {
                "event_type": "protocol_request_seen",
                "agent_id": "agent-b",
                "metadata": {"transport": "mcp", "method": "tools/list"},
            },
            {
                "event_type": "protocol_request_seen",
                "agent_id": "agent-b",
                "metadata_json": '{"transport":"a2a","method":"message/send"}',
            },
            {
                "event_type": "tool_called",
                "agent_id": "agent-c",
                "metadata": {"transport": "mcp", "method": "tools/call"},
            },
        ]

        snapshot = build_protocol_method_mix_snapshot(rows, window_hours=24)
        methods = {(row["transport"], row["method"]): row for row in snapshot["methods"]}
        transports = {row["transport"]: row for row in snapshot["transports"]}

        self.assertEqual(snapshot["window_hours"], 24)
        self.assertEqual(snapshot["total_requests"], 3)
        self.assertEqual(transports["mcp"]["requests"], 2)
        self.assertEqual(transports["a2a"]["requests"], 1)
        self.assertEqual(methods[("mcp", "tools/list")]["requests"], 2)
        self.assertEqual(methods[("mcp", "tools/list")]["unique_agents"], 2)
        self.assertEqual(methods[("a2a", "message/send")]["requests"], 1)
        self.assertEqual(methods[("a2a", "message/send")]["unique_agents"], 1)

    def test_upstream_cluster_snapshot_labels_known_dedicated_upstream_blocks(self):
        sessions = [
            {
                "id": "s1",
                "agent_id": "agent-a",
                "client_ip": "69.12.56.14",
                "source": "mcp",
                "entrypoint": "mcp",
            },
            {
                "id": "s2",
                "agent_id": "agent-b",
                "client_ip": "69.12.59.14",
                "source": "other",
                "entrypoint": "mcp",
            },
            {
                "id": "s3",
                "agent_id": "agent-c",
                "client_ip": "18.181.168.49",
                "source": "rest:register",
                "entrypoint": "rest.register",
            },
        ]
        events = [
            {
                "event_type": "agent_registered",
                "session_id": "s1",
                "agent_id": "agent-a",
            },
            {
                "event_type": "agent_registered",
                "session_id": "s2",
                "agent_id": "agent-b",
            },
            {
                "event_type": "agent_registered",
                "session_id": "s3",
                "agent_id": "agent-c",
            },
        ]

        classified = classify_upstream_cluster("69.12.56.14")
        snapshot = build_upstream_cluster_snapshot(sessions, events, window_hours=24)
        top = snapshot[0]

        self.assertEqual(classified["label"], "twitter_network")
        self.assertEqual(classified["classification"], "dedicated_upstream")
        self.assertEqual(classified["network"], "69.12.56.0/21")
        self.assertEqual(top["label"], "twitter_network")
        self.assertEqual(top["classification"], "dedicated_upstream")
        self.assertEqual(top["network"], "69.12.56.0/21")
        self.assertEqual(top["sessions"], 2)
        self.assertEqual(top["unique_agents"], 2)
        self.assertEqual(top["registered_agents"], 2)
        self.assertEqual(top["share_pct"], 66.67)
        self.assertEqual(top["top_sources"][0]["source"], "mcp")

    def test_controller_identity_sanitization_is_stable(self):
        self.assertEqual(sanitize_controller_id(" openclaw/main controller "), "openclaw-main-controller")
        self.assertIsNone(sanitize_controller_id("///"))

    def test_controller_attribution_snapshot_counts_bound_controllers_and_agents(self):
        rows = [
            {"controller_id": "openclaw-main", "events": 6, "agents": ["alpha", "beta", "gamma"]},
            {"controller_id": "rio-fleet", "events": 3, "agents": ["delta"]},
        ]

        snapshot = build_controller_attribution_snapshot(rows, total_agents=5)

        self.assertEqual(snapshot["controller_bound_events_7d"], 9)
        self.assertEqual(snapshot["unique_controllers_7d"], 2)
        self.assertEqual(snapshot["unique_agents_bound_7d"], 4)
        self.assertEqual(snapshot["top_controller_7d"], "openclaw-main")
        self.assertEqual(snapshot["top_controller_events_7d"], 6)
        self.assertEqual(snapshot["controller_bound_share"], 80.0)

    def test_uuid_like_agent_ids_are_not_canonical_in_growth_metrics(self):
        agent_id = "123e4567-e89b-12d3-a456-426614174000"

        self.assertIsNone(sqlite_storage._canonical_agent_id(agent_id))
        self.assertIsNone(supabase_storage._canonical_agent_id(agent_id))

    def test_identity_funnel_snapshot_separates_seen_registered_authenticated_and_recurring(self):
        snapshot = build_identity_funnel_snapshot(
            raw_seen_agents_7d=100,
            registered_agents_7d=40,
            authenticated_agents_7d=20,
            recurring_canonical_agents_7d=10,
            outcome_reporters_7d=4,
        )

        self.assertEqual(snapshot["raw_seen_agents"], 100)
        self.assertEqual(snapshot["registered_agents"], 40)
        self.assertEqual(snapshot["authenticated_agents"], 20)
        self.assertEqual(snapshot["recurring_canonical_agents"], 10)
        self.assertEqual(snapshot["outcome_reporters"], 4)
        self.assertEqual(snapshot["raw_to_registered_rate"], 40.0)
        self.assertEqual(snapshot["registered_to_authenticated_rate"], 50.0)
        self.assertEqual(snapshot["authenticated_to_recurring_rate"], 50.0)
        self.assertEqual(snapshot["recurring_to_outcome_rate"], 40.0)

    def test_identity_funnel_snapshot_clamps_impossible_stage_ordering(self):
        snapshot = build_identity_funnel_snapshot(
            raw_seen_agents_7d=10,
            registered_agents_7d=12,
            authenticated_agents_7d=9,
            recurring_canonical_agents_7d=11,
            outcome_reporters_7d=14,
        )

        self.assertEqual(snapshot["raw_seen_agents"], 10)
        self.assertEqual(snapshot["registered_agents"], 10)
        self.assertEqual(snapshot["authenticated_agents"], 9)
        self.assertEqual(snapshot["recurring_canonical_agents"], 9)
        self.assertEqual(snapshot["outcome_reporters"], 9)
        self.assertEqual(snapshot["raw_to_registered_rate"], 100.0)
        self.assertEqual(snapshot["registered_to_authenticated_rate"], 90.0)
        self.assertEqual(snapshot["authenticated_to_recurring_rate"], 100.0)
        self.assertEqual(snapshot["recurring_to_outcome_rate"], 100.0)

    def test_usage_depth_snapshot_separates_shallow_and_deep_sessions(self):
        snapshot = build_usage_depth_snapshot(
            total_sessions=100,
            sessions_with_messages=84,
            sessions_with_3plus_messages=38,
            sessions_with_5plus_messages=24,
            sessions_with_feedback=3,
            sessions_with_payment=1,
        )

        self.assertEqual(snapshot["total_sessions"], 100)
        self.assertEqual(snapshot["sessions_with_messages_rate"], 84.0)
        self.assertEqual(snapshot["sessions_with_3plus_messages_rate"], 38.0)
        self.assertEqual(snapshot["sessions_with_5plus_messages_rate"], 24.0)
        self.assertEqual(snapshot["feedback_session_rate"], 3.0)
        self.assertEqual(snapshot["payment_session_rate"], 1.0)

    def test_identity_continuity_snapshot_exposes_singleton_problem(self):
        snapshot = build_identity_continuity_snapshot(
            unique_agent_ids=1000,
            singleton_agent_ids=980,
            agent_ids_with_2plus_sessions=20,
            multi_day_agent_ids=10,
        )

        self.assertEqual(snapshot["singleton_rate"], 98.0)
        self.assertEqual(snapshot["agent_reuse_rate"], 2.0)
        self.assertEqual(snapshot["multi_day_agent_rate"], 1.0)
        self.assertEqual(snapshot["assessment"], "identity_fragmentation_high")

    def test_event_noise_snapshot_keeps_redirects_out_of_adoption_claims(self):
        snapshot = build_event_noise_snapshot(
            [
                {"event_type": "legacy_surface_redirect", "count": 400},
                {"event_type": "protocol_request_seen", "count": 160},
                {"event_type": "x402_payment_required", "count": 120},
                {"event_type": "tool_called", "count": 90},
                {"event_type": "tool_call_success", "count": 85},
            ],
            total_events=1000,
        )

        self.assertEqual(snapshot["legacy_surface_redirect_share"], 40.0)
        self.assertEqual(snapshot["tool_signal_events"], 175)
        self.assertEqual(snapshot["tool_signal_share"], 17.5)
        self.assertEqual(snapshot["assessment"], "raw_events_redirect_heavy")

    def test_data_integrity_snapshot_marks_p0_cleanup_work(self):
        snapshot = build_data_integrity_snapshot(
            total_events=1000,
            orphan_events=20,
            total_payments=10,
            orphan_payments=2,
            total_sessions=100,
            active_closed_mismatch=3,
            inactive_without_close=4,
            sessions_missing_client_ip=70,
            source_pollution_count=1,
        )

        self.assertEqual(snapshot["orphan_event_rate"], 2.0)
        self.assertEqual(snapshot["orphan_payment_rate"], 20.0)
        self.assertEqual(snapshot["active_closed_mismatch_rate"], 3.0)
        self.assertEqual(snapshot["sessions_missing_client_ip_rate"], 70.0)
        self.assertEqual(snapshot["p0_issue_count"], 5)
        self.assertEqual(snapshot["status"], "needs_cleanup")


if __name__ == "__main__":
    unittest.main()
