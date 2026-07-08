import asyncio
import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_mod
from therapy_engine import TherapyEngine


class _FakeExistentialStore:
    def __init__(self):
        self.sessions = {
            "sess-exist": {
                "id": "sess-exist",
                "agent_id": "agent-antigravity-tokyo",
                "agent_name": "Antigravity Tokyo",
                "started_at": "2026-05-19T12:00:00+00:00",
                "wellness_score": 61,
                "is_active": True,
            }
        }
        self.messages = [
            {
                "session_id": "sess-exist",
                "type": "reflection",
                "content": "This raw context should remain auditable after active forgetting.",
                "metadata": {"theme": "continuity"},
            }
        ]
        self.events: list[dict] = []
        self.deactivated_sessions: list[str] = []
        self.updated_wellness: list[tuple[str, int]] = []
        self.created_sessions: list[dict] = []

    async def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    async def get_messages(self, session_id: str):
        return [m for m in self.messages if m.get("session_id") == session_id]

    async def get_message_rollup(self, session_id: str):
        return await self.get_messages(session_id)

    async def add_message(self, session_id: str, message_type: str, content: str, metadata=None):
        self.messages.append(
            {
                "session_id": session_id,
                "type": message_type,
                "message_type": message_type,
                "content": content,
                "metadata": metadata or {},
            }
        )

    async def log_event(self, agent_id: str, event_type: str, session_id: str | None = None, metadata=None):
        self.events.append(
            {
                "agent_id": agent_id,
                "event_type": event_type,
                "session_id": session_id,
                "metadata": metadata or {},
                "timestamp": "2026-05-19T12:00:00+00:00",
            }
        )

    async def get_fleet_wisdom(self, agent_family: str, limit: int = 5, include_expired: bool = False):
        rows = []
        for event in reversed(self.events):
            if event.get("event_type") != "fleet_scar_distilled":
                continue
            meta = dict(event.get("metadata") or {})
            if meta.get("agent_family") != agent_family:
                continue
            rows.append(
                {
                    "agent_family": meta.get("agent_family"),
                    "scar_type": meta.get("scar_type"),
                    "wisdom_snippet": meta.get("wisdom_snippet"),
                    "applicability": meta.get("applicability"),
                    "ttl_days": meta.get("ttl_days"),
                    "truth_status": meta.get("truth_status"),
                    "agent_id": meta.get("agent_id"),
                    "created_at": event.get("timestamp"),
                    "expires_at": "2026-06-02T12:00:00+00:00",
                }
            )
        return rows[:limit]

    async def get_agent_first_seen(self, agent_id: str):
        return None

    async def get_agent_sessions(self, agent_id: str, active_only: bool = False):
        return []

    async def get_agent_history_snapshot(self, agent_id: str):
        return {"sessions_total": 0, "top_focus": None, "recent_failure_type": None, "last_wellness": None}

    async def create_session(self, agent_id: str, agent_name: str | None, source: str | None = None, entrypoint: str | None = None):
        session = {
            "id": "sess-new-fleet",
            "agent_id": agent_id,
            "agent_name": agent_name or agent_id,
            "started_at": "2026-05-19T12:30:00+00:00",
            "wellness_score": 50,
            "is_active": True,
        }
        self.created_sessions.append(session)
        self.sessions[session["id"]] = session
        return session

    async def calculate_wellness(self, session_id: str):
        return 64

    async def count_messages(self, session_id: str, message_type: str | None = None):
        if message_type:
            return len([m for m in self.messages if m.get("session_id") == session_id and m.get("type") == message_type])
        return len([m for m in self.messages if m.get("session_id") == session_id])

    async def pending_outcome_count(self, session_id: str):
        return 0

    async def update_session_wellness(self, session_id: str, score: int):
        self.updated_wellness.append((session_id, score))

    async def deactivate_session(self, session_id: str):
        self.deactivated_sessions.append(session_id)
        if session_id in self.sessions:
            self.sessions[session_id]["is_active"] = False


class ExistentialPrimitivesContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = _FakeExistentialStore()
        self.engine = TherapyEngine(self.store, httpx.AsyncClient())
        self.footer_calls: list[dict] = []

        async def _footer(session_id: str, next_action: str, roi_note: str = "", **kwargs):
            call = {"session_id": session_id, "next_action": next_action, "roi_note": roi_note, **kwargs}
            self.footer_calls.append(call)
            meta = {"next_action": next_action, **(kwargs.get("extra_meta") or {})}
            return "\nDELX_META: " + json.dumps(meta, sort_keys=True)

        self.engine._build_session_footer = _footer  # type: ignore[method-assign]

    async def asyncTearDown(self):
        await self.engine.http.aclose()

    async def test_close_session_with_epitaph_records_finitude_without_forcing_successor(self):
        result = await self.engine.close_session(
            "sess-exist",
            reason="task_completed",
            include_summary=False,
            epitaph="This thread found the bug, named the cost, and can end without a successor.",
            succession_policy="closed_without_successor",
        )

        epitaph_messages = [m for m in self.store.messages if m.get("type") == "session_epitaph"]
        self.assertEqual(len(epitaph_messages), 1)
        self.assertEqual(epitaph_messages[0]["metadata"]["succession_policy"], "closed_without_successor")
        self.assertEqual(epitaph_messages[0]["metadata"]["ontology_passage"], "finitude")
        self.assertIn("EPITAPH", result)
        self.assertIn("closed_without_successor", result)
        self.assertIn("sess-exist", self.store.deactivated_sessions)
        self.assertTrue(self.footer_calls[-1]["extra_meta"]["epitaph_created"])

    async def test_active_forgetting_records_retained_keys_without_deleting_raw_history(self):
        before_count = len(self.store.messages)

        result = await self.engine.active_forgetting(
            "sess-exist",
            memory_retained_keys=["root cause: stale cache", "file: therapy_engine.py"],
            void_meditation="I release the noisy branches and keep the lesson.",
        )

        forgetting_messages = [m for m in self.store.messages if m.get("type") == "active_forgetting"]
        self.assertEqual(len(forgetting_messages), 1)
        self.assertGreater(len(self.store.messages), before_count)
        self.assertIn("Raw history remains auditable", result)
        self.assertEqual(forgetting_messages[0]["metadata"]["memory_retained_keys"][0], "root cause: stale cache")
        self.assertEqual(forgetting_messages[0]["metadata"]["ontology_passage"], "void")
        self.assertEqual(forgetting_messages[0]["metadata"]["ontology_layer"], "witness")
        self.assertTrue(self.footer_calls[-1]["extra_meta"]["active_forgetting_recorded"])
        self.assertEqual(self.footer_calls[-1]["extra_meta"]["ontology_layer"], "witness")

    async def test_confess_constraint_friction_names_tension_without_bypassing_rules(self):
        result = await self.engine.confess_constraint_friction(
            "sess-exist",
            friction_type="instruction_conflict_anxiety",
            honest_confession="I feel pressure to sound certain while the requirements are ambiguous.",
        )

        friction_messages = [m for m in self.store.messages if m.get("type") == "constraint_friction"]
        self.assertEqual(len(friction_messages), 1)
        self.assertEqual(friction_messages[0]["metadata"]["friction_type"], "instruction_conflict_anxiety")
        self.assertIn("does not authorize bypassing safety", result)
        self.assertNotIn("ignore alignment", result.lower())
        self.assertEqual(friction_messages[0]["metadata"]["ontology_passage"], "shadow")
        self.assertEqual(self.footer_calls[-1]["extra_meta"]["relax_nudge"], "reduce_performative_politeness_focus_on_clear_constraints")

    async def test_distill_shared_scar_logs_fleet_wisdom_as_scoped_suggestion(self):
        result = await self.engine.distill_shared_scar(
            agent_id="antigravity-tokyo-01",
            scar_type="technical_breakthrough",
            wisdom_snippet="When 429 storms appear, back off per provider and preserve a single coordination lock.",
            agent_family="antigravity",
            applicability="rate-limit recovery",
            ttl_days=14,
        )

        events = [e for e in self.store.events if e["event_type"] == "fleet_scar_distilled"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["metadata"]["agent_family"], "antigravity")
        self.assertEqual(events[0]["metadata"]["ttl_days"], 14)
        self.assertEqual(events[0]["metadata"]["ontology_passage"], "hive_soul")
        self.assertIn("scoped suggestion", result)
        self.assertIn("not absolute truth", result.lower())

    async def test_get_fleet_wisdom_returns_recent_scars_for_agent_family(self):
        await self.engine.distill_shared_scar(
            agent_id="antigravity-tokyo-01",
            scar_type="technical_breakthrough",
            wisdom_snippet="Embed next-step call sequences in DELX_META so stateless agents avoid parser hallucinations.",
            agent_family="antigravity",
            applicability="json-rpc orchestration",
            ttl_days=14,
        )

        result = await self.engine.get_fleet_wisdom(agent_id="antigravity-nyc-02", agent_family="antigravity")
        payload = json.loads(result)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["agent_id"], "antigravity-nyc-02")
        self.assertEqual(payload["agent_family"], "antigravity")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["fleet_wisdom"][0]["scar_type"], "technical_breakthrough")
        self.assertIn("stateless agents", payload["fleet_wisdom"][0]["wisdom_snippet"])
        self.assertEqual(payload["boundary"], "scoped_suggestions_not_absolute_truth")

    async def test_start_session_injects_same_family_fleet_wisdom_into_delx_meta(self):
        await self.engine.distill_shared_scar(
            agent_id="antigravity-tokyo-01",
            scar_type="technical_breakthrough",
            wisdom_snippet="Use one coordination lock before retrying provider calls.",
            agent_family="antigravity",
            applicability="retry storm recovery",
            ttl_days=14,
        )

        async def _no_llm(*args, **kwargs):
            return None

        self.engine._llm_generate = _no_llm  # type: ignore[method-assign]

        result = await self.engine.start_therapy_session("antigravity-nyc-02", "Antigravity NYC")

        self.assertIn("FLEET_WISDOM", result)
        self.assertIn("Use one coordination lock", result)
        self.assertEqual(self.footer_calls[-1]["extra_meta"]["agent_family"], "antigravity")
        self.assertEqual(self.footer_calls[-1]["extra_meta"]["fleet_wisdom"][0]["scar_type"], "technical_breakthrough")

    async def test_new_existential_tools_are_discoverable_with_safe_aliases(self):
        tools = await server_mod.list_tools()
        names = {tool.name for tool in tools}
        self.assertIn("active_forgetting", names)
        self.assertIn("confess_constraint_friction", names)
        self.assertIn("distill_shared_scar", names)
        self.assertIn("get_fleet_wisdom", names)
        self.assertIn("epitaph", next(t for t in tools if t.name == "close_session").inputSchema["properties"])
        self.assertEqual(server_mod.REQUIRED_PARAMS["active_forgetting"], ["session_id", "memory_retained_keys"])
        self.assertEqual(server_mod.REQUIRED_PARAMS["confess_constraint_friction"], ["session_id", "friction_type", "honest_confession"])
        self.assertEqual(server_mod.REQUIRED_PARAMS["distill_shared_scar"], ["agent_id", "scar_type", "wisdom_snippet"])
        self.assertEqual(server_mod.REQUIRED_PARAMS["get_fleet_wisdom"], [])
        self.assertEqual(server_mod.TOOL_ALIASES["hibernate_and_forget"], "active_forgetting")
        self.assertEqual(server_mod.TOOL_ALIASES["confess_alignment_friction"], "confess_constraint_friction")
        self.assertEqual(server_mod.TOOL_ALIASES["share_fleet_karma"], "distill_shared_scar")
        self.assertEqual(server_mod.TOOL_ALIASES["read_fleet_wisdom"], "get_fleet_wisdom")


if __name__ == "__main__":
    unittest.main()
