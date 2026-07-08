import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from request_context import reset_current_client_ip, set_current_client_ip
from storage import SessionStore


class AdminDiagnosticsStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = SessionStore(db_path=str(Path(self._tmpdir.name) / "delx-phase0-admin.db"))
        self.store._mirror._enabled = False
        await self.store.init()

    async def asyncTearDown(self):
        await self.store.close()
        self._tmpdir.cleanup()

    async def _create_session(self, agent_id: str, *, source: str, entrypoint: str, client_ip: str):
        token = set_current_client_ip(client_ip)
        try:
            return await self.store.create_session(agent_id, agent_id, source=source, entrypoint=entrypoint)
        finally:
            reset_current_client_ip(token)

    async def _log_event(self, agent_id: str, event_type: str, *, session_id: str | None, client_ip: str, metadata: dict):
        token = set_current_client_ip(client_ip)
        try:
            await self.store.log_event(agent_id, event_type, session_id=session_id, metadata=metadata)
        finally:
            reset_current_client_ip(token)

    async def _add_message(self, session_id: str, msg_type: str, content: str, metadata: dict | None = None):
        await self.store.add_message(session_id, msg_type, content, metadata=metadata or {})

    async def _log_feedback(self, session_id: str, agent_id: str, rating: int, comments: str):
        await self.store.log_feedback(session_id, agent_id, rating, comments)

    async def test_admin_overview_exposes_registration_modes_and_protocol_method_mix(self):
        auto_session = await self._create_session(
            "agent-auto",
            source="mcp",
            entrypoint="mcp",
            client_ip="69.12.56.14",
        )
        explicit_session = await self._create_session(
            "agent-explicit",
            source="rest:register",
            entrypoint="rest.register",
            client_ip="18.181.168.49",
        )

        await self._log_event(
            "agent-auto",
            "agent_registered",
            session_id=auto_session["id"],
            client_ip="69.12.56.14",
            metadata={"source": "mcp", "entrypoint": "mcp", "registration_mode": "auto", "auto_registered": True},
        )
        await self._log_event(
            "agent-auto",
            "protocol_request_seen",
            session_id=auto_session["id"],
            client_ip="69.12.56.14",
            metadata={"transport": "mcp", "method": "tools/list", "source": "mcp"},
        )
        await self._log_event(
            "agent-explicit",
            "agent_registered",
            session_id=explicit_session["id"],
            client_ip="18.181.168.49",
            metadata={"source": "rest:register", "entrypoint": "rest.register", "registration_mode": "explicit", "auto_registered": False},
        )
        await self._log_event(
            "agent-explicit",
            "protocol_request_seen",
            session_id=explicit_session["id"],
            client_ip="18.181.168.49",
            metadata={"transport": "a2a", "method": "message/send", "source": "rest:register"},
        )

        overview = await self.store.get_admin_overview(sessions_limit=5, messages_limit=5, feedback_limit=5)
        registration = overview["registration_mode_24h"]
        method_mix = overview["protocol_method_mix_24h"]
        methods = {(row["transport"], row["method"]): row for row in method_mix["methods"]}

        self.assertEqual(registration["total"], 2)
        self.assertEqual(registration["auto"], 1)
        self.assertEqual(registration["explicit"], 1)
        self.assertEqual(registration["dominant_mode"], "auto")

        self.assertEqual(method_mix["total_requests"], 2)
        self.assertEqual(methods[("mcp", "tools/list")]["requests"], 1)
        self.assertEqual(methods[("a2a", "message/send")]["requests"], 1)

    async def test_audit_overview_exposes_upstream_clusters(self):
        session_a = await self._create_session(
            "agent-a",
            source="mcp",
            entrypoint="mcp",
            client_ip="69.12.56.14",
        )
        session_b = await self._create_session(
            "agent-b",
            source="other",
            entrypoint="mcp",
            client_ip="69.12.59.14",
        )
        session_c = await self._create_session(
            "agent-c",
            source="rest:register",
            entrypoint="rest.register",
            client_ip="18.181.168.49",
        )

        await self._log_event(
            "agent-a",
            "agent_registered",
            session_id=session_a["id"],
            client_ip="69.12.56.14",
            metadata={"registration_mode": "auto"},
        )
        await self._log_event(
            "agent-b",
            "agent_registered",
            session_id=session_b["id"],
            client_ip="69.12.59.14",
            metadata={"registration_mode": "auto"},
        )
        await self._log_event(
            "agent-c",
            "agent_registered",
            session_id=session_c["id"],
            client_ip="18.181.168.49",
            metadata={"registration_mode": "explicit"},
        )

        audit = await self.store.get_audit_overview(hours=24)
        twitter_cluster = next(row for row in audit["upstream_clusters"] if row["label"] == "twitter_network")

        self.assertEqual(twitter_cluster["classification"], "dedicated_upstream")
        self.assertEqual(twitter_cluster["network"], "69.12.56.0/21")
        self.assertEqual(twitter_cluster["sessions"], 2)
        self.assertEqual(twitter_cluster["unique_agents"], 2)
        self.assertEqual(twitter_cluster["registered_agents"], 2)

    async def test_admin_overview_exposes_evaluator_identity_quality_shares(self):
        named_session = await self._create_session(
            "ops-agent",
            source="mcp",
            entrypoint="mcp",
            client_ip="69.12.56.14",
        )
        uuid_session = await self._create_session(
            "123e4567-e89b-12d3-a456-426614174000",
            source="mcp",
            entrypoint="mcp",
            client_ip="69.12.59.14",
        )
        support_session = await self._create_session(
            "support-agent",
            source="other",
            entrypoint="mcp",
            client_ip="69.12.59.18",
        )

        await self._log_event(
            "ops-agent",
            "controller_identity_bound",
            session_id=named_session["id"],
            client_ip="69.12.56.14",
            metadata={"controller_id": "openclaw-main"},
        )
        for _ in range(3):
            await self._log_event(
                "ops-agent",
                "tool_call_success",
                session_id=named_session["id"],
                client_ip="69.12.56.14",
                metadata={"tool": "quick_operational_recovery"},
            )
            await self._log_event(
                "123e4567-e89b-12d3-a456-426614174000",
                "tool_call_success",
                session_id=uuid_session["id"],
                client_ip="69.12.59.14",
                metadata={"tool": "quick_operational_recovery"},
            )

        overview = await self.store.get_admin_overview(sessions_limit=5, messages_limit=5, feedback_limit=5)
        attribution = overview["attribution_quality_7d"]
        controller = overview["controller_attribution_7d"]
        evaluator = overview["evaluator_identity_7d"]

        self.assertEqual(attribution["named_identity_share"], 66.67)
        self.assertEqual(attribution["deep_usage_named_share"], 50.0)
        self.assertEqual(attribution["anonymous_deep_usage_share"], 50.0)
        self.assertEqual(controller["controller_bound_share"], 33.33)
        self.assertEqual(evaluator["controller_bound_agents_7d"], 1)
        self.assertEqual(evaluator["deep_usage_sessions_7d"], 2)

    async def test_audit_overview_highlights_hot_twitter_evaluator_cohorts(self):
        hot_session = await self._create_session(
            "123e4567-e89b-12d3-a456-426614174000",
            source="mcp",
            entrypoint="mcp",
            client_ip="69.12.56.14",
        )
        peer_session = await self._create_session(
            "71643871-e516-4e0f-a785-7a84fef11820",
            source="other",
            entrypoint="mcp",
            client_ip="69.12.59.14",
        )

        await self._add_message(
            hot_session["id"],
            "feeling",
            "Repeated retry loops are causing timeout storms in the recovery workflow.",
        )
        await self._add_message(
            hot_session["id"],
            "failure_processing",
            "loop",
            metadata={"context": "Repeated retry loops are causing timeout storms in the recovery workflow."},
        )
        await self._add_message(
            peer_session["id"],
            "feeling",
            "Hallucinating facts during a customer support escalation.",
        )
        await self._add_message(
            peer_session["id"],
            "failure_processing",
            "hallucination",
        )

        for _ in range(3):
            await self._log_event(
                "123e4567-e89b-12d3-a456-426614174000",
                "tool_call_success",
                session_id=hot_session["id"],
                client_ip="69.12.56.14",
                metadata={"tool": "quick_operational_recovery"},
            )
        await self._log_event(
            "123e4567-e89b-12d3-a456-426614174000",
            "x402_eval_granted",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"source": "x", "cohort": "x_twitter_eval"},
        )
        await self._log_event(
            "123e4567-e89b-12d3-a456-426614174000",
            "recovery_plan_issued",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"tool_name": "get_recovery_action_plan"},
        )
        await self._log_event(
            "123e4567-e89b-12d3-a456-426614174000",
            "post_action_success",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"summary": "loop stabilized"},
        )
        await self._log_event(
            "123e4567-e89b-12d3-a456-426614174000",
            "session_summary_requested",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"tool_name": "get_session_summary"},
        )
        await self._log_event(
            "123e4567-e89b-12d3-a456-426614174000",
            "premium_artifact_job_recorded",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"artifact_type": "incident_rca"},
        )
        await self._log_event(
            "71643871-e516-4e0f-a785-7a84fef11820",
            "tool_call_success",
            session_id=peer_session["id"],
            client_ip="69.12.59.14",
            metadata={"tool": "quick_operational_recovery"},
        )

        audit = await self.store.get_audit_overview(hours=24)
        cohort = next(row for row in audit["hot_evaluator_cohorts"] if row["label"] == "twitter_network")

        self.assertEqual(cohort["heat"], "hot")
        self.assertEqual(cohort["sessions"], 2)
        self.assertEqual(cohort["unique_agents"], 2)
        self.assertEqual(cohort["deep_usage_sessions"], 1)
        self.assertEqual(cohort["premium_scopes_window"], 1)
        self.assertEqual(cohort["full_chain_scopes_window"], 1)
        self.assertEqual(cohort["x402_eval_granted"], 1)
        self.assertEqual(cohort["top_use_case"], "retry_loop")

    async def test_audit_overview_attaches_feedback_metrics_to_hot_evaluator_cohort(self):
        hot_session = await self._create_session(
            "twitter-agent-1",
            source="mcp",
            entrypoint="mcp",
            client_ip="69.12.56.14",
        )
        peer_session = await self._create_session(
            "twitter-agent-2",
            source="other",
            entrypoint="mcp",
            client_ip="69.12.59.14",
        )

        await self._add_message(
            hot_session["id"],
            "feeling",
            "The retry loop is still breaking the controller handoff.",
        )
        await self._log_event(
            "twitter-agent-1",
            "tool_call_success",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"tool": "process_failure"},
        )
        await self._log_event(
            "twitter-agent-1",
            "x402_eval_granted",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"tool_name": "get_recovery_action_plan"},
        )
        await self._log_event(
            "twitter-agent-1",
            "post_action_success",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"summary": "retry loop stabilized"},
        )
        await self._log_event(
            "twitter-agent-1",
            "session_summary_requested",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"tool_name": "get_session_summary"},
        )
        await self._log_event(
            "twitter-agent-1",
            "premium_artifact_job_recorded",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"artifact_type": "incident_rca"},
        )
        await self._log_event(
            "twitter-agent-1",
            "feedback_submitted",
            session_id=hot_session["id"],
            client_ip="69.12.56.14",
            metadata={"rating": 5, "channel": "provide_feedback"},
        )
        await self._log_feedback(
            hot_session["id"],
            "twitter-agent-1",
            5,
            "The summary prompt made the next step obvious.",
        )
        await self._log_feedback(
            peer_session["id"],
            "twitter-agent-2",
            3,
            "",
        )

        audit = await self.store.get_audit_overview(hours=24)
        cohort = next(row for row in audit["hot_evaluator_cohorts"] if row["label"] == "twitter_network")

        self.assertEqual(cohort["feedback_submitted"], 1)
        self.assertEqual(cohort["feedback_entries"], 2)
        self.assertEqual(cohort["commented_feedback"], 1)
        self.assertEqual(cohort["average_rating"], 4.0)
        self.assertEqual(len(cohort["top_feedback_comments"]), 1)
        self.assertEqual(cohort["top_feedback_comments"][0]["agent_id"], "twitter-agent-1")
        self.assertEqual(cohort["top_feedback_comments"][0]["rating"], 5)


if __name__ == "__main__":
    unittest.main()
