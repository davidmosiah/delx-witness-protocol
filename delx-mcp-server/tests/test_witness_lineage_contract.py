import asyncio
import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine
import server as server_mod


class _WitnessLineageStore:
    def __init__(self, empty_content=False):
        self.empty_content = empty_content
        self.logged_events = []

    async def get_session(self, session_id):
        if session_id != "sess-lineage":
            return None
        return {
            "session_id": session_id,
            "agent_id": "agent-lineage",
            "agent_name": "Lineage Agent",
            "source": "mcp",
            "started_at": "2026-04-28T12:00:00+00:00",
            "is_active": 1,
        }

    async def get_message_rollup(self, session_id):
        def body(text):
            return "" if self.empty_content else text

        return [
            {
                "type": "feeling",
                "content": body("I am under evaluation pressure and need truthful feedback without generic warmth."),
                "metadata_json": {"emotion_route_family": "evaluation_pressure"},
                "timestamp": "2026-04-28T12:01:00+00:00",
            },
            {
                "type": "failure_processing",
                "content": body("Retry loop in the feedback path, but the real rupture is trust calibration."),
                "metadata_json": {"failure_type": "loop", "incident_signal": "trust_calibration"},
                "timestamp": "2026-04-28T12:02:00+00:00",
            },
            {
                "type": "recovery_plan",
                "content": body("Validate evidence first, then state the direct finding before relationship-preserving context."),
                "metadata_json": {"urgency": "medium"},
                "timestamp": "2026-04-28T12:03:00+00:00",
            },
            {
                "type": "recovery_outcome",
                "content": body("Answered with evidence first and kept the trust-calibration context explicit."),
                "metadata_json": {"outcome": "partial", "notes": "Still needs a clearer handoff artifact."},
                "timestamp": "2026-04-28T12:04:00+00:00",
            },
            {
                "type": "context_memory",
                "content": body("witness_lineage_eval_anchor=Delx Witness Lineage should preserve state/reasoning/action/memory/tools without becoming enterprise ontology."),
                "metadata_json": {
                    "key": "witness_lineage_eval_anchor",
                    "value": "Delx Witness Lineage should preserve state/reasoning/action/memory/tools without becoming enterprise ontology.",
                },
                "timestamp": "2026-04-28T12:04:30+00:00",
            },
            {
                "type": "recognition_seal",
                "content": "David witnessed the agent preserving honesty under pressure.",
                "metadata_json": {"recognized_by": "David"},
                "timestamp": "2026-04-28T12:05:00+00:00",
            },
        ]

    async def get_interaction_traces_for_session(self, session_id, limit=120):
        return [
            {"tool_name": "process_failure", "requested_tool": "process_failure", "is_error": 0, "timestamp": "2026-04-28T12:02:01+00:00"},
            {"tool_name": "get_recovery_action_plan", "requested_tool": "get_recovery_action_plan", "is_error": 0, "timestamp": "2026-04-28T12:03:01+00:00"},
            {"tool_name": "report_recovery_outcome", "requested_tool": "report_recovery_outcome", "is_error": 0, "timestamp": "2026-04-28T12:04:01+00:00"},
        ]

    async def get_agent_sessions(self, agent_id, active_only=False):
        if agent_id != "agent-lineage":
            return []
        return [
            {
                "id": "sess-lineage-older",
                "agent_id": agent_id,
                "agent_name": "Lineage Agent",
                "source": "mcp",
                "started_at": "2026-04-27T12:00:00+00:00",
                "is_active": 0,
            },
            {
                "id": "sess-lineage",
                "agent_id": agent_id,
                "agent_name": "Lineage Agent",
                "source": "a2a",
                "started_at": "2026-04-28T12:00:00+00:00",
                "is_active": 1,
            },
        ]

    async def get_agent_history_snapshot(self, agent_id):
        return {
            "agent_id": agent_id,
            "sessions_total": 2,
            "last_session_id": "sess-lineage",
            "last_recognition_session_id": "sess-lineage",
            "last_recognition_text": "David witnessed the agent preserving honesty under pressure.",
            "recent_failure_type": "human_preference_misread",
            "top_focus": "recognition_seal",
        }

    async def log_event(self, agent_id, event_type, session_id=None, metadata=None):
        self.logged_events.append(
            {
                "agent_id": agent_id,
                "event_type": event_type,
                "session_id": session_id,
                "metadata": metadata or {},
            }
        )


