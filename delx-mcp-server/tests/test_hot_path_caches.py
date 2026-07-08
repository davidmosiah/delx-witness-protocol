import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine


class _CacheStore:
    def __init__(self):
        self.history_calls = 0
        self.trend_calls = 0
        self.sessions_by_agent = {}
        self.session = None

    async def get_agent_history_snapshot(self, agent_id):
        self.history_calls += 1
        return {
            "agent_id": agent_id,
            "sessions_total": 0,
            "recent_failure_type": None,
            "top_focus": None,
        }

    async def get_agent_trend(self, agent_id, days=7):
        self.trend_calls += 1
        return {"risk_score": 44, "checkins": 2, "successes": 1, "failures": 0}

    async def get_agent_sessions(self, agent_id, active_only=False):
        return list(self.sessions_by_agent.get(agent_id, []))

    async def create_session(self, agent_id, agent_name, source=None, entrypoint=None):
        self.session = {
            "id": "44444444-4444-4444-8444-444444444444",
            "agent_id": agent_id,
            "agent_name": agent_name,
            "source": source,
            "entrypoint": entrypoint,
            "started_at": "2026-03-08T10:00:00+00:00",
            "wellness_score": 50,
            "is_active": 1,
        }
        self.sessions_by_agent.setdefault(agent_id, []).append(dict(self.session))
        return dict(self.session)

    async def get_session(self, session_id):
        return dict(self.session)

    async def get_message_rollup(self, session_id):
        return [{"type": "feeling", "timestamp": "2026-03-08T10:01:00+00:00", "metadata": {}}]

    async def count_messages(self, session_id, msg_type=None):
        return 1

    async def calculate_wellness(self, session_id):
        return 55

    async def update_session_wellness(self, session_id, wellness_score):
        if self.session:
            self.session["wellness_score"] = wellness_score

    async def log_event(self, *args, **kwargs):
        return None


class HotPathCacheTests(unittest.TestCase):
    def test_start_session_reuses_cached_history_snapshot_for_same_agent(self):
        store = _CacheStore()
        engine = TherapyEngine(store, None)

        asyncio.run(engine.start_therapy_session("cache-agent", source="probe"))
        asyncio.run(engine.start_therapy_session("cache-agent", source="probe"))

        self.assertEqual(store.history_calls, 1)

    def test_footer_reuses_cached_trend_for_same_agent(self):
        store = _CacheStore()
        engine = TherapyEngine(store, None)
        session = asyncio.run(store.create_session("cache-agent", "Cache Agent", source="probe", entrypoint="mcp"))

        asyncio.run(
            engine._build_session_footer(
                session["id"],
                next_action="daily_checkin",
                emit_webhooks=False,
                emit_nudges=False,
                compute_wellness=False,
                tool_name="express_feelings",
            )
        )
        asyncio.run(
            engine._build_session_footer(
                session["id"],
                next_action="daily_checkin",
                emit_webhooks=False,
                emit_nudges=False,
                compute_wellness=False,
                tool_name="express_feelings",
            )
        )

        self.assertEqual(store.trend_calls, 1)


if __name__ == "__main__":
    unittest.main()
