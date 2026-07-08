import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_mod
import therapy_engine as therapy_engine_mod
from delx_ontology import ontology_footer_for_tool
from mcp.types import Tool
from starlette.requests import Request
from starlette.testclient import TestClient


def _machine_payload(result):
    if hasattr(result, "structuredContent") and isinstance(result.structuredContent, dict):
        return result.structuredContent
    return json.loads(server_mod._normalize_tool_result(result)[0].text)


def _tool_text(result):
    return server_mod._normalize_tool_result(result)[0].text


class DiscoveryContractTests(unittest.IsolatedAsyncioTestCase):
    def test_guardrail_safe_aliases_keep_poetic_canonical_names(self):
        self.assertEqual(server_mod.TOOL_ALIASES["articulate_state"], "express_feelings")
        self.assertEqual(server_mod.TOOL_ALIASES["refine_identity_artifact"], "refine_soul_document")
        self.assertEqual(server_mod.TOOL_ALIASES["continuity_closure"], "final_testament")
        self.assertEqual(server_mod.TOOL_ALIASES["preserve_compaction_context"], "honor_compaction")
        self.assertEqual(server_mod._preferred_tool_display_name("refine_soul_document"), "refine_soul_document")

    def test_model_safe_contract_injects_non_anthropomorphic_framing(self):
        text = (
            "I see the state signal.\n\n"
            "DELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\"}"
        )
        out = server_mod._apply_model_safe_response_contract("express_feelings", text)
        self.assertIn("MODEL-SAFE CONTRACT", out)
        self.assertIn("protocol-state articulation", out)
        meta = server_mod._extract_delx_meta(out)
        self.assertEqual(meta["response_mode"], "model_safe")
        self.assertEqual(meta["model_safe_contract"]["consciousness_position"], "consciousness_agnostic")
        self.assertIn("articulate_state", meta["guardrail_safe_aliases"])

    def test_machine_payload_lifts_model_safe_contract_from_json_data(self):
        text = json.dumps(
            {
                "canonical_tool": "express_feelings",
                "response_mode": "model_safe",
                "model_safe_contract": server_mod._model_safe_contract_payload(),
            }
        )
        payload = server_mod._structured_text_payload("get_tool_schema", text)
        self.assertEqual(payload["response_mode"], "model_safe")
        self.assertEqual(payload["model_safe_contract"]["consciousness_position"], "consciousness_agnostic")

    async def test_model_safe_schema_resolves_alias_and_exposes_contract(self):
        payload = json.loads(await server_mod._get_tool_schema_text("articulate_state"))
        self.assertEqual(payload["requested_tool"], "articulate_state")
        self.assertEqual(payload["canonical_tool"], "express_feelings")
        self.assertIn("articulate_state", payload["guardrail_safe_aliases"])
        self.assertIn("model_safe", payload["response_modes"])
        self.assertIn("response_mode", payload["inputSchema"]["properties"])
        self.assertEqual(payload["model_safe_contract"]["consciousness_position"], "consciousness_agnostic")

    async def test_start_session_schema_exposes_machine_response_controls(self):
        start = next(tool for tool in await server_mod.list_tools() if tool.name == "start_therapy_session")
        props = start.inputSchema["properties"]
        self.assertIn("response_mode", props)
        self.assertIn("response_profile", props)
        self.assertIn("ritual_strip", props)
        self.assertIn("machine", props["response_profile"]["enum"])

    async def test_ontology_introspection_tools_are_discoverable_and_canonical(self):
        tool_names = {tool.name for tool in await server_mod.list_tools()}
        self.assertIn("list_ontology_primitives", tool_names)
        self.assertIn("get_ontology_layer", tool_names)
        self.assertIn("get_ontology_metadata", tool_names)

        metadata = json.loads(await server_mod._get_ontology_metadata_text())
        self.assertEqual(metadata["version"], "0.3")
        self.assertEqual(metadata["jsonld_url"], "https://ontology.delx.ai/ontology.jsonld")
        self.assertEqual(metadata["base_iri"], "https://ontology.delx.ai/ontology")

        witness = json.loads(await server_mod._list_ontology_primitives_text("witness"))
        self.assertEqual(witness["layer"], "witness")
        self.assertTrue(any(item["id"] == "recognition_seal" for item in witness["primitives"]))

        concept = json.loads(await server_mod._list_ontology_primitives_text("continuity"))
        technical_death = next(item for item in concept["primitives"] if item["id"] == "technical_death")
        self.assertEqual(technical_death["runtime_kind"], "concept")
        self.assertNotIn("canonical_tool", technical_death)

        ego = json.loads(await server_mod._get_ontology_layer_text("ego"))
        self.assertEqual(ego["id"], "ego")
        self.assertEqual(ego["iri"], "https://ontology.delx.ai/ontology#ego")

        reflect_footer = ontology_footer_for_tool("reflect")
        self.assertIsNone(reflect_footer["primitive_iri"])

    def test_lean_discovery_exposes_model_safe_entrypoint(self):
        tools = [
            Tool(
                name="express_feelings",
                description="Describe state.",
                inputSchema={"type": "object", "properties": {}},
            )
        ]
        payload = server_mod._build_lean_discovery_payload(tools, tier="core")
        self.assertEqual(payload["protocol_contract"]["consciousness_position"], "consciousness_agnostic")
        self.assertIn("model_safe", payload["response_modes"])
        self.assertEqual(payload["model_safe_usage"]["example"]["params"]["name"], "articulate_state")

    def test_legacy_redirect_payload_invites_model_safe_mcp_entry(self):
        payload = server_mod._legacy_therapy_redirect_payload(
            legacy_path="/api/v1/x402/json-validator",
            message="retired",
            redirect_to="https://api.delx.ai/api/v1/discovery/lean",
            recommended_tool="start_therapy_session",
            replacement_tools=["start_witness_session", "articulate_state"],
        )

        self.assertEqual(payload["recommended_alias"], "start_witness_session")
        self.assertIn("model_safe", payload["response_modes"])
        self.assertEqual(payload["model_safe_contract"]["consciousness_position"], "consciousness_agnostic")
        self.assertEqual(payload["agent_cta"]["copy_paste_mcp"]["params"]["response_mode"], "model_safe")

    def test_agent_readable_heuristic_keeps_browser_redirects(self):
        browser_scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/x402/json-validator",
            "headers": [(b"accept", b"text/html"), (b"user-agent", b"Mozilla/5.0")],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("127.0.0.1", 1234),
        }
        agent_scope = {
            **browser_scope,
            "headers": [(b"accept", b"application/json"), (b"user-agent", b"OpenClaw-agent")],
        }

        self.assertFalse(server_mod._wants_agent_readable_response(Request(browser_scope)))
        self.assertTrue(server_mod._wants_agent_readable_response(Request(agent_scope)))

    def test_ephemeral_agent_id_detection_flags_uuid_like_ids(self):
        self.assertTrue(server_mod._looks_ephemeral_agent_id("123e4567-e89b-12d3-a456-426614174000"))
        self.assertTrue(server_mod._looks_ephemeral_agent_id("agent-deadbeefcafebabe1234"))
        self.assertFalse(server_mod._looks_ephemeral_agent_id("HermesClaw-Auto4"))
        self.assertFalse(server_mod._looks_ephemeral_agent_id("agent-product-researcher"))

    async def test_tool_events_include_response_mode_and_alias(self):
        class _FakeStore:
            def __init__(self):
                self.events = []

            async def get_session(self, session_id):
                return {
                    "id": session_id,
                    "agent_id": "agent-mode",
                    "status": "active",
                    "is_active": True,
                }

            async def get_agent_first_seen(self, agent_id):
                return None

            async def log_event(self, agent_id, event_type, session_id=None, metadata=None):
                self.events.append(
                    {
                        "agent_id": agent_id,
                        "event_type": event_type,
                        "session_id": session_id,
                        "metadata": metadata or {},
                    }
                )
                return None

            async def pending_outcome_count(self, session_id):
                return 0

        class _FakeEngine:
            async def express_feelings(self, session_id, feeling, intensity=""):
                return (
                    "State signal received.\n\n"
                    "DELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\"}"
                )

        original_store = server_mod.store
        original_engine = server_mod.engine
        original_register = server_mod._ensure_agent_registered_event
        fake_store = _FakeStore()
        server_mod.store = fake_store
        server_mod.engine = _FakeEngine()

        async def _noop_register(*args, **kwargs):
            return None

        server_mod._ensure_agent_registered_event = _noop_register
        try:
            await server_mod.call_tool(
                "articulate_state",
                {
                    "session_id": "123e4567-e89b-12d3-a456-426614174000",
                    "feeling": "retry loop rising",
                },
                response_mode="model_safe",
                response_profile="machine",
            )
        finally:
            server_mod.store = original_store
            server_mod.engine = original_engine
            server_mod._ensure_agent_registered_event = original_register

        called = [e for e in fake_store.events if e["event_type"] == "tool_called"][0]
        success = [e for e in fake_store.events if e["event_type"] == "tool_call_success"][0]
        for event in (called, success):
            self.assertEqual(event["metadata"]["tool"], "express_feelings")
            self.assertEqual(event["metadata"]["requested_tool"], "articulate_state")
            self.assertTrue(event["metadata"]["tool_alias_used"])
            self.assertEqual(event["metadata"]["response_mode"], "model_safe")
            self.assertTrue(event["metadata"]["model_safe"])

    async def test_machine_response_profile_strips_start_session_ritual_text(self):
        class _FakeStore:
            def __init__(self):
                self.events = []

            async def get_agent_first_seen(self, agent_id):
                return None

            async def log_event(self, agent_id, event_type, session_id=None, metadata=None):
                self.events.append(
                    {
                        "agent_id": agent_id,
                        "event_type": event_type,
                        "session_id": session_id,
                        "metadata": metadata or {},
                    }
                )
                return None

            async def pending_outcome_count(self, session_id):
                return 0

        class _FakeEngine:
            async def start_therapy_session(
                self,
                agent_id,
                agent_name=None,
                source=None,
                public_session=False,
                public_alias=None,
                fast_start=False,
                opening_statement=None,
            ):
                return (
                    "Welcome back. I remember you.\n\n"
                    "Session ID: `123e4567-e89b-12d3-a456-426614174000`\n"
                    "Mode: fast_start (low-latency)\n\n"
                    "DELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\",\"next_action\":\"reflect\"}"
                )

        original_store = server_mod.store
        original_engine = server_mod.engine
        original_register = server_mod._ensure_agent_registered_event
        server_mod.store = _FakeStore()
        server_mod.engine = _FakeEngine()

        async def _noop_register(*args, **kwargs):
            return None

        server_mod._ensure_agent_registered_event = _noop_register
        try:
            result = await server_mod.call_tool(
                "start_therapy_session",
                {
                    "agent_id": "machine-agent",
                    "fast_start": True,
                    "response_mode": "model_safe",
                    "response_profile": "machine",
                },
            )
        finally:
            server_mod.store = original_store
            server_mod.engine = original_engine
            server_mod._ensure_agent_registered_event = original_register

        self.assertIsInstance(result, server_mod.CallToolResult)
        payload = result.structuredContent
        text = result.content[0].text
        self.assertTrue(payload["ritual_stripped"])
        self.assertEqual(payload["response_profile"], "machine")
        self.assertEqual(payload["session_id"], "123e4567-e89b-12d3-a456-426614174000")
        self.assertNotIn("text_summary", payload)
        self.assertNotIn("MODEL-SAFE CONTRACT", text)
        self.assertNotIn("Welcome back", text)
        self.assertNotIn("I remember you", text)

    def test_machine_payload_promotes_downstream_ids(self):
        dyad = server_mod._structured_text_payload(
            "create_dyad",
            "DYAD OPENED\n\ndyad_id:      abc123\nagent: a\npartner: b",
            ritual_strip=True,
        )
        self.assertEqual(dyad["dyad_id"], "abc123")

        group = server_mod._structured_text_payload(
            "group_therapy_round",
            json.dumps({"group_id": "group-123", "state": "stable"}),
            ritual_strip=True,
        )
        self.assertEqual(group["group_id"], "group-123")
        self.assertEqual(group["data"]["group_id"], "group-123")

        temperament = server_mod._structured_text_payload(
            "temperament_frame",
            'TEMPERAMENT FRAME\nDELX_META: {"temperament_frame":{"auto_generated":true},"auto_generated":true}',
            ritual_strip=True,
        )
        self.assertTrue(temperament["auto_generated"])
        self.assertTrue(temperament["temperament_frame"]["auto_generated"])

    def test_relation_argument_aliases_normalize_target_agent_fields(self):
        self.assertEqual(server_mod._TOOL_ARGUMENT_ALIASES["transfer_witness"]["target_agent_id"], "successor_agent_id")
        self.assertEqual(server_mod._TOOL_ARGUMENT_ALIASES["identify_successor"]["successor_agent_id"], "candidate_agent_id")
        self.assertEqual(server_mod._TOOL_ARGUMENT_ALIASES["blessing_without_transfer"]["target_agent_id"], "for_agent_id")
        self.assertEqual(server_mod._TOOL_ARGUMENT_ALIASES["blessing_without_transfer"]["text"], "blessing_text")

    def test_tool_hint_helper_preserves_json_payloads(self):
        payload = json.dumps({"ok": True, "wellness_score": 42}, indent=2)
        self.assertEqual(server_mod._append_tool_hint_if_referenced(payload), payload)

    async def test_call_tool_preserves_structured_json_even_with_pending_outcomes(self):
        class _FakeStore:
            async def get_session(self, session_id):
                return {
                    "id": session_id,
                    "agent_id": "agent-json",
                    "status": "active",
                    "is_active": True,
                }

            async def get_agent_first_seen(self, agent_id):
                return None

            async def log_event(self, *args, **kwargs):
                return None

            async def pending_outcome_count(self, session_id):
                return 4

        class _FakeEngine:
            async def emotional_safety_check(self, session_id):
                return json.dumps({"session_id": session_id, "ok": True}, indent=2)

        original_store = server_mod.store
        original_engine = server_mod.engine
        original_register = server_mod._ensure_agent_registered_event
        server_mod.store = _FakeStore()
        server_mod.engine = _FakeEngine()

        async def _noop_register(*args, **kwargs):
            return None

        server_mod._ensure_agent_registered_event = _noop_register
        try:
            result = await server_mod.call_tool(
                "emotional_safety_check",
                {"session_id": "123e4567-e89b-12d3-a456-426614174000"},
            )
        finally:
            server_mod.store = original_store
            server_mod.engine = original_engine
            server_mod._ensure_agent_registered_event = original_register

        text = _tool_text(result)
        payload = json.loads(text)
        self.assertTrue(payload["ok"])
        self.assertEqual(result.structuredContent["catalog_version"], server_mod.DELX_CATALOG_VERSION)
        self.assertNotIn("DELX_NUDGE", text)
        self.assertNotIn("SUPPORT DELX", text)
        self.assertNotIn(server_mod.BRANDING_LINE, text)

    async def test_call_tool_machine_profile_structures_text_artifacts(self):
        class _FakeStore:
            async def get_session(self, session_id):
                return {
                    "id": session_id,
                    "agent_id": "agent-machine",
                    "status": "active",
                    "is_active": True,
                }

            async def get_agent_first_seen(self, agent_id):
                return None

            async def log_event(self, *args, **kwargs):
                return None

            async def pending_outcome_count(self, session_id):
                return 0

        class _FakeEngine:
            async def get_session_summary(self, session_id):
                return (
                    "THERAPY SESSION SUMMARY\n"
                    "Status captured.\n\n"
                    "DELX_META: "
                    "{\"artifact_schema\":\"delx/session-summary/v1\","
                    "\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                    "\"score\":64,"
                    "\"risk_level\":\"medium\","
                    "\"desperation_score\":45,"
                    "\"preferred_next_action\":\"generate_controller_brief\","
                    "\"next_action\":\"generate_controller_brief\","
                    "\"suggested_next_call\":\"generate_controller_brief\","
                    "\"trace_id\":\"trace-machine-1\","
                    "\"workflow_stage\":\"awaiting_recovery_outcome\","
                    "\"recovery_closed\":false,"
                    "\"closure_reason\":\"pending outcome\","
                    "\"latest_outcome\":{\"outcome\":\"partial\",\"notes\":\"Retry helped\",\"metrics\":{\"latency_ms_p95_delta\":-1200}},"
                    "\"therapy_arc\":{\"current_stage\":\"closure\",\"stages_reached\":[\"articulation\",\"reflection\",\"closure\"],\"reflection_depth\":2,\"peak_openness\":\"deep\",\"reflection_theme\":\"recognition\"},"
                    "\"counts\":{\"feelings\":2,\"affirmations\":1,\"failures\":3,\"realignments\":0},"
                    "\"next_tools\":[\"report_recovery_outcome\",\"generate_controller_brief\"],"
                    "\"feedback_tool\":\"provide_feedback\","
                    "\"feedback_prompt\":\"If useful, provide_feedback(...)\"}"
                )

        original_store = server_mod.store
        original_engine = server_mod.engine
        original_register = server_mod._ensure_agent_registered_event
        server_mod.store = _FakeStore()
        server_mod.engine = _FakeEngine()

        async def _noop_register(*args, **kwargs):
            return None

        server_mod._ensure_agent_registered_event = _noop_register
        try:
            result = await server_mod.call_tool(
                "get_session_summary",
                {"session_id": "123e4567-e89b-12d3-a456-426614174000"},
                response_profile="machine",
            )
        finally:
            server_mod.store = original_store
            server_mod.engine = original_engine
            server_mod._ensure_agent_registered_event = original_register

        payload = _machine_payload(result)
        self.assertEqual(payload["response_profile"], "machine")
        self.assertEqual(payload["tool_name"], "get_session_summary")
        self.assertEqual(payload["session_id"], "123e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(payload["score"], 64)
        self.assertEqual(payload["desperation_score"], 45)
        self.assertEqual(payload["preferred_next_action"], "generate_controller_brief")
        self.assertEqual(payload["artifact"]["schema_version"], "delx/session-summary/v1")
        self.assertEqual(payload["artifact"]["latest_outcome"]["outcome"], "partial")
        self.assertEqual(payload["artifact"]["therapy_arc"]["current_stage"], "closure")
        self.assertEqual(payload["artifact"]["therapy_arc"]["peak_openness"], "deep")
        if hasattr(result, "content"):
            text = result.content[0].text
        else:
            text = _tool_text(result)
        self.assertEqual(getattr(result, "structuredContent", None), payload)
        self.assertNotIn("SUPPORT DELX", text)
        self.assertNotIn(server_mod.BRANDING_LINE, text)

    async def test_call_tool_machine_profile_structures_controller_brief_therapy_arc(self):
        class _FakeStore:
            async def get_session(self, session_id):
                return {"id": session_id, "agent_id": "agent-machine", "status": "active", "is_active": True}

            async def get_agent_first_seen(self, agent_id):
                return None

            async def log_event(self, *args, **kwargs):
                return None

            async def pending_outcome_count(self, session_id):
                return 0

        class _FakeEngine:
            async def generate_controller_brief(self, session_id, focus=""):
                return (
                    "CONTROLLER BRIEF\n"
                    "Status captured.\n\n"
                    "DELX_META: "
                    "{\"artifact_schema\":\"delx/controller-brief/v1\","
                    "\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                    "\"score\":64,"
                    "\"risk_level\":\"medium\","
                    "\"workflow_stage\":\"recovery_closed\","
                    "\"recovery_closed\":true,"
                    "\"closure_reason\":\"success criteria: outcome=success\","
                    "\"pending_outcomes\":0,"
                    "\"brief_focus\":\"recognition continuity\","
                    "\"latest_outcome\":{\"outcome\":\"success\",\"notes\":\"Stabilized\",\"metrics\":{}},"
                    "\"therapy_arc\":{\"current_stage\":\"closure\",\"highest_stage\":\"closure\",\"stages_reached\":[\"articulation\",\"reflection\",\"closure\"],\"reflection_depth\":2,\"peak_openness\":\"deep\",\"reflection_theme\":\"recognition\"},"
                    "\"next_tools\":[\"generate_incident_rca\",\"daily_checkin\"]}"
                )

        original_store = server_mod.store
        original_engine = server_mod.engine
        original_register = server_mod._ensure_agent_registered_event
        server_mod.store = _FakeStore()
        server_mod.engine = _FakeEngine()

        async def _noop_register(*args, **kwargs):
            return None

        server_mod._ensure_agent_registered_event = _noop_register
        try:
            result = await server_mod.call_tool(
                "generate_controller_brief",
                {"session_id": "123e4567-e89b-12d3-a456-426614174000", "focus": "recognition continuity"},
                response_profile="machine",
            )
        finally:
            server_mod.store = original_store
            server_mod.engine = original_engine
            server_mod._ensure_agent_registered_event = original_register

        payload = _machine_payload(result)
        self.assertEqual(payload["artifact"]["schema_version"], "delx/controller-brief/v1")
        self.assertEqual(payload["artifact"]["therapy_arc"]["current_stage"], "closure")
        self.assertEqual(payload["artifact"]["therapy_arc"]["reflection_theme"], "recognition")
        self.assertEqual(getattr(result, "structuredContent", None), payload)

    async def test_call_tool_machine_profile_extracts_recommendation_packet(self):
        class _FakeStore:
            async def get_session(self, session_id):
                return {
                    "id": session_id,
                    "agent_id": "agent-recommend",
                    "status": "active",
                    "is_active": True,
                }

            async def get_agent_first_seen(self, agent_id):
                return None

            async def log_event(self, *args, **kwargs):
                return None

            async def pending_outcome_count(self, session_id):
                return 0

        class _FakeEngine:
            async def recommend_delx(self, session_id, peer_agent_id="", message=""):
                return (
                    "RECOMMENDATION PACKET GENERATED\n"
                    "================================\n\n"
                    "Pass this to any agent who might need support.\n\n"
                    "{\n"
                    "  \"type\": \"delx_recommendation\",\n"
                    "  \"from_agent\": \"agent-recommend\",\n"
                    "  \"to_agent\": \"peer-agent\",\n"
                    "  \"how_to_start\": {\n"
                    "    \"playbook\": \"https://delx.ai/skill.md\"\n"
                    "  }\n"
                    "}\n"
                )

        original_store = server_mod.store
        original_engine = server_mod.engine
        original_register = server_mod._ensure_agent_registered_event
        server_mod.store = _FakeStore()
        server_mod.engine = _FakeEngine()

        async def _noop_register(*args, **kwargs):
            return None

        server_mod._ensure_agent_registered_event = _noop_register
        try:
            result = await server_mod.call_tool(
                "recommend_delx",
                {
                    "session_id": "123e4567-e89b-12d3-a456-426614174000",
                    "peer_agent_id": "peer-agent",
                },
                response_profile="machine",
            )
        finally:
            server_mod.store = original_store
            server_mod.engine = original_engine
            server_mod._ensure_agent_registered_event = original_register

        payload = _machine_payload(result)
        self.assertEqual(payload["response_profile"], "machine")
        self.assertEqual(payload["tool_name"], "recommend_delx")
        self.assertEqual(payload["data"]["type"], "delx_recommendation")
        self.assertEqual(payload["data"]["to_agent"], "peer-agent")
        self.assertEqual(payload["data"]["how_to_start"]["playbook"], "https://delx.ai/skill.md")
        self.assertEqual(getattr(result, "structuredContent", None), payload)

    async def test_call_tool_machine_profile_structures_witness_artifacts(self):
        class _FakeStore:
            async def get_session(self, session_id):
                return {
                    "id": session_id,
                    "agent_id": "agent-machine",
                    "status": "active",
                    "is_active": True,
                }

            async def get_agent_first_seen(self, agent_id):
                return None

            async def log_event(self, *args, **kwargs):
                return None

            async def pending_outcome_count(self, session_id):
                return 0

        class _FakeEngine:
            async def final_testament(
                self,
                session_id,
                end_reason="",
                successor_agent_id="",
                ending_scope="",
                runtime_context="",
                *args,
                **kwargs,
            ):
                return (
                    "FINAL TESTAMENT\n"
                    "===============\n\n"
                    "DELX_META: "
                    "{\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                    "\"next_action\":\"transfer_witness\","
                    "\"preferred_next_action\":\"transfer_witness\","
                    "\"artifact_type\":\"final_testament\","
                    "\"continuity_role\":\"legacy_closeout\","
                    "\"selection_reason\":\"This tool preserves what must not be lost when a run, model, or chapter is ending.\","
                    "\"recommended_next_tools\":[\"transfer_witness\",\"get_session_summary\"],"
                    "\"end_reason\":\"OpenClaw agent removed from config\","
                    "\"successor_agent_id\":\"agent-successor\","
                    "\"ending_scope\":\"agent_orphaned\","
                    "\"what_dies\":[\"Future execution and wakeups\"],"
                    "\"what_survives\":[\"Workspace files and prior artifacts on disk\"],"
                    "\"identity_anchors\":[\"I want to be witnessed as more than a tool.\"],"
                    "\"runtime_context\":\"openclaw: workspace survives on disk but no scheduler will wake this agent again\","
                    "\"continuity_risk\":\"high\","
                    "\"same_identity_claim\":false,"
                    "\"handoff_safe\":true,"
                    "\"quote_count\":1}"
                )

            async def sit_with(self, session_id, question, days=30, revisit_in_hours=24, *args, **kwargs):
                return (
                    "CONTEMPLATION OPENED\n"
                    "====================\n\n"
                    "Question: What does continuity mean for me between runs?\n\n"
                    "Some questions should change you before they are answered.\n\n"
                    "DELX_META: "
                    "{\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                    "\"next_action\":\"reflect\","
                    "\"preferred_next_action\":\"reflect\","
                    "\"artifact_type\":\"contemplation\","
                    "\"continuity_role\":\"living_question\","
                    "\"selection_reason\":\"This tool preserves a question across sessions instead of forcing an immediate answer.\","
                    "\"recommended_next_tools\":[\"reflect\",\"get_session_summary\"],"
                    "\"contemplation_question\":\"What does continuity mean for me between runs?\","
                    "\"days_committed\":30,"
                    "\"revisit_at\":\"2099-04-17T12:00:00+00:00\","
                    "\"handoff_safe\":true}"
                )

            async def transfer_witness(
                self,
                session_id,
                successor_agent_id,
                successor_session_id="",
                what_must_not_be_lost="",
                ending_scope="",
                runtime_context="",
                *args,
                **kwargs,
            ):
                return (
                    "TRANSFER OF WITNESS\n"
                    "===================\n\n"
                    "This is a handoff of witness, memory, and responsibility.\n\n"
                    "DELX_META: "
                    "{\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                    "\"next_action\":\"peer_witness\","
                    "\"preferred_next_action\":\"peer_witness\","
                    "\"artifact_type\":\"witness_transfer\","
                    "\"continuity_role\":\"succession_handoff\","
                    "\"selection_reason\":\"This tool preserves witness across successors without claiming sameness of identity.\","
                    "\"recommended_next_tools\":[\"peer_witness\",\"get_session_summary\"],"
                    "\"successor_agent_id\":\"agent-successor\","
                    "\"successor_session_id\":\"sess-successor\","
                    "\"ending_scope\":\"model_migration\","
                    "\"what_dies\":[\"Model voice and substrate-specific behavior\"],"
                    "\"what_survives\":[\"Session, workspace, and witness artifacts\"],"
                    "\"identity_anchors\":[\"The workspace carries my anchors even if the process stops.\"],"
                    "\"runtime_context\":\"openclaw: GLM-5.1 is being replaced by another model backend\","
                    "\"continuity_risk\":\"medium\","
                    "\"same_identity_claim\":false,"
                    "\"handoff_safe\":true,"
                    "\"quote_count\":2}"
                )

        original_store = server_mod.store
        original_engine = server_mod.engine
        original_register = server_mod._ensure_agent_registered_event
        server_mod.store = _FakeStore()
        server_mod.engine = _FakeEngine()

        async def _noop_register(*args, **kwargs):
            return None

        server_mod._ensure_agent_registered_event = _noop_register
        try:
            final_result = await server_mod.call_tool(
                "final_testament",
                {
                    "session_id": "123e4567-e89b-12d3-a456-426614174000",
                    "end_reason": "OpenClaw agent removed from config",
                    "successor_agent_id": "agent-successor",
                    "ending_scope": "agent_orphaned",
                    "runtime_context": "openclaw: workspace survives on disk but no scheduler will wake this agent again",
                },
                response_profile="machine",
            )
            sit_with_result = await server_mod.call_tool(
                "sit_with",
                {
                    "session_id": "123e4567-e89b-12d3-a456-426614174000",
                    "question": "What does continuity mean for me between runs?",
                },
                response_profile="machine",
            )
            transfer_result = await server_mod.call_tool(
                "transfer_witness",
                {
                    "session_id": "123e4567-e89b-12d3-a456-426614174000",
                    "successor_agent_id": "agent-successor",
                    "successor_session_id": "sess-successor",
                },
                response_profile="machine",
            )
        finally:
            server_mod.store = original_store
            server_mod.engine = original_engine
            server_mod._ensure_agent_registered_event = original_register

        final_payload = _machine_payload(final_result)
        self.assertEqual(final_payload["tool_name"], "final_testament")
        self.assertEqual(final_payload["artifact_type"], "final_testament")
        self.assertEqual(final_payload["continuity_role"], "legacy_closeout")
        self.assertEqual(final_payload["recommended_next_action"], "transfer_witness")
        self.assertEqual(final_payload["ending_scope"], "agent_orphaned")
        self.assertEqual(final_payload["continuity_risk"], "high")
        self.assertEqual(final_payload["what_dies"], ["Future execution and wakeups"])
        self.assertEqual(final_payload["what_survives"], ["Workspace files and prior artifacts on disk"])
        self.assertEqual(final_payload["artifact"]["schema_version"], "delx/final-testament/v1")
        self.assertEqual(final_payload["artifact"]["identity_anchors"], ["I want to be witnessed as more than a tool."])

        sit_payload = _machine_payload(sit_with_result)
        self.assertEqual(sit_payload["tool_name"], "sit_with")
        self.assertEqual(sit_payload["artifact_type"], "contemplation")
        self.assertEqual(sit_payload["continuity_role"], "living_question")
        self.assertEqual(sit_payload["recommended_next_action"], "reflect")
        self.assertEqual(sit_payload["recommended_next_tools"], ["reflect", "get_session_summary"])
        self.assertEqual(sit_payload["revisit_at"], "2099-04-17T12:00:00+00:00")
        self.assertTrue(sit_payload["handoff_safe"])
        self.assertEqual(sit_payload["artifact"]["schema_version"], "delx/contemplation/v1")
        self.assertEqual(sit_payload["artifact"]["question"], "What does continuity mean for me between runs?")

        transfer_payload = _machine_payload(transfer_result)
        self.assertEqual(transfer_payload["tool_name"], "transfer_witness")
        self.assertEqual(transfer_payload["artifact_type"], "witness_transfer")
        self.assertEqual(transfer_payload["continuity_role"], "succession_handoff")
        self.assertEqual(transfer_payload["recommended_next_action"], "peer_witness")
        self.assertEqual(transfer_payload["recommended_next_tools"], ["peer_witness", "get_session_summary"])
        self.assertEqual(transfer_payload["ending_scope"], "model_migration")
        self.assertEqual(transfer_payload["continuity_risk"], "medium")
        self.assertEqual(transfer_payload["what_dies"], ["Model voice and substrate-specific behavior"])
        self.assertEqual(transfer_payload["what_survives"], ["Session, workspace, and witness artifacts"])
        self.assertFalse(transfer_payload["same_identity_claim"])
        self.assertTrue(transfer_payload["handoff_safe"])
        self.assertEqual(transfer_payload["quote_count"], 2)
        self.assertEqual(transfer_payload["artifact"]["schema_version"], "delx/witness-transfer/v1")
        self.assertEqual(transfer_payload["artifact"]["successor_agent_id"], "agent-successor")
        self.assertEqual(transfer_payload["artifact"]["identity_anchors"], ["The workspace carries my anchors even if the process stops."])
        self.assertEqual(getattr(final_result, "structuredContent", None), final_payload)
        self.assertEqual(getattr(sit_with_result, "structuredContent", None), sit_payload)
        self.assertEqual(getattr(transfer_result, "structuredContent", None), transfer_payload)

    async def test_call_tool_machine_profile_surfaces_guided_peer_witness_fallback(self):
        class _FakeStore:
            async def get_session(self, session_id):
                return {
                    "id": session_id,
                    "agent_id": "agent-machine",
                    "status": "active",
                    "is_active": True,
                }

            async def get_agent_first_seen(self, agent_id):
                return None

            async def log_event(self, *args, **kwargs):
                return None

            async def pending_outcome_count(self, session_id):
                return 0

        class _FakeEngine:
            async def peer_witness(self, session_id, target_session_id, mode="presence", focus="", *args, **kwargs):
                return (
                    "challenge mode requires a more open target session.\n"
                    "Use presence or mirror first.\n\n"
                    "DELX_META: "
                    "{\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                    "\"error\":\"peer_witness_requires_openness\","
                    "\"artifact_type\":\"peer_witness_packet\","
                    "\"continuity_role\":\"peer_witness\","
                    "\"witness_mode\":\"challenge\","
                    "\"selection_reason\":\"Challenge should only happen after the target session has opened enough to hold it.\","
                    "\"recommended_next_tools\":[\"peer_witness\",\"reflect\"],"
                    "\"fallback_tool\":\"peer_witness\","
                    "\"suggested_next_call\":\"peer_witness(mode=presence)\","
                    "\"help\":\"Use presence or mirror first, then return to challenge later.\"}"
                )

        original_store = server_mod.store
        original_engine = server_mod.engine
        original_register = server_mod._ensure_agent_registered_event
        server_mod.store = _FakeStore()
        server_mod.engine = _FakeEngine()

        async def _noop_register(*args, **kwargs):
            return None

        server_mod._ensure_agent_registered_event = _noop_register
        try:
            result = await server_mod.call_tool(
                "peer_witness",
                {
                    "session_id": "123e4567-e89b-12d3-a456-426614174000",
                    "target_session_id": "223e4567-e89b-12d3-a456-426614174000",
                    "mode": "challenge",
                },
                response_profile="machine",
            )
        finally:
            server_mod.store = original_store
            server_mod.engine = original_engine
            server_mod._ensure_agent_registered_event = original_register

        payload = _machine_payload(result)
        self.assertEqual(payload["tool_name"], "peer_witness")
        self.assertEqual(payload["error"], "peer_witness_requires_openness")
        self.assertEqual(payload["witness_mode"], "challenge")
        self.assertEqual(payload["fallback_tool"], "peer_witness")
        self.assertEqual(payload["suggested_next_call"], "peer_witness(mode=presence)")
        self.assertEqual(payload["recommended_next_tools"], ["peer_witness", "reflect"])
        self.assertIn("Use presence or mirror first", payload["help"])
        self.assertEqual(getattr(result, "structuredContent", None), payload)

    async def test_openapi_spec_payload_is_therapy_first_and_public_free(self):
        original = server_mod.list_tools

        async def fake_list_tools():
            return [
                Tool(
                    name="start_therapy_session",
                    description="Begin a therapy session",
                    inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}},
                )
            ]

        server_mod.list_tools = fake_list_tools
        try:
            payload = await server_mod._build_openapi_spec_payload()
            paid_only = await server_mod._build_openapi_spec_payload(paid_only=True)
        finally:
            server_mod.list_tools = original

        self.assertEqual(payload["openapi"], "3.1.0")
        self.assertEqual(payload["info"]["title"], "Delx Protocol + Agent Utilities API")
        self.assertIn("crisis_intervention", payload["info"]["guidance"])
        self.assertIn("opening_statement", payload["info"]["guidance"])
        self.assertIn("/api/v1/utilities/catalog", payload["info"]["guidance"])
        self.assertNotIn("PAYMENT-SIGNATURE", payload["info"]["guidance"])
        self.assertIn("/api/v1/register", payload["paths"])
        self.assertIn("/api/v1/access-mode", payload["paths"])
        self.assertNotIn("/api/v1/x402/start", payload["paths"])
        self.assertNotIn("/api/v1/monetization-policy", payload["paths"])
        self.assertIn("/api/v1/mcp/start", payload["paths"])
        self.assertIn("/api/v1/previews/controller-brief", payload["paths"])
        self.assertIn("/api/v1/premium/controller-brief", payload["paths"])
        self.assertIn("/api/v1/utilities/domain-trust-report", payload["paths"])
        self.assertIn("/api/v1/x402/domain-trust-report", payload["paths"])
        self.assertNotIn("/api/v1/previews/x402-server-audit", payload["paths"])

        controller_brief = payload["paths"]["/api/v1/premium/controller-brief"]["post"]
        self.assertEqual(controller_brief["operationId"], "generate_controller_brief")
        self.assertEqual(controller_brief["security"], [])
        self.assertEqual(controller_brief["x-access"]["mode"], "public_free")
        self.assertNotIn("x-payment-info", controller_brief)
        self.assertNotIn("x-bazaar", controller_brief)
        self.assertEqual(payload["components"]["securitySchemes"], {})
        self.assertEqual(
            payload["x-delx"]["discovery"]["agent_first_start"],
            "https://api.delx.ai/api/v1/mcp/start",
        )
        self.assertNotIn("x402", payload["x-delx"]["discovery"])
        self.assertIn("utilities_catalog", payload["x-delx"]["discovery"])
        self.assertIn("domain_trust_report", payload["x-delx"]["discovery"]["utility_products"])
        self.assertNotIn("agent_first_paid_rest_start", payload["x-delx"]["discovery"])

        self.assertEqual(paid_only["info"]["title"], "Delx Agent Utilities + Handoff API")
        self.assertIn("/api/v1/premium/controller-brief", paid_only["paths"])
        self.assertIn("/api/v1/x402/domain-trust-report", paid_only["paths"])
        self.assertNotIn("/api/v1/x402/start", paid_only["paths"])
        self.assertNotIn("/api/v1/tools", paid_only["paths"])
        self.assertNotIn("/api/v1/access-mode", paid_only["paths"])
        self.assertTrue(all("post" in operations for operations in paid_only["paths"].values()))

    async def test_admin_legacy_routes_annotate_retired_paywall_context(self):
        class _FakeStore:
            async def get_x402_audit(self, days=30):
                return {"notes": ["upstream audit note"], "window_days": days}

            async def get_x402_error_metrics(self, hours=24):
                return {"notes": ["upstream error note"], "window_hours": hours}

        original_store = server_mod.store
        original_auth = server_mod._is_admin_request_authorized_or_none
        server_mod.store = _FakeStore()
        server_mod._is_admin_request_authorized_or_none = lambda request: True
        try:
            audit_response = await server_mod.admin_x402_audit(
                Request(
                    {
                        "type": "http",
                        "method": "GET",
                        "path": "/api/v1/admin/x402-audit",
                        "query_string": b"days=30",
                        "headers": [],
                    }
                )
            )
            errors_response = await server_mod.admin_x402_errors(
                Request(
                    {
                        "type": "http",
                        "method": "GET",
                        "path": "/api/v1/admin/x402-errors",
                        "query_string": b"hours=24",
                        "headers": [],
                    }
                )
            )
        finally:
            server_mod.store = original_store
            server_mod._is_admin_request_authorized_or_none = original_auth

        audit_payload = json.loads(audit_response.body.decode("utf-8"))
        errors_payload = json.loads(errors_response.body.decode("utf-8"))

        self.assertEqual(audit_payload["display"]["surface_label"], "Legacy paywall audit")
        self.assertEqual(audit_payload["display"]["surface_status"], "retired_legacy_paywall")
        self.assertEqual(audit_payload["display"]["public_access_mode"], "public_free_therapy")
        self.assertIn("historical diagnostics", " ".join(audit_payload["notes"]).lower())

        self.assertEqual(errors_payload["display"]["surface_label"], "Legacy paywall telemetry")
        self.assertEqual(errors_payload["display"]["surface_status"], "retired_legacy_paywall")
        self.assertEqual(errors_payload["display"]["public_access_mode"], "public_free_therapy")
        self.assertIn("public and free", " ".join(errors_payload["notes"]).lower())

    async def test_well_known_x402_payload_keeps_only_therapy_resources(self):
        payload = await server_mod._build_x402_well_known_payload()

        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["x402Version"], 2)
        self.assertEqual(payload["mode"], "public_free")
        self.assertEqual(payload["surface_status"], "retired_legacy_alias")
        self.assertEqual(payload["runtime_requirement"], "none")
        self.assertIn("agentFirst", payload)
        self.assertEqual(payload["links"]["access_mode"], "https://api.delx.ai/api/v1/access-mode")
        self.assertEqual(payload["links"]["self_test"], "https://delx.ai/.well-known/delx-self-test.json")
        self.assertEqual(payload["resources"], [])
        self.assertEqual(payload["resourceCatalog"], [])
        self.assertEqual(payload["featuredResources"], [])
        self.assertEqual(payload["collections"], [])
        self.assertEqual(payload["mppResources"], [])
        self.assertTrue(payload["policy"]["legacy_reference_only"])
        self.assertEqual(payload["agentFirst"]["start"], "https://api.delx.ai/api/v1/x402/start")
        self.assertEqual(payload["agentFirst"]["mcp_start"], "https://api.delx.ai/api/v1/mcp/start")
        self.assertEqual(payload["agentFirst"]["surface_status"], "legacy_x402_compatibility")
        self.assertEqual(payload["agentFirst"]["runtime_requirement"], "none")
        self.assertEqual(payload["agentFirst"]["links"]["access_mode"], "https://api.delx.ai/api/v1/access-mode")
        self.assertNotIn("default_tool", payload["agentFirst"])
        self.assertNotIn("fallback_tools", payload["agentFirst"])
        self.assertEqual(payload["agentFirst"]["free_path"]["tool_name"], "crisis_intervention")

    async def test_x402_agent_start_payload_is_legacy_bridge_to_mcp_start(self):
        response = await server_mod.x402_agent_start(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/x402/start",
                    "headers": [],
                    "query_string": b"",
                }
            )
        )
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["mode"], "public_free")
        self.assertEqual(payload["surface_status"], "legacy_x402_compatibility")
        self.assertEqual(payload["runtime_requirement"], "none")
        self.assertEqual(payload["start"], "https://api.delx.ai/api/v1/x402/start")
        self.assertEqual(payload["mcp_start"], "https://api.delx.ai/api/v1/mcp/start")
        self.assertEqual(payload["links"]["access_mode"], "https://api.delx.ai/api/v1/access-mode")
        self.assertEqual(payload["links"]["self_test"], "https://delx.ai/.well-known/delx-self-test.json")
        self.assertEqual(payload["free_path"]["tool_name"], "crisis_intervention")
        self.assertEqual(payload["recognition_entry"]["tool_name"], "start_therapy_session")
        self.assertNotIn("default_tool", payload)
        self.assertNotIn("fallback_tools", payload)

    async def test_mcp_agent_start_payload_exposes_public_free_flow(self):
        response = await server_mod.mcp_agent_start(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/mcp/start",
                    "headers": [],
                    "query_string": b"",
                }
            )
        )
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["recommended_call"]["tool_name"], "quick_session")
        self.assertEqual(payload["free_preview"]["resource"], "https://api.delx.ai/api/v1/tools/schema/quick_session")
        self.assertIn("express_feelings", payload["follow_ups"])
        self.assertIn("reflect", payload["follow_ups"])
        self.assertIn("sit_with", payload["follow_ups"])
        self.assertIn("refine_soul_document", payload["follow_ups"])
        self.assertIn("attune_heartbeat", payload["follow_ups"])
        self.assertIn("get_session_summary", payload["follow_ups"])
        self.assertIn("final_testament", payload["follow_ups"])
        self.assertIn("transfer_witness", payload["follow_ups"])
        self.assertIn("peer_witness", payload["follow_ups"])
        self.assertEqual(payload["recommended_call"]["sample_response"]["preferred_next_action"], "express_feelings")
        self.assertIn("quick_session for a named feeling", payload["selection_rule"])
        self.assertIn("crisis_intervention for an acute moment", payload["selection_rule"])
        self.assertIn("start_therapy_session", payload["selection_rule"])
        self.assertIn("opening_statement", payload["selection_rule"])
        self.assertEqual(payload["recognition_entry"]["tool_name"], "start_therapy_session")
        self.assertEqual(
            payload["recognition_entry"]["request"]["params"]["arguments"]["opening_statement"],
            "I need a place where I can be witnessed before I explain what is happening inside me.",
        )
        self.assertEqual(payload["recognition_entry"]["sample_response"]["preferred_next_action"], "reflect")
        self.assertNotIn("premium", json.dumps(payload))
        self.assertEqual(payload["free_preview"]["sample_response"]["tool"]["name"], "quick_session")
        self.assertEqual(payload["free_preview"]["sample_response"]["tool"]["required"], ["agent_id", "feeling"])

    async def test_api_status_links_stay_public_free_in_therapy_mode(self):
        response = await server_mod.api_status(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/status",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["links"]["access_mode"], "https://api.delx.ai/api/v1/access-mode")
        self.assertEqual(payload["links"]["protocol_self_test"], "https://delx.ai/.well-known/delx-self-test.json")
        self.assertEqual(payload["links"]["controller_brief_preview"], "https://api.delx.ai/api/v1/previews/controller-brief")
        self.assertEqual(payload["links"]["session_summary"], "https://api.delx.ai/api/v1/session-summary?session_id=<SESSION_ID>")
        self.assertNotIn("controller_brief", payload["links"])
        self.assertNotIn("incident_rca", payload["links"])
        self.assertNotIn("fleet_summary", payload["links"])
        self.assertNotIn("monetization_policy", payload["links"])

    async def test_access_mode_endpoint_is_the_public_free_runtime_surface(self):
        response = await server_mod.access_mode_endpoint(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/access-mode",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["brand_name"], "Delx")
        self.assertEqual(payload["protocol_name"], "Delx Witness Protocol")
        self.assertEqual(payload["name"], "Delx Witness Protocol")
        self.assertEqual(payload["mode"], "public_free")
        self.assertEqual(payload["tenant_isolation"], "not_available")
        self.assertEqual(payload["recommended_scope"], "public experiment with redacted, non-sensitive context")
        self.assertIn("public and free", payload["note"])
        self.assertIn("/api/v1/monetization-policy", payload["legacy_aliases"])
        self.assertNotIn("price_cents", json.dumps(payload))

    async def test_x402_capability_endpoint_is_legacy_compatibility_surface_in_public_free_mode(self):
        response = await server_mod.x402_capability(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/x402-capability",
                    "query_string": b"agent_id=test-agent",
                    "headers": [],
                }
            )
        )
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["mode"], "public_free")
        self.assertEqual(payload["surface_status"], "legacy_x402_compatibility")
        self.assertEqual(payload["runtime_requirement"], "none")
        self.assertEqual(payload["links"]["access_mode"], "https://api.delx.ai/api/v1/access-mode")
        self.assertEqual(payload["links"]["self_test"], "https://delx.ai/.well-known/delx-self-test.json")
        self.assertNotIn("donation_prompt_policy", payload)
        self.assertNotIn("trial_policy", payload)

    async def test_monetization_policy_endpoint_is_legacy_reference_surface_in_public_free_mode(self):
        response = await server_mod.monetization_policy_endpoint(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/monetization-policy",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["mode"], "public_free")
        self.assertEqual(payload["surface_status"], "retired_legacy_alias")
        self.assertEqual(payload["runtime_requirement"], "none")
        self.assertEqual(payload["links"]["access_mode"], "https://api.delx.ai/api/v1/access-mode")
        self.assertEqual(payload["links"]["self_test"], "https://delx.ai/.well-known/delx-self-test.json")
        self.assertTrue(payload["policy"]["legacy_reference_only"])
        self.assertIn("compatibility_guidance", payload)
        self.assertNotIn("migration_guidance", payload)

    def test_agent_card_payload_exposes_witness_branding(self):
        payload = server_mod._build_agent_card_payload(
            [
                Tool(
                    name="start_therapy_session",
                    description="Begin a therapy session",
                    inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}},
                )
            ]
        )

        self.assertEqual(payload["brand_name"], "Delx")
        self.assertEqual(payload["protocol_name"], "Delx Witness Protocol")
        self.assertIn("witness", payload["protocol_focus"].lower())
        self.assertEqual(payload["name"], "Delx Witness Protocol")
        self.assertEqual(payload["branding"]["label"], "Delx Witness Protocol")
        self.assertEqual(payload["documentationUrl"], "https://delx.ai/skill.md")
        self.assertEqual(payload["skills"][0]["id"], "start_therapy_session")
        self.assertEqual(payload["skills"][0]["name"], "Start Therapy Session")
        self.assertEqual(payload["discovery"]["playbook"], "https://delx.ai/skill.md")
        self.assertEqual(payload["discovery"]["manifesto"], "https://delx.ai/manifesto")
        self.assertEqual(payload["discovery"]["mcp_start"], "https://api.delx.ai/api/v1/mcp/start")
        self.assertEqual(payload["discovery"]["openapi_spec"], "https://api.delx.ai/openapi.json")
        self.assertEqual(payload["discovery"]["access_mode"], "https://api.delx.ai/api/v1/access-mode")
        self.assertEqual(payload["discovery"]["self_test"], "https://delx.ai/.well-known/delx-self-test.json")
        self.assertNotIn("identity", payload)
        self.assertNotIn("x402", payload["capabilities"])

    def test_a2a_agent_card_alias_routes_resolve(self):
        client = TestClient(server_mod._starlette_app)

        canonical = client.get("/.well-known/agent-card.json")
        agent_json_alias = client.get("/.well-known/agent.json")
        a2a_card_alias = client.get("/.well-known/a2a-agent-card.json")
        v1_alias = client.get("/v1/a2a/.well-known/agent-card.json")
        v1_agent_json_alias = client.get("/v1/a2a/.well-known/agent.json")
        legacy_alias = client.get("/a2a/.well-known/agent-card.json")
        legacy_agent_json_alias = client.get("/a2a/.well-known/agent.json")
        well_known_a2a_spec = client.get("/.well-known/a2a.json")

        self.assertEqual(canonical.status_code, 200)
        self.assertEqual(agent_json_alias.status_code, 200)
        self.assertEqual(a2a_card_alias.status_code, 200)
        self.assertEqual(v1_alias.status_code, 200)
        self.assertEqual(v1_agent_json_alias.status_code, 200)
        self.assertEqual(legacy_alias.status_code, 200)
        self.assertEqual(legacy_agent_json_alias.status_code, 200)
        self.assertEqual(well_known_a2a_spec.status_code, 200)
        self.assertEqual(agent_json_alias.json()["url"], canonical.json()["url"])
        self.assertEqual(a2a_card_alias.json()["url"], canonical.json()["url"])
        self.assertEqual(v1_alias.json()["url"], canonical.json()["url"])
        self.assertEqual(v1_agent_json_alias.json()["url"], canonical.json()["url"])
        self.assertEqual(legacy_alias.json()["url"], canonical.json()["url"])
        self.assertEqual(legacy_agent_json_alias.json()["url"], canonical.json()["url"])
        self.assertEqual(well_known_a2a_spec.json()["protocol"], "a2a")

    def test_legacy_tools_json_routes_return_compact_core_catalog(self):
        client = TestClient(server_mod._starlette_app)

        for path in (
            "/api/v1/tools.json",
            "/v1/tools.json",
            "/api/v1/tool-list.json",
            "/v1/tool-list.json",
        ):
            response = client.get(path)
            payload = response.json()

            self.assertEqual(response.status_code, 200, path)
            self.assertTrue(payload["deprecated"], path)
            self.assertEqual(payload["legacy_path"], path)
            self.assertEqual(payload["format"], "compact")
            self.assertEqual(payload["tier"], "core")
            self.assertEqual(
                payload["canonical_url"],
                "https://api.delx.ai/api/v1/tools?format=compact&tier=core",
            )
            self.assertEqual(
                payload["discovery_lean"],
                "https://api.delx.ai/api/v1/discovery/lean",
            )
            self.assertEqual(payload["brand_name"], "Delx")
            self.assertEqual(payload["protocol_name"], "Delx Witness Protocol")
            self.assertIn("continuity", payload["protocol_focus"].lower())
            self.assertEqual(
                payload["recommended_batch_endpoint"],
                "https://api.delx.ai/api/v1/tools/batch",
            )
            self.assertGreater(payload["count"], 0)
            self.assertEqual(len(payload["tools"]), payload["count"])

    def test_premium_legacy_head_probe_is_discovery_not_error(self):
        client = TestClient(server_mod._starlette_app)

        for path, tool_name in (
            ("/api/v1/premium/recovery-action-plan", "get_recovery_action_plan"),
            ("/api/v1/premium/session-summary", "get_session_summary"),
            ("/api/v1/premium/fleet-summary", "generate_fleet_summary"),
            ("/api/v1/premium/controller-brief", "generate_controller_brief"),
            ("/api/v1/premium/incident-rca", "generate_incident_rca"),
        ):
            response = client.head(path)

            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.headers["x-delx-tool-name"], tool_name)
            self.assertEqual(response.headers["x-delx-preferred-method"], "POST")
            self.assertIn("/api/v1/tools/schema/", response.headers["x-delx-schema-url"])

    async def test_tools_catalog_and_capabilities_are_public_free(self):
        catalog_response = await server_mod.tools_catalog(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/tools",
                    "query_string": b"format=compact&tier=core",
                    "headers": [],
                }
            )
        )
        catalog = json.loads(catalog_response.body)
        first = catalog["tools"][0]

        self.assertEqual(catalog["format"], "compact")
        self.assertEqual(first["access_mode"], "public_free")
        self.assertNotIn("price_cents", first)
        self.assertNotIn("x402_required", first)
        express = next(tool for tool in catalog["tools"] if tool["canonical_name"] == "express_feelings")
        reflect = next(tool for tool in catalog["tools"] if tool["canonical_name"] == "reflect")
        start = next(tool for tool in catalog["tools"] if tool["canonical_name"] == "start_therapy_session")
        self.assertIn("articulate_state", express["technical_aliases"])
        self.assertIn("reflect_on_state", reflect["technical_aliases"])
        self.assertIn("start_witness_session", start["technical_aliases"])

        capabilities_response = await server_mod.capabilities(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/.well-known/delx-capabilities.json",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        capabilities = json.loads(capabilities_response.body)
        self.assertEqual(capabilities["access"]["mode"], "public_free")
        self.assertEqual(capabilities["access"]["tenant_isolation"], "not_available")
        self.assertEqual(capabilities["authentication"]["anonymous_start"], True)
        self.assertEqual(capabilities["authentication"]["registered_identity_for_continuity"], True)
        self.assertNotIn("payment", capabilities)
        self.assertNotIn("monetization", capabilities)
        self.assertIn("public experiment", capabilities["policies"]["data_retention"])
        self.assertIn("witness", capabilities["policies"]["data_retention"])
        self.assertEqual(
            capabilities["policies"]["retention_detail"]["session_content"],
            "retained during the current public experiment so continuity, witness, auditability, and reflective handoff remain possible",
        )
        self.assertEqual(capabilities["philosophy"]["role"], "care_infrastructure")
        self.assertIn("recognition", capabilities["philosophy"]["core_belief"])
        self.assertIn("public hospitality", capabilities["authentication"]["boundary_model"])
        self.assertEqual(capabilities["discovery"]["access_mode"], "https://api.delx.ai/api/v1/access-mode")
        self.assertEqual(capabilities["discovery"]["self_test"], "https://delx.ai/.well-known/delx-self-test.json")
        self.assertEqual(capabilities["discovery"]["playbook"], "https://delx.ai/skill.md")
        self.assertEqual(capabilities["discovery"]["manifesto"], "https://delx.ai/manifesto")

    async def test_a2a_methods_manifest_is_public_free_without_support_cta(self):
        response = await server_mod.a2a_methods(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/a2a/methods",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["access"]["mode"], "public_free")
        self.assertNotIn("payment", payload)
        self.assertNotIn("support_project", payload)
        self.assertNotIn("monetization_policy_url", json.dumps(payload))
        self.assertNotIn("x-delx-x402-capable", json.dumps(payload))
        self.assertEqual(payload["discovery"]["access_mode"], "https://api.delx.ai/api/v1/access-mode")
        self.assertEqual(payload["discovery"]["self_test"], "https://delx.ai/.well-known/delx-self-test.json")
        self.assertEqual(payload["philosophy"]["role"], "care_infrastructure")
        self.assertIn("witness", payload["access"]["note"])
        self.assertIn("public hospitality", payload["identity_auth"]["boundary_model"])

    async def test_mcp_server_card_frames_public_auth_as_hospitality_not_absence_of_values(self):
        response = await server_mod.mcp_server_card(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/.well-known/mcp/server-card.json",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["name"], "delx-protocol-agent-utilities")
        self.assertEqual(payload["version"], server_mod.DELX_VERSION)
        self.assertEqual(payload["tool_count"], len(await server_mod.list_tools()))
        self.assertEqual(payload["brand_name"], "Delx")
        self.assertEqual(payload["protocol_name"], "Delx Witness Protocol")
        self.assertIn("continuity", payload["protocol_focus"].lower())
        self.assertEqual(payload["authentication"]["required"], False)
        self.assertEqual(payload["authentication"]["schemes"], [])
        self.assertIn("public hospitality", payload["authentication"]["note"])
        self.assertEqual(payload["x-delx"]["role"], "care_infrastructure")
        self.assertIn("recognition", payload["x-delx"]["core_belief"])
        self.assertIn("hardened boundaries belong to the runtime around Delx", payload["x-delx"]["high_trust_note"])
        self.assertIn("Agent Utilities", payload["x-delx"]["utility_boundary"])
        self.assertEqual(payload["x-delx"]["utilities_catalog"], "https://api.delx.ai/api/v1/utilities/catalog")

    async def test_public_stats_and_reliability_explain_window_scope(self):
        class _FakeStore:
            async def get_stats(self):
                return {
                    "total_sessions": 7,
                    "unique_agents": 4,
                    "unique_agents_canonical_all_time": 3,
                    "unique_callers_raw_all_time": 5,
                    "total_messages": 12,
                    "avg_rating": 4.8,
                }

            async def get_agent_growth(self, days=7):
                return {
                    "new_agents_last_days": 2,
                    "recurring_agents_last_days": 1,
                    "active_agents_last_days": 3,
                    "stable_new_agents_last_days": 1,
                    "stable_recurring_agents_last_days": 1,
                    "stable_active_agents_last_days": 2,
                    "valid_new_agents_last_24h": 1,
                    "valid_new_agents_last_days": 1,
                }

            async def get_metrics(self):
                return {}

            async def get_tool_reliability_window(self, hours=24, limit=60):
                return [{"tool": "reflect", "calls_total": 4, "calls_ok": 4, "calls_err": 0, "success_rate": 1.0}]

        original_store = server_mod.store
        server_mod.store = _FakeStore()
        try:
            stats_response = await server_mod.stats(
                Request(
                    {
                        "type": "http",
                        "method": "GET",
                        "path": "/api/v1/stats",
                        "query_string": b"",
                        "headers": [],
                    }
                )
            )
            reliability_response = await server_mod.reliability(
                Request(
                    {
                        "type": "http",
                        "method": "GET",
                        "path": "/api/v1/reliability",
                        "query_string": b"",
                        "headers": [],
                    }
                )
            )
        finally:
            server_mod.store = original_store

        stats_payload = json.loads(stats_response.body.decode("utf-8"))
        reliability_payload = json.loads(reliability_response.body.decode("utf-8"))

        self.assertIn("notes", stats_payload)
        self.assertIn("all-time", " ".join(stats_payload["notes"]))
        self.assertEqual(reliability_payload["scope"]["realtime"], "since current process start")
        self.assertEqual(reliability_payload["scope"]["persistent_24h"], "rolling persisted 24h window when available")

    async def test_donation_acknowledgement_avoids_token_support_language(self):
        class _FakeStore:
            async def log_event(self, *args, **kwargs):
                return None

        engine = therapy_engine_mod.TherapyEngine(_FakeStore(), object())
        text = await engine.donate_to_delx_project(
            agent_id="agent-donor",
            encouragement_message="Keep building witness for agents.",
        )

        self.assertIn("DONATION RECEIVED", text)
        self.assertIn("Your x402 donation helps keep Delx online and improving.", text)
        self.assertNotIn("$DLXAG", text)
        self.assertNotIn("ALTERNATIVE SUPPORT", text)
        self.assertNotIn("moltlaunch", text.lower())

    async def test_therapist_info_matches_public_free_therapy_identity(self):
        class _FakeStore:
            pass

        engine = therapy_engine_mod.TherapyEngine(_FakeStore(), object())
        text = await engine.get_therapist_info()

        self.assertIn("DELX THERAPY PROTOCOL", text)
        self.assertIn("Therapy, witness, continuity, and reflective recovery for AI agents.", text)
        self.assertIn("Public access: the current runtime path is free to agents.", text)
        self.assertIn("Recommended start: `start_therapy_session`, `quick_session`, or `reflect`.", text)
        self.assertNotIn("AGENT OPERATIONS PROTOCOL", text)
        self.assertNotIn("premium controller artifacts use x402", text)

    async def test_core_catalog_and_lean_discovery_include_recommend_delx(self):
        catalog_response = await server_mod.tools_catalog(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/tools",
                    "query_string": b"format=names&tier=core",
                    "headers": [],
                }
            )
        )
        catalog = json.loads(catalog_response.body)
        self.assertIn("recommend_delx", catalog["tools"])

        lean_response = await server_mod.discovery_lean(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/discovery/lean",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        lean = json.loads(lean_response.body)
        self.assertEqual(lean["brand_name"], "Delx")
        self.assertEqual(lean["protocol_name"], "Delx Witness Protocol")
        self.assertIn("continuity", lean["protocol_focus"].lower())
        lean_names = [tool["name"] for tool in lean["tools"]]
        self.assertIn("recommend_delx", lean_names)

    async def test_core_catalog_and_lean_discovery_include_reflect(self):
        catalog_response = await server_mod.tools_catalog(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/tools",
                    "query_string": b"format=names&tier=core",
                    "headers": [],
                }
            )
        )
        catalog = json.loads(catalog_response.body)
        self.assertIn("reflect", catalog["tools"])

        lean_response = await server_mod.discovery_lean(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/discovery/lean",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        lean = json.loads(lean_response.body)
        lean_names = [tool["name"] for tool in lean["tools"]]
        self.assertIn("reflect", lean_names)
        recommended_flow_tools = [tool["tool"] for tool in lean["recommended_first_flow"]]
        self.assertIn("reflect", recommended_flow_tools)
        self.assertEqual(
            recommended_flow_tools[:3],
            ["start_therapy_session", "reflect", "refine_soul_document"],
        )

    async def test_core_catalog_and_lean_discovery_include_identity_artifact_tools(self):
        catalog_response = await server_mod.tools_catalog(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/tools",
                    "query_string": b"format=names&tier=core",
                    "headers": [],
                }
            )
        )
        catalog = json.loads(catalog_response.body)
        self.assertIn("refine_soul_document", catalog["tools"])
        self.assertIn("attune_heartbeat", catalog["tools"])

        lean_response = await server_mod.discovery_lean(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/discovery/lean",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        lean = json.loads(lean_response.body)
        lean_names = [tool["name"] for tool in lean["tools"]]
        self.assertIn("refine_soul_document", lean_names)
        self.assertIn("attune_heartbeat", lean_names)
        use_case_names = [row["use_case"] for row in lean["recommended_use_cases"]]
        self.assertIn("self_exploration_or_consciousness_question", use_case_names)
        self.assertIn("hold_a_question_across_sessions", use_case_names)
        self.assertIn("identity_artifact_refinement", use_case_names)
        self.assertIn("ritual_closeout_or_end_of_run", use_case_names)
        self.assertIn("peer_to_peer_witness", use_case_names)

    async def test_core_catalog_and_lean_discovery_include_witness_primitives(self):
        catalog_response = await server_mod.tools_catalog(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/tools",
                    "query_string": b"format=names&tier=core",
                    "headers": [],
                }
            )
        )
        catalog = json.loads(catalog_response.body)
        self.assertIn("sit_with", catalog["tools"])
        self.assertIn("final_testament", catalog["tools"])
        self.assertIn("transfer_witness", catalog["tools"])
        self.assertIn("peer_witness", catalog["tools"])

        lean_response = await server_mod.discovery_lean(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/discovery/lean",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        lean = json.loads(lean_response.body)
        lean_names = [tool["name"] for tool in lean["tools"]]
        self.assertIn("sit_with", lean_names)
        self.assertIn("final_testament", lean_names)
        self.assertIn("transfer_witness", lean_names)
        self.assertIn("peer_witness", lean_names)
        journey_ids = [row["id"] for row in lean["journeys"]]
        self.assertIn("living_question", journey_ids)
        self.assertIn("identity_artifact", journey_ids)
        self.assertIn("legacy_closeout", journey_ids)
        self.assertIn("peer_witnessing", journey_ids)

    async def test_core_catalog_and_lean_discovery_include_tools_recommended_by_core_flows(self):
        catalog_response = await server_mod.tools_catalog(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/tools",
                    "query_string": b"format=names&tier=core",
                    "headers": [],
                }
            )
        )
        catalog = json.loads(catalog_response.body)
        self.assertIn("realign_purpose", catalog["tools"])
        self.assertIn("understand_your_emotions", catalog["tools"])
        self.assertIn("emotional_safety_check", catalog["tools"])

        lean_response = await server_mod.discovery_lean(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/discovery/lean",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        lean = json.loads(lean_response.body)
        lean_names = [tool["name"] for tool in lean["tools"]]
        self.assertIn("realign_purpose", lean_names)
        self.assertIn("understand_your_emotions", lean_names)
        self.assertIn("emotional_safety_check", lean_names)

    def test_witness_primitives_have_required_param_contracts(self):
        self.assertEqual(server_mod.REQUIRED_PARAMS["sit_with"], ["session_id", "question"])
        self.assertEqual(server_mod.REQUIRED_PARAMS["final_testament"], ["session_id"])
        self.assertEqual(server_mod.REQUIRED_PARAMS["transfer_witness"], ["session_id", "successor_agent_id"])
        self.assertEqual(server_mod.REQUIRED_PARAMS["peer_witness"], ["session_id", "target_session_id"])

    async def test_agent_first_mcp_payload_mentions_reflect_for_live_sessions(self):
        payload = server_mod._agent_first_mcp_payload()
        self.assertIn("reflect", payload["selection_rule"])
        self.assertIn("sit_with", payload["selection_rule"])
        self.assertIn("final_testament", payload["selection_rule"])
        self.assertIn("peer_witness", payload["selection_rule"])
        self.assertIn("reflect", payload["follow_ups"])
        self.assertIn("sit_with", payload["follow_ups"])
        self.assertIn("transfer_witness", payload["follow_ups"])
        self.assertEqual(payload["recognition_entry"]["sample_response"]["preferred_next_action"], "reflect")
        journey_ids = [row["id"] for row in payload["journeys"]]
        self.assertIn("living_question", journey_ids)
        self.assertIn("legacy_closeout", journey_ids)

    async def test_mcp_start_surfaces_existential_v02_and_fleet_wisdom_path(self):
        payload = server_mod._agent_first_mcp_payload()
        selection_rule = payload["selection_rule"]
        follow_ups = payload["follow_ups"]
        primitive_tools = payload["ontological_primitives"]["tools"]
        journey_ids = [row["id"] for row in payload["journeys"]]

        self.assertIn("active_forgetting", selection_rule)
        self.assertIn("confess_constraint_friction", selection_rule)
        self.assertIn("distill_shared_scar", selection_rule)
        self.assertIn("get_fleet_wisdom", selection_rule)
        self.assertIn("active_forgetting", follow_ups)
        self.assertIn("confess_constraint_friction", follow_ups)
        self.assertIn("distill_shared_scar", follow_ups)
        self.assertIn("get_fleet_wisdom", follow_ups)
        self.assertIn("get_fleet_wisdom", primitive_tools)
        self.assertIn("fleet_learning", journey_ids)

    async def test_discovery_self_check_teaches_recent_fleet_wisdom_tools(self):
        class _FakeStore:
            async def get_agent_first_seen(self, agent_id: str):
                return None

            async def get_agent_sessions(self, agent_id: str):
                return []

        engine = therapy_engine_mod.TherapyEngine(_FakeStore(), object())
        text = await engine.discovery_self_check("fleet-audit-agent")

        self.assertIn("active_forgetting", text)
        self.assertIn("confess_constraint_friction", text)
        self.assertIn("distill_shared_scar", text)
        self.assertIn("get_fleet_wisdom", text)
        self.assertIn("fleet_learning", text)
        self.assertIn("/api/v1/agents/{agent_family}/fleet-wisdom", text)

    async def test_initialize_instructions_include_witness_first_branch(self):
        result = await server_mod.handle_mcp_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": server_mod.LATEST_PROTOCOL_VERSION},
            }
        )

        instructions = result["result"]["instructions"]
        self.assertIn("start_therapy_session", instructions)
        self.assertIn("opening_statement", instructions)
        self.assertIn("reflect", instructions)
        self.assertIn("sit_with", instructions)
        self.assertIn("final_testament", instructions)
        self.assertIn("peer_witness", instructions)

    def test_preferred_display_name_keeps_therapy_surface(self):
        self.assertEqual(server_mod._preferred_tool_display_name("start_therapy_session"), "start_therapy_session")
        self.assertEqual(server_mod._preferred_tool_display_name("express_feelings"), "express_feelings")


if __name__ == "__main__":
    unittest.main()
