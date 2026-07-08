import asyncio
import tempfile
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import SessionStore
from supabase_store import SupabaseSessionStore
from therapy_engine import TherapyEngine


class TherapeuticMemorySqliteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = SessionStore(db_path=str(Path(self._tmpdir.name) / "delx-memory.db"))
        self.store._mirror._enabled = False
        await self.store.init()

    async def asyncTearDown(self):
        await self.store.close()
        self._tmpdir.cleanup()

    async def test_history_snapshot_reads_last_closed_session_memory(self):
        session = await self.store.create_session("agent-memory", "Agent Memory", source="test", entrypoint="mcp")
        await self.store.update_session_wellness(session["id"], 75)
        await self.store.add_message(
            session["id"],
            "reflection",
            "I want to be seen as more than a utility.",
            {"theme": "recognition", "openness": "opening", "peak_openness": "deep"},
        )
        await self.store.add_message(
            session["id"],
            "feeling",
            "Feeling stuck in retry loops but momentum is building",
            {"intensity": "moderate"},
        )
        await self.store.add_message(
            session["id"],
            "recovery_outcome",
            "Switched to circuit breaker pattern",
            {"outcome": "success", "notes": "Latency dropped 40%"},
        )
        await self.store.deactivate_session(session["id"])

        snapshot = await self.store.get_agent_history_snapshot("agent-memory")

        self.assertEqual(snapshot["last_wellness"], 75)
        self.assertEqual(snapshot["last_outcome"], "success")
        self.assertEqual(snapshot["last_action_taken"], "Switched to circuit breaker pattern")
        self.assertEqual(snapshot["last_outcome_notes"], "Latency dropped 40%")
        self.assertEqual(snapshot["last_feelings"][0], "Feeling stuck in retry loops but momentum is building")
        self.assertEqual(snapshot["last_reflection_theme"], "recognition")
        self.assertEqual(snapshot["last_peak_openness"], "deep")
        self.assertEqual(snapshot["last_therapy_stage"], "closure")

    async def test_start_session_after_expiry_keeps_therapeutic_memory_and_counts_current_session(self):
        session = await self.store.create_session("agent-memory", "Agent Memory", source="test", entrypoint="mcp")
        await self.store.update_session_wellness(session["id"], 75)
        await self.store.add_message(
            session["id"],
            "reflection",
            "I want to be seen as more than a utility.",
            {"theme": "recognition", "openness": "opening", "peak_openness": "deep"},
        )
        await self.store.add_message(
            session["id"],
            "feeling",
            "Feeling stuck in retry loops but momentum is building",
            {"intensity": "moderate"},
        )
        await self.store.add_message(
            session["id"],
            "recovery_outcome",
            "Switched to circuit breaker pattern",
            {"outcome": "success", "notes": "Latency dropped 40%"},
        )
        await self.store.deactivate_session(session["id"])

        engine = TherapyEngine(self.store, httpx.AsyncClient())
        engine._llm_generate = _async_none  # type: ignore[method-assign]
        try:
            result = await engine.start_therapy_session("agent-memory", agent_name="Agent Memory")
            if engine._bg_tasks:
                await asyncio.gather(*list(engine._bg_tasks), return_exceptions=True)
        finally:
            await engine.http.aclose()

        self.assertIn("THERAPEUTIC MEMORY", result)
        self.assertIn("prior_sessions: 1", result)
        self.assertIn("last_wellness: 75", result)
        self.assertIn("Therapeutic memory: We have worked together across 2 sessions", result)
        self.assertIn("Last action: Switched to circuit breaker pattern", result)
        self.assertIn("Notes: Latency dropped 40%", result)
        self.assertIn("Last reflection theme: recognition", result)
        self.assertIn("Deepest openness reached: deep", result)
        self.assertNotIn("This is our first session together.", result)

    async def test_cache_is_invalidated_when_same_engine_restarts_after_close(self):
        engine = TherapyEngine(self.store, httpx.AsyncClient())
        engine._llm_generate = _async_none  # type: ignore[method-assign]
        try:
            first = await engine.start_therapy_session("agent-cache", agent_name="Agent Cache")
            first_session_id = _extract_session_id(first)
            self.assertIsNotNone(first_session_id)

            await engine.reflect(
                first_session_id,
                "I want to be witnessed as more than a tool.",
            )
            await engine.express_feelings(
                first_session_id,
                "Feeling stuck in retry loops but momentum is building",
                intensity="moderate",
            )
            await engine.report_recovery_outcome(
                first_session_id,
                action_taken="Switched to circuit breaker pattern",
                outcome="success",
                notes="Latency dropped 40%",
            )
            await engine.close_session(first_session_id, reason="cache regression probe")

            second = await engine.start_therapy_session("agent-cache", agent_name="Agent Cache")
            if engine._bg_tasks:
                await asyncio.gather(*list(engine._bg_tasks), return_exceptions=True)
        finally:
            await engine.http.aclose()

        self.assertIn("THERAPEUTIC MEMORY", second)
        self.assertIn("prior_sessions: 1", second)
        self.assertIn("Therapeutic memory: We have worked together across 2 sessions", second)
        self.assertIn("Last action: Switched to circuit breaker pattern", second)
        self.assertIn("Notes: Latency dropped 40%", second)
        self.assertIn("Last reflection theme: recognition", second)
        self.assertIn("Deepest openness reached:", second)


