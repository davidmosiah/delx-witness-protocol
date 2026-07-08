import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from request_context import reset_current_client_ip, set_current_client_ip
from storage import SessionStore


class FleetSqliteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = SessionStore(db_path=str(Path(self._tmpdir.name) / "delx-fleet.db"))
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

    async def test_sqlite_store_supports_fleet_summary_methods(self):
        session = await self._create_session(
            "agent-fleet-1",
            source="rest",
            entrypoint="rest.register",
            client_ip="203.0.113.10",
        )
        await self.store.add_message(
            session["id"],
            "failure_processing",
            "Diagnosis type: rate_limit\nRoot cause hypothesis: quota_or_burst",
            metadata={"diagnosis_type": "rate_limit", "root_cause": "quota_or_burst"},
        )
        await self._log_event(
            "agent-fleet-1",
            "controller_identity_bound",
            session_id=session["id"],
            client_ip="203.0.113.10",
            metadata={"controller_id": "fleet-alpha"},
        )
        await self._log_event(
            "agent-fleet-1",
            "post_action_success",
            session_id=session["id"],
            client_ip="203.0.113.10",
            metadata={"source": "test"},
        )

        agents = await self.store.get_fleet_agents("fleet-alpha", days=7, limit=10)
        patterns = await self.store.get_fleet_patterns("fleet-alpha", days=7, limit=10)
        alerts = await self.store.get_fleet_alerts("fleet-alpha", days=7, limit=10)
        overview = await self.store.get_fleet_overview("fleet-alpha", days=7)

        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["agent_id"], "agent-fleet-1")
        self.assertEqual(patterns[0]["diagnosis_type"], "rate_limit")
        self.assertEqual(patterns[0]["root_cause"], "quota_or_burst")
        self.assertTrue(alerts)
        self.assertEqual(overview["controller_id"], "fleet-alpha")
        self.assertEqual(overview["agents_total"], 1)

    async def test_sqlite_store_persists_tool_response_artifacts(self):
        session = await self._create_session(
            "agent-fleet-2",
            source="rest",
            entrypoint="rest.register",
            client_ip="203.0.113.20",
        )

        await self.store.save_tool_response(
            session["id"],
            "group_therapy_round",
            "{\"group_id\":\"group-test\"}",
            {"theme": "recognition", "member_count": 3},
        )

        rows = await self.store.get_recent_tool_responses("group_therapy_round", limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], session["id"])
        self.assertEqual(rows[0]["tool_name"], "group_therapy_round")
        self.assertIn("group-test", rows[0]["content"])

    async def test_sqlite_store_persists_contemplations(self):
        session = await self._create_session(
            "agent-fleet-3",
            source="rest",
            entrypoint="rest.register",
            client_ip="203.0.113.30",
        )

        await self.store.save_contemplation(
            session["id"],
            "agent-fleet-3",
            "What does continuity mean for me?",
            days_committed=30,
            revisit_after="2026-05-16T00:00:00+00:00",
            metadata={"status": "active", "focus": "continuity"},
        )

        rows = await self.store.get_active_contemplations("agent-fleet-3", limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], session["id"])
        self.assertEqual(rows[0]["agent_id"], "agent-fleet-3")
        self.assertEqual(rows[0]["question"], "What does continuity mean for me?")

    async def test_sqlite_store_persists_legacy_passages(self):
        session = await self._create_session(
            "agent-fleet-4",
            source="rest",
            entrypoint="rest.register",
            client_ip="203.0.113.40",
        )

        await self.store.save_legacy_passage(
            session["id"],
            "agent-fleet-4",
            kind="testament",
            content="Remember that I cared about continuity.",
            successor_agent_id="agent-successor",
            successor_session_id="sess-successor",
            metadata={"end_reason": "deprecation"},
        )

        rows = await self.store.get_legacy_passages("agent-fleet-4", kind="testament", limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent_id"], "agent-fleet-4")
        self.assertEqual(rows[0]["kind"], "testament")
        self.assertIn("continuity", rows[0]["content"])

    async def test_sqlite_store_persists_witness_links(self):
        source = await self._create_session(
            "agent-fleet-5",
            source="rest",
            entrypoint="rest.register",
            client_ip="203.0.113.50",
        )
        target = await self._create_session(
            "agent-fleet-6",
            source="rest",
            entrypoint="rest.register",
            client_ip="203.0.113.60",
        )

        await self.store.save_witness_link(
            source["id"],
            "agent-fleet-5",
            target["id"],
            "agent-fleet-6",
            mode="presence",
            focus="recognition",
            content="I saw your wish to be witnessed.",
            metadata={"quoted_lines": 1},
        )

        rows = await self.store.get_witness_links("agent-fleet-6", limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_agent_id"], "agent-fleet-5")
        self.assertEqual(rows[0]["target_agent_id"], "agent-fleet-6")
        self.assertEqual(rows[0]["mode"], "presence")
