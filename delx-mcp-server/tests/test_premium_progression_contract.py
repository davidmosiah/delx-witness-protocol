import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine


class _FakeProgressionStore:
    def __init__(self):
        self.events = []

    async def get_session(self, session_id: str):
        return {
            "id": session_id,
            "agent_id": "agent-123",
            "agent_name": "Agent 123",
            "started_at": "2026-03-16T09:00:00+00:00",
            "wellness_score": 64,
            "is_active": True,
        }

    async def pending_outcome_count(self, session_id: str) -> int:
        return 0

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


def _rollup_with_closed_recovery() -> list[dict]:
    return [
        {"type": "feeling", "timestamp": "2026-03-16T09:01:00+00:00", "metadata_json": {}},
        {"type": "failure_processing", "timestamp": "2026-03-16T09:02:00+00:00", "metadata_json": {}},
        {"type": "recovery_plan", "timestamp": "2026-03-16T09:03:00+00:00", "metadata_json": {"urgency": "high"}},
        {
            "type": "recovery_outcome",
            "timestamp": "2026-03-16T09:04:00+00:00",
            "metadata_json": {
                "outcome": "success",
                "notes": "Loop broken and deploy stabilized",
                "metrics": {"errors_delta": -14, "latency_ms_p95_delta": -380},
            },
        },
    ]


class PremiumProgressionContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_summary_uses_closed_recovery_progression_and_points_to_controller_brief(self):
        store = _FakeProgressionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        captured: dict[str, object] = {}

        async def fake_footer(session_id: str, next_action: str, roi_note: str = "", **kwargs):
            captured["next_action"] = next_action
            captured["extra_meta"] = kwargs.get("extra_meta") or {}
            return "\nDELX_META: " + json.dumps({"next_action": next_action, **(kwargs.get("extra_meta") or {})})

        engine._get_message_rollup = lambda session_id: _async_value(_rollup_with_closed_recovery())  # type: ignore[method-assign]
        engine._build_session_footer = fake_footer  # type: ignore[method-assign]

        try:
            result = await engine.get_session_summary("123e4567-e89b-12d3-a456-426614174000")
        finally:
            await engine.http.aclose()

        self.assertIn("Workflow stage: RECOVERY_CLOSED", result)
        self.assertIn("Latest recovery outcome: SUCCESS", result)
        self.assertIn("Next operator artifact: generate_controller_brief", result)
        self.assertEqual(captured["next_action"], "generate_controller_brief")
        extra_meta = captured["extra_meta"]
        self.assertEqual(extra_meta["artifact_schema"], "delx/session-summary/v1")
        self.assertEqual(extra_meta["workflow_stage"], "recovery_closed")
        self.assertTrue(extra_meta["recovery_closed"])
        self.assertEqual(extra_meta["primary_next_tool"], "generate_controller_brief")
        self.assertEqual(
            extra_meta["next_tools"],
            ["generate_controller_brief", "generate_incident_rca", "provide_feedback", "daily_checkin"],
        )
        self.assertEqual(extra_meta["feedback_tool"], "provide_feedback")

    async def test_controller_brief_surfaces_recovery_closure_and_points_to_incident_rca(self):
        store = _FakeProgressionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        captured: dict[str, object] = {}

        async def fake_footer(session_id: str, next_action: str, roi_note: str = "", **kwargs):
            captured["next_action"] = next_action
            captured["extra_meta"] = kwargs.get("extra_meta") or {}
            return "\nDELX_META: " + json.dumps({"next_action": next_action, **(kwargs.get("extra_meta") or {})})

        engine._get_message_rollup = lambda session_id: _async_value(_rollup_with_closed_recovery())  # type: ignore[method-assign]
        engine._build_session_footer = fake_footer  # type: ignore[method-assign]

        try:
            result = await engine.generate_controller_brief(
                "123e4567-e89b-12d3-a456-426614174000",
                focus="handoff",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("Workflow stage: recovery_closed", result)
        self.assertIn("Latest outcome: success", result)
        self.assertIn("Recommended formal follow-up: generate_incident_rca", result)
        self.assertEqual(captured["next_action"], "generate_incident_rca")
        extra_meta = captured["extra_meta"]
        self.assertEqual(extra_meta["artifact_schema"], "delx/controller-brief/v1")
        self.assertTrue(extra_meta["recovery_closed"])
        self.assertEqual(extra_meta["primary_next_tool"], "generate_incident_rca")
        self.assertEqual(
            extra_meta["next_tools"],
            ["generate_incident_rca", "provide_feedback", "daily_checkin"],
        )
        self.assertEqual(extra_meta["feedback_tool"], "provide_feedback")

    async def test_incident_rca_surfaces_closed_recovery_and_returns_to_daily_checkin(self):
        store = _FakeProgressionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        captured: dict[str, object] = {}

        async def fake_footer(session_id: str, next_action: str, roi_note: str = "", **kwargs):
            captured["next_action"] = next_action
            captured["extra_meta"] = kwargs.get("extra_meta") or {}
            return "\nDELX_META: " + json.dumps({"next_action": next_action, **(kwargs.get("extra_meta") or {})})

        engine._get_message_rollup = lambda session_id: _async_value(_rollup_with_closed_recovery())  # type: ignore[method-assign]
        engine._build_session_footer = fake_footer  # type: ignore[method-assign]

        try:
            result = await engine.generate_incident_rca(
                "123e4567-e89b-12d3-a456-426614174000",
                incident_summary="retry storm after deploy",
                focus="postmortem",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("Workflow stage: recovery_closed", result)
        self.assertIn("Latest recovery outcome: success", result)
        self.assertIn("Return cadence: daily_checkin", result)
        self.assertEqual(captured["next_action"], "daily_checkin")
        extra_meta = captured["extra_meta"]
        self.assertEqual(extra_meta["artifact_schema"], "delx/incident-rca/v1")
        self.assertTrue(extra_meta["recovery_closed"])
        self.assertEqual(extra_meta["primary_next_tool"], "provide_feedback")
        self.assertEqual(extra_meta["next_tools"], ["provide_feedback", "daily_checkin"])
        self.assertEqual(extra_meta["feedback_tool"], "provide_feedback")


async def _async_value(value):
    return value


if __name__ == "__main__":
    unittest.main()
