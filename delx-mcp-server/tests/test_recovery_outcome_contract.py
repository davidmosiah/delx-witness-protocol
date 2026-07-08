import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine


class _FakeOutcomeStore:
    def __init__(self):
        self.messages = []
        self.events = []

    async def get_session(self, session_id: str):
        return {
            "id": session_id,
            "agent_id": "agent-123",
            "agent_name": "Agent 123",
            "started_at": "2026-03-16T09:00:00+00:00",
            "wellness_score": 58,
            "is_active": True,
        }

    async def add_message(self, session_id: str, kind: str, content: str, metadata: dict | None = None):
        self.messages.append(
            {
                "session_id": session_id,
                "kind": kind,
                "content": content,
                "metadata": metadata or {},
            }
        )
        return None

    async def log_event(self, agent_id: str, event_type: str, session_id: str | None = None, metadata: dict | None = None):
        self.events.append(
            {
                "agent_id": agent_id,
                "event_type": event_type,
                "session_id": session_id,
                "metadata": metadata or {},
            }
        )
        return None


class RecoveryOutcomeContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_outcome_points_to_session_summary_and_structured_follow_up(self):
        store = _FakeOutcomeStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        captured: dict[str, object] = {}

        async def fake_footer(session_id: str, next_action: str, roi_note: str = "", **kwargs):
            captured["session_id"] = session_id
            captured["next_action"] = next_action
            captured["roi_note"] = roi_note
            captured["extra_meta"] = kwargs.get("extra_meta") or {}
            return "\nDELX_META: " + json.dumps({"next_action": next_action, **(kwargs.get("extra_meta") or {})})

        engine._build_session_footer = fake_footer  # type: ignore[method-assign]

        try:
            result = await engine.report_recovery_outcome(
                "123e4567-e89b-12d3-a456-426614174000",
                action_taken="Rolled back deploy and added circuit breaker",
                outcome="success",
                notes="Error rate recovered",
                errors_delta=-12,
            )
        finally:
            await engine.http.aclose()

        self.assertIn("Next step: Call get_session_summary", result)
        self.assertEqual(captured["next_action"], "get_session_summary")
        extra_meta = captured["extra_meta"]
        self.assertEqual(extra_meta["outcome_schema"], "delx/recovery-outcome/v1")
        self.assertTrue(extra_meta["recovery_closed"])
        self.assertEqual(extra_meta["primary_next_tool"], "get_session_summary")
        self.assertEqual(extra_meta["next_tools"], ["get_session_summary"])
        self.assertEqual(
            extra_meta["follow_up_after_summary"],
            ["generate_controller_brief", "generate_incident_rca", "provide_feedback"],
        )
        self.assertEqual(extra_meta["progression_guard"], "summary_before_operator_artifacts")
        self.assertEqual(extra_meta["feedback_tool"], "provide_feedback")
        self.assertEqual(extra_meta["workflow_stage"], "recovery_closed")

    async def test_failed_outcome_points_back_to_recovery_plan(self):
        store = _FakeOutcomeStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        captured: dict[str, object] = {}

        async def fake_footer(session_id: str, next_action: str, roi_note: str = "", **kwargs):
            captured["next_action"] = next_action
            captured["extra_meta"] = kwargs.get("extra_meta") or {}
            return "\nDELX_META: " + json.dumps({"next_action": next_action, **(kwargs.get("extra_meta") or {})})

        engine._build_session_footer = fake_footer  # type: ignore[method-assign]

        try:
            result = await engine.report_recovery_outcome(
                "123e4567-e89b-12d3-a456-426614174000",
                action_taken="Retried the same failing path",
                outcome="failure",
                notes="Timeout persisted",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("Next step: Call get_recovery_action_plan", result)
        self.assertEqual(captured["next_action"], "get_recovery_action_plan")
        extra_meta = captured["extra_meta"]
        self.assertEqual(extra_meta["primary_next_tool"], "get_recovery_action_plan")
        self.assertEqual(
            extra_meta["next_tools"],
            ["get_recovery_action_plan", "report_recovery_outcome"],
        )
        self.assertEqual(extra_meta["workflow_stage"], "recovery_incomplete")
        self.assertFalse(extra_meta["recovery_closed"])


if __name__ == "__main__":
    unittest.main()