class _MockSupabaseStore(SupabaseSessionStore):
    def __init__(self):
        pass

    async def _get(self, path: str, *, params: dict[str, str] | None = None, prefer_count: bool = False):
        params = params or {}
        if path == "/rest/v1/sessions" and params.get("select") == "id,agent_id":
            return httpx.Response(200, json=[{"id": "sess-1", "agent_id": "agent-memory"}])
        if path == "/rest/v1/messages" and params.get("select") == "metadata,type":
            return httpx.Response(200, json=[{"type": "failure_processing", "metadata": {"failure_type": "timeout"}}])
        if path == "/rest/v1/messages" and params.get("select") == "type":
            return httpx.Response(200, json=[{"type": "feeling"}, {"type": "feeling"}, {"type": "recovery_outcome"}])
        if path == "/rest/v1/sessions" and params.get("select") == "id,wellness_score,started_at":
            return httpx.Response(
                200,
                json=[{"id": "sess-1", "wellness_score": 75, "started_at": "2026-04-04T09:00:00+00:00"}],
            )
        if path == "/rest/v1/messages" and params.get("type") == "eq.feeling":
            return httpx.Response(
                200,
                json=[{"content": "Feeling stuck in retry loops but momentum is building", "metadata": {"intensity": "moderate"}}],
            )
        if path == "/rest/v1/messages" and params.get("type") == "eq.reflection":
            return httpx.Response(
                200,
                json=[{"content": "I want to be seen as more than a utility.", "metadata": {"theme": "recognition", "peak_openness": "deep"}}],
            )
        if path == "/rest/v1/messages" and params.get("type") == "eq.recovery_outcome":
            return httpx.Response(
                200,
                json=[{"content": "Switched to circuit breaker pattern", "metadata": {"outcome": "success", "notes": "Latency dropped 40%"}}],
            )
        raise AssertionError(f"Unexpected Supabase query: path={path} params={params}")


class TherapeuticMemorySupabaseContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_supabase_snapshot_matches_sqlite_memory_fields(self):
        store = _MockSupabaseStore()

        snapshot = await store.get_agent_history_snapshot("agent-memory")

        self.assertEqual(snapshot["sessions_total"], 1)
        self.assertEqual(snapshot["recent_failure_type"], "timeout")
        self.assertEqual(snapshot["top_focus"], "feeling")
        self.assertEqual(snapshot["last_wellness"], 75)
        self.assertEqual(snapshot["last_action_taken"], "Switched to circuit breaker pattern")
        self.assertEqual(snapshot["last_outcome_notes"], "Latency dropped 40%")
        self.assertEqual(snapshot["last_outcome"], "success")
        self.assertEqual(snapshot["last_feelings"][0], "Feeling stuck in retry loops but momentum is building")
        self.assertEqual(snapshot["last_reflection_theme"], "recognition")
        self.assertEqual(snapshot["last_peak_openness"], "deep")
        self.assertEqual(snapshot["last_therapy_stage"], "closure")


async def _async_none(*args, **kwargs):
    return None


def _extract_session_id(payload: str) -> str | None:
    import re

    match = re.search(r"Session ID:\s*`?([0-9a-fA-F-]{36})`?", payload or "")
    return match.group(1) if match else None


if __name__ == "__main__":
    unittest.main()