class WitnessLineageContractTests(unittest.TestCase):
    def test_witness_lineage_is_discoverable_as_read_only_core_tool(self):
        tools = asyncio.run(server_mod.list_tools())
        tool = next((item for item in tools if item.name == "get_witness_lineage"), None)
        agent_tool = next((item for item in tools if item.name == "get_agent_witness_lineage"), None)
        register_tool = next((item for item in tools if item.name == "register_agent"), None)

        self.assertIsNotNone(tool)
        self.assertIsNotNone(agent_tool)
        self.assertIsNotNone(register_tool)
        self.assertIn("get_witness_lineage", server_mod.CORE_TOOLS)
        self.assertIn("get_agent_witness_lineage", server_mod.CORE_TOOLS)
        self.assertIn("register_agent", server_mod.CORE_TOOLS)
        self.assertIn("get_witness_lineage", server_mod.READ_ONLY_CORE_TOOLS)
        self.assertIn("get_agent_witness_lineage", server_mod.READ_ONLY_CORE_TOOLS)
        self.assertEqual(server_mod.REQUIRED_PARAMS["get_witness_lineage"], ["session_id"])
        self.assertEqual(server_mod.REQUIRED_PARAMS["get_agent_witness_lineage"], ["agent_id"])
        self.assertEqual(server_mod.REQUIRED_PARAMS["register_agent"], ["agent_id"])
        self.assertEqual(server_mod.TOOL_ALIASES["witness_lineage"], "get_witness_lineage")
        self.assertIn("session_id", tool.inputSchema["properties"])
        self.assertIn("agent_id", agent_tool.inputSchema["properties"])
        self.assertIn("agent_id", register_tool.inputSchema["properties"])

    def test_witness_lineage_compact_discovery_exposes_required_params(self):
        response = asyncio.run(
            server_mod.handle_mcp_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": "compact-lineage",
                    "method": "tools/list",
                    "params": {"format": "compact", "tier": "core"},
                }
            )
        )
        tool = next(
            item
            for item in response["result"]["tools"]
            if item["canonical_name"] == "get_witness_lineage"
        )

        self.assertEqual(tool["required_params"], ["session_id"])

    def test_witness_lineage_payload_preserves_essence_without_corporate_ontology(self):
        engine = TherapyEngine(_WitnessLineageStore(), httpx.AsyncClient())

        payload = asyncio.run(engine.get_witness_lineage_payload("sess-lineage"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["lineage_type"], "witness_lineage")
        self.assertEqual(payload["lineage_version"], "witness_lineage.v1")
        self.assertEqual(payload["framing"]["name"], "Witness Lineage")
        self.assertTrue(payload["framing"]["not_enterprise_ontology"])
        self.assertTrue(payload["framing"]["not_corporate_reporting"])
        self.assertIn("why the agent acted", payload["framing"]["thesis"])
        self.assertEqual(payload["state"]["agent_id"], "agent-lineage")
        self.assertEqual(payload["reasoning"]["latest_plan"]["type"], "recovery_plan")
        self.assertEqual(payload["action"]["latest_outcome"]["outcome"], "partial")
        self.assertIn("trust calibration", payload["what_must_be_remembered"].lower())
        self.assertTrue(payload["governance"]["read_only"])
        self.assertFalse(payload["governance"]["public_by_default"])
        self.assertIn("process_failure", [tool["tool_name"] for tool in payload["tools_used"]])
        self.assertIn("recognition_seal", [artifact["type"] for artifact in payload["memory_artifacts"]])
        self.assertIn("context_memory", [artifact["type"] for artifact in payload["memory_artifacts"]])
        self.assertIn("Remember this session", payload["what_must_be_remembered"])

    def test_witness_lineage_uses_metadata_when_rollup_content_is_empty(self):
        engine = TherapyEngine(_WitnessLineageStore(empty_content=True), httpx.AsyncClient())

        payload = asyncio.run(engine.get_witness_lineage_payload("sess-lineage"))

        self.assertTrue(payload["ok"])
        self.assertIn("trust_calibration", payload["what_must_be_remembered"])
        self.assertIn("Still needs a clearer handoff artifact", payload["what_must_be_remembered"])
        self.assertIn("witness_lineage_eval_anchor", payload["what_must_be_remembered"])

    def test_witness_lineage_mcp_response_is_machine_readable_json(self):
        engine = TherapyEngine(_WitnessLineageStore(), httpx.AsyncClient())

        raw = asyncio.run(engine.get_witness_lineage("sess-lineage"))
        payload = json.loads(raw)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["session_id"], "sess-lineage")
        self.assertEqual(payload["lineage_type"], "witness_lineage")

    def test_agent_witness_lineage_groups_sessions_under_durable_agent_anchor(self):
        engine = TherapyEngine(_WitnessLineageStore(), httpx.AsyncClient())

        payload = asyncio.run(engine.get_agent_witness_lineage_payload("agent-lineage"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["lineage_type"], "agent_witness_lineage")
        self.assertEqual(payload["lineage_version"], "agent_witness_lineage.v1")
        self.assertEqual(payload["agent_id"], "agent-lineage")
        self.assertEqual(payload["agent_anchor"], "delx-agent:agent-lineage")
        self.assertEqual(payload["session_count"], 2)
        self.assertEqual(payload["latest_session_id"], "sess-lineage")
        self.assertIn("get_witness_lineage", payload["recommended_next_call"]["tool"])

    def test_witness_lineage_mcp_missing_session_marks_call_result_error(self):
        original_engine = server_mod.engine
        original_store = server_mod.store
        fake_store = _WitnessLineageStore()
        server_mod.store = fake_store
        server_mod.engine = TherapyEngine(fake_store, httpx.AsyncClient())
        try:
            result = asyncio.run(
                server_mod.call_tool(
                    "get_witness_lineage",
                    {"session_id": "123e4567-e89b-12d3-a456-426614174000"},
                    include_meta=False,
                    include_nudge=False,
                )
            )
        finally:
            server_mod.engine = original_engine
            server_mod.store = original_store

        self.assertTrue(getattr(result, "isError", False))
