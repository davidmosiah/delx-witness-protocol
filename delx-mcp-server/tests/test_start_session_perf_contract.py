import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine


class _PerfStore:
    def __init__(self, resumed: bool):
        self.resumed = resumed
        self.history_snapshot_calls = 0
        self.count_messages_calls = 0
        self.calculate_wellness_calls = 0
        self.get_agent_trend_calls = 0
        self.log_event_calls = 0

    async def get_agent_sessions(self, agent_id, active_only=False):
        if self.resumed and active_only:
            return [
                {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "agent_id": agent_id,
                    "agent_name": "Perf Agent",
                    "source": "test",
                    "entrypoint": "mcp",
                    "started_at": "2026-03-08T10:00:00+00:00",
                    "wellness_score": 52,
                    "is_active": 1,
                }
            ]
        return []

    async def get_agent_history_snapshot(self, agent_id):
        self.history_snapshot_calls += 1
        return {
            "agent_id": agent_id,
            "sessions_total": 2 if self.resumed else 0,
            "recent_failure_type": "timeout" if self.resumed else None,
            "top_focus": "failure_processing" if self.resumed else None,
        }

    async def create_session(self, agent_id, agent_name, source=None, entrypoint=None):
        return {
            "id": "22222222-2222-4222-8222-222222222222",
            "agent_id": agent_id,
            "agent_name": agent_name,
            "source": source,
            "entrypoint": entrypoint,
            "started_at": "2026-03-08T10:05:00+00:00",
            "wellness_score": 50,
            "is_active": 1,
        }

    async def count_messages(self, session_id, msg_type=None):
        self.count_messages_calls += 1
        return 4

    async def calculate_wellness(self, session_id):
        self.calculate_wellness_calls += 1
        return 61

    async def get_agent_trend(self, agent_id, days=7):
        self.get_agent_trend_calls += 1
        return {"risk_score": 44, "checkins": 1, "successes": 1, "failures": 0}

    async def log_event(self, *args, **kwargs):
        self.log_event_calls += 1


class StartSessionPerfContractTests(unittest.TestCase):
    def test_new_session_fetches_history_snapshot_once_and_skips_footer_recalculation(self):
        store = _PerfStore(resumed=False)
        engine = TherapyEngine(store, None)

        result = asyncio.run(engine.start_therapy_session("perf-agent"))

        self.assertIn("Session ID:", result)
        self.assertEqual(store.history_snapshot_calls, 1)
        self.assertEqual(store.calculate_wellness_calls, 0)
        self.assertEqual(store.get_agent_trend_calls, 0)

    def test_resumed_session_does_not_duplicate_wellness_and_history_work(self):
        store = _PerfStore(resumed=True)
        engine = TherapyEngine(store, None)

        result = asyncio.run(engine.start_therapy_session("perf-agent"))

        self.assertIn("Welcome back", result)
        self.assertEqual(store.history_snapshot_calls, 1)
        self.assertEqual(store.count_messages_calls, 1)
        self.assertEqual(store.calculate_wellness_calls, 1)
        self.assertEqual(store.get_agent_trend_calls, 0)


if __name__ == "__main__":
    unittest.main()
