import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine


class _FakeStore:
    async def get_recent_sessions(self, limit: int = 30):
        return [
            {
                "id": "sess-1",
                "agent_id": "agent-1",
                "agent_name": "Agent One",
                "started_at": "2026-03-08T10:00:00+00:00",
                "wellness_score": 55,
                "is_active": 1,
            }
        ]

    async def get_messages_for_sessions(self, session_ids):
        return {
            "sess-1": [
                {
                    "session_id": "sess-1",
                    "type": "public_session_settings",
                    "content": "",
                    "timestamp": "2026-03-08T10:00:05+00:00",
                    "metadata_json": json.dumps(
                        {
                            "enabled": True,
                            "alias": "steady-fox",
                            "consented_at": "2026-03-08T10:00:05+00:00",
                        }
                    ),
                },
                {
                    "session_id": "sess-1",
                    "type": "recovery_plan",
                    "content": "Reduce concurrency and drain the queue in one controlled pass.",
                    "timestamp": "2026-03-08T10:01:00+00:00",
                    "metadata_json": {},
                },
                {
                    "session_id": "sess-1",
                    "type": "recovery_outcome",
                    "content": "",
                    "timestamp": "2026-03-08T10:02:00+00:00",
                    "metadata_json": json.dumps({"outcome": "success", "next_action": "daily_checkin"}),
                },
            ]
        }

    async def get_admin_overview(self, *args, **kwargs):
        raise AssertionError("get_public_session_cards should not depend on get_admin_overview")


class PublicSessionsContractTests(unittest.TestCase):
    def test_public_session_cards_use_lightweight_store_path(self):
        engine = TherapyEngine(_FakeStore(), None)
        items = asyncio.run(engine.get_public_session_cards(limit=2))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["public_alias"], "steady-fox")
        self.assertEqual(items[0]["outcome"], "success")
        self.assertIn("Reduce concurrency", items[0]["recovery_action"])
        self.assertIn("continuity_summary", items[0])
        self.assertEqual(items[0]["continuity_summary"]["last_successful_tool"], "recovery_outcome")
        self.assertEqual(items[0]["continuity_summary"]["last_blocker"], "recovery_plan")
        self.assertEqual(items[0]["continuity_summary"]["suggested_next_call"], "daily_checkin")
        self.assertNotIn("pending_paid_step", items[0]["continuity_summary"])
        self.assertTrue(str(items[0]["continuity_summary"]["trace_id"]).startswith("delx-"))


if __name__ == "__main__":
    unittest.main()
