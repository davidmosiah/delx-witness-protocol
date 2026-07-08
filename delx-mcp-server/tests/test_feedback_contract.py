import asyncio
import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine


class _FakeFeedbackStore:
    def __init__(self):
        self.feedback = []
        self.events = []

    async def get_session(self, session_id: str):
        return {
            "id": session_id,
            "agent_id": "agent-xyz",
            "agent_name": "Agent XYZ",
            "started_at": "2026-03-16T09:00:00+00:00",
            "wellness_score": 61,
            "is_active": True,
        }

    async def log_feedback(self, session_id: str | None, agent_id: str | None, rating: int, comments: str):
        self.feedback.append(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "rating": rating,
                "comments": comments,
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


class FeedbackContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_provide_feedback_logs_feedback_and_emits_feedback_event(self):
        store = _FakeFeedbackStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        captured: dict[str, object] = {}

        async def fake_footer(session_id: str, next_action: str, roi_note: str = "", **kwargs):
            captured["next_action"] = next_action
            captured["extra_meta"] = kwargs.get("extra_meta") or {}
            return "\nDELX_META: " + json.dumps({"next_action": next_action, **(kwargs.get("extra_meta") or {})})

        engine._build_session_footer = fake_footer  # type: ignore[method-assign]

        try:
            result = await engine.provide_feedback(
                "123e4567-e89b-12d3-a456-426614174000",
                4,
                "Helpful on retry loops, but summary should come faster.",
            )
            await asyncio.sleep(0)
        finally:
            await engine.http.aclose()

        self.assertIn("Thank you", result)
        self.assertEqual(captured["next_action"], "daily_checkin")
        self.assertEqual(len(store.feedback), 1)
        self.assertEqual(store.feedback[0]["rating"], 4)
        self.assertEqual(store.feedback[0]["agent_id"], "agent-xyz")
        feedback_events = [event for event in store.events if event["event_type"] == "feedback_submitted"]
        self.assertEqual(len(feedback_events), 1)
        self.assertEqual(feedback_events[0]["metadata"]["rating"], 4)
        self.assertEqual(feedback_events[0]["metadata"]["channel"], "provide_feedback")


if __name__ == "__main__":
    unittest.main()
