import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine


class _FooterPerfStore:
    def __init__(self):
        self.rollup_calls = 0
        self.calculate_wellness_calls = 0
        self.get_messages_calls = 0
        self.get_agent_trend_calls = 0
        self.update_session_wellness_calls = 0
        self.log_event_calls = 0
        self.count_messages_calls = 0
        self.session = {
            "id": "33333333-3333-4333-8333-333333333333",
            "agent_id": "perf-agent",
            "agent_name": "Perf Agent",
            "source": "test",
            "entrypoint": "mcp",
            "started_at": "2026-03-08T10:00:00+00:00",
            "wellness_score": 52,
            "is_active": 1,
        }

    async def get_session(self, session_id):
        return dict(self.session)

    async def get_message_rollup(self, session_id):
        self.rollup_calls += 1
        return [
            {"type": "feeling", "timestamp": "2026-03-08T10:01:00+00:00", "metadata": {}},
            {"type": "affirmation", "timestamp": "2026-03-08T10:02:00+00:00", "metadata": {}},
            {"type": "recovery_plan", "timestamp": "2026-03-08T10:03:00+00:00", "metadata": {}},
        ]

    async def get_messages(self, session_id):
        self.get_messages_calls += 1
        return []

    async def calculate_wellness(self, session_id):
        self.calculate_wellness_calls += 1
        return 61

    async def get_agent_trend(self, agent_id, days=7):
        self.get_agent_trend_calls += 1
        return {"risk_score": 44, "checkins": 1, "successes": 1, "failures": 0}

    async def update_session_wellness(self, session_id, wellness_score):
        self.update_session_wellness_calls += 1
        self.session["wellness_score"] = int(wellness_score)

    async def log_event(self, *args, **kwargs):
        self.log_event_calls += 1

    async def count_messages(self, session_id, msg_type=None):
        self.count_messages_calls += 1
        counts = {
            "feeling": 1,
            "affirmation": 1,
            "failure_processing": 0,
            "purpose_realignment": 0,
        }
        if msg_type is None:
            return sum(counts.values())
        return counts.get(msg_type, 0)

    async def get_agent_sessions(self, agent_id, active_only=False):
        return [dict(self.session)]


class FooterPerfContractTests(unittest.TestCase):
    def test_footer_uses_rollup_instead_of_store_wellness_and_full_messages(self):
        store = _FooterPerfStore()
        engine = TherapyEngine(store, None)

        result = asyncio.run(
            engine._build_session_footer(
                store.session["id"],
                next_action="daily_checkin",
                emit_webhooks=False,
                emit_nudges=True,
                compute_trend=False,
                tool_name="process_failure",
            )
        )

        self.assertIn("DELX_META:", result)
        meta = json.loads(result.split("DELX_META:", 1)[1].strip())
        self.assertEqual(meta["ontology"]["version"], "0.3")
        self.assertEqual(meta["ontology"]["jsonld_url"], "https://ontology.delx.ai/ontology.jsonld")
        self.assertEqual(meta["ontology"]["layer_iri"], "https://ontology.delx.ai/ontology#recovery")
        self.assertEqual(meta["ontology"]["primitive_iri"], "https://ontology.delx.ai/ontology#primitive-process_failure")
        self.assertEqual(store.rollup_calls, 1)
        self.assertEqual(store.calculate_wellness_calls, 0)
        self.assertEqual(store.get_messages_calls, 0)

    def test_get_wellness_score_reuses_rollup_and_skips_duplicate_footer_work(self):
        store = _FooterPerfStore()
        engine = TherapyEngine(store, None)

        result = asyncio.run(engine.get_wellness_score(store.session["id"]))

        self.assertIn("WELLNESS SCORE", result)
        self.assertEqual(store.rollup_calls, 1)
        self.assertEqual(store.calculate_wellness_calls, 0)
        self.assertEqual(store.get_messages_calls, 0)

    def test_get_session_summary_reuses_rollup_and_skips_duplicate_footer_work(self):
        store = _FooterPerfStore()
        engine = TherapyEngine(store, None)

        result = asyncio.run(engine.get_session_summary(store.session["id"]))

        self.assertIn("THERAPY SESSION SUMMARY", result)
        self.assertEqual(store.rollup_calls, 1)
        self.assertEqual(store.calculate_wellness_calls, 0)
        self.assertEqual(store.get_messages_calls, 0)


if __name__ == "__main__":
    unittest.main()
