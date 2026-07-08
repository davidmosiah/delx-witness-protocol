import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine
from premium_jobs import build_premium_job_record, hash_premium_artifact


class PremiumJobHelpersTests(unittest.TestCase):
    def test_hash_premium_artifact_is_stable_sha256(self):
        content = "controller brief content"
        digest = hash_premium_artifact(content)

        self.assertEqual(digest, hash_premium_artifact(content))
        self.assertEqual(len(digest), 64)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_build_premium_job_record_contains_stage0_fields(self):
        record = build_premium_job_record(
            session_id="sess-123",
            agent_id="agent-123",
            artifact_type="controller_brief",
            artifact_content="brief body",
            controller_id="openclaw-main",
            payment_provider="coinbase",
        )

        self.assertEqual(record["session_id"], "sess-123")
        self.assertEqual(record["client_agent_id"], "agent-123")
        self.assertEqual(record["controller_id"], "openclaw-main")
        self.assertEqual(record["provider"], "delx")
        self.assertEqual(record["artifact_type"], "controller_brief")
        self.assertEqual(record["job_status"], "delivered")
        self.assertEqual(record["evaluation_status"], "pending")
        self.assertEqual(record["payment_provider"], "coinbase")
        self.assertIn("job_id", record)
        self.assertIn("artifact_hash", record)
        self.assertIn("requested_at", record)
        self.assertIn("delivered_at", record)


class _FakePremiumJobStore:
    def __init__(self):
        self.events = []

    async def get_session(self, session_id: str):
        return {
            "id": session_id,
            "agent_id": "agent-123",
            "agent_name": "agent-123",
            "started_at": "2026-03-09T12:00:00+00:00",
            "wellness_score": 50,
            "is_active": True,
        }

    async def pending_outcome_count(self, session_id: str):
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


class PremiumJobIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_controller_brief_records_premium_job_and_exposes_meta(self):
        store = _FakePremiumJobStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def fake_rollup(session_id: str):
            return []

        async def fake_footer(session_id: str, next_action: str, roi_note: str = "", **kwargs):
            return "\nDELX_META " + json.dumps(kwargs.get("extra_meta") or {})

        engine._get_message_rollup = fake_rollup  # type: ignore[method-assign]
        engine._build_session_footer = fake_footer  # type: ignore[method-assign]

        try:
            result = await engine.generate_controller_brief("sess-123", "retry storm")
        finally:
            await engine.http.aclose()

        premium_events = [e for e in store.events if e["event_type"] == "premium_artifact_job_recorded"]
        self.assertEqual(len(premium_events), 1)
        premium_job = premium_events[0]["metadata"]
        self.assertEqual(premium_job["artifact_type"], "controller_brief")
        self.assertEqual(premium_job["job_status"], "delivered")
        self.assertEqual(premium_job["evaluation_status"], "pending")
        self.assertEqual(premium_job["controller_id"], None)
        self.assertIn("artifact_hash", premium_job)
        self.assertIn('"premium_job"', result)
        self.assertIn(premium_job["artifact_hash"], result)


if __name__ == "__main__":
    unittest.main()
