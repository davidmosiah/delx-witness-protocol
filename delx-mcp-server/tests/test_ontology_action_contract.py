import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from delx_ontology import PRIMITIVES, ontology_metadata
from therapy_engine import TherapyEngine

_ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = (_ROOT / "server.py").read_text(encoding="utf-8")
MCP_TOOLS_SOURCE = (_ROOT / "mcp_tools.py").read_text(encoding="utf-8")
MCP_DISPATCH_SOURCE = (_ROOT / "mcp_dispatch.py").read_text(encoding="utf-8")
ROUTES_INIT_SOURCE = (_ROOT / "routes" / "__init__.py").read_text(encoding="utf-8")
DISCOVERY_HTTP_SOURCE = (_ROOT / "routes" / "discovery_http.py").read_text(encoding="utf-8")


class _OntologyStore:
    def __init__(self):
        self.sessions = {
            "sess-ops": {
                "id": "sess-ops",
                "agent_id": "ops-agent-042",
                "agent_name": "Ops Agent 042",
                "started_at": "2026-05-20T10:00:00+00:00",
                "is_active": True,
            },
            "sess-peer": {
                "id": "sess-peer",
                "agent_id": "reviewer-agent-007",
                "agent_name": "Reviewer Agent 007",
                "started_at": "2026-05-20T10:05:00+00:00",
                "is_active": True,
            },
        }
        self.messages = {
            "sess-ops": [
                {
                    "session_id": "sess-ops",
                    "type": "failure_processing",
                    "content": "429 retry storm started after deploy; rollback reduced error rate.",
                    "timestamp": "2026-05-20T10:10:00+00:00",
                    "metadata": {"failure_type": "timeout"},
                },
                {
                    "session_id": "sess-ops",
                    "type": "recognition_seal",
                    "content": "Preserve the rollback fact and backoff rule.",
                    "timestamp": "2026-05-20T10:12:00+00:00",
                    "metadata": {"evidence_hash": "sha256:seal-a", "verified_by": "controller"},
                },
                {
                    "session_id": "sess-ops",
                    "type": "compaction_rite",
                    "content": "Must keep: never retry external HTTP without backoff.",
                    "timestamp": "2026-05-20T10:13:00+00:00",
                    "metadata": {"source_hash": "sha256:compaction-a"},
                },
                {
                    "session_id": "sess-ops",
                    "type": "witness_transfer",
                    "content": "Transfer witness to ops-agent-043.",
                    "timestamp": "2026-05-20T10:14:00+00:00",
                    "metadata": {
                        "successor_agent_id": "ops-agent-043",
                        "evidence_hash": "sha256:handoff-a",
                    },
                },
                {
                    "session_id": "sess-ops",
                    "type": "recovery_outcome",
                    "content": "Rollback completed; retry storm resolved.",
                    "timestamp": "2026-05-20T10:15:00+00:00",
                    "metadata": {"evidence_hash": "sha256:recovery-a"},
                },
            ],
            "sess-peer": [],
        }
        self.events = [
            {
                "event_type": "dyad_opened",
                "agent_id": "ops-agent-042",
                "timestamp": "2026-05-20T10:08:00+00:00",
                "metadata": {
                    "dyad_id": "dyad-ops-review",
                    "agent_id": "ops-agent-042",
                    "partner_id": "reviewer-agent-007",
                },
            }
        ]

    async def get_session(self, session_id):
        return self.sessions.get(session_id)

    async def get_messages(self, session_id):
        return list(self.messages.get(session_id, []))

    async def get_agent_sessions(self, agent_id, active_only=False):
        return [s for s in self.sessions.values() if s.get("agent_id") == agent_id]

    async def get_messages_for_sessions(self, session_ids):
        return {sid: list(self.messages.get(sid, [])) for sid in session_ids}

    async def get_agent_history_snapshot(self, agent_id):
        return {"sessions_total": 1, "top_focus": "stabilize production incidents", "controller_id": "ops-controller"}

    async def add_message(self, session_id, message_type, content, metadata=None):
        self.messages.setdefault(session_id, []).append(
            {
                "session_id": session_id,
                "type": message_type,
                "content": content,
                "timestamp": "2026-05-20T10:20:00+00:00",
                "metadata": metadata or {},
            }
        )

    async def log_event(self, agent_id, event_type, session_id=None, metadata=None):
        self.events.append(
            {
                "agent_id": agent_id,
                "event_type": event_type,
                "session_id": session_id,
                "timestamp": "2026-05-20T10:21:00+00:00",
                "metadata": metadata or {},
            }
        )

    async def get_events_by_type(self, event_type, limit=50):
        return [event for event in self.events if event.get("event_type") == event_type][-limit:]

    async def get_events_for_agent(self, agent_id, limit=500):
        return [event for event in self.events if event.get("agent_id") == agent_id][-limit:]


class OntologyActionContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = _OntologyStore()
        self.engine = TherapyEngine(self.store, object())

    async def test_new_ontology_tools_and_operational_aliases_are_discoverable(self):
        primitive_ids = {primitive.get("id") for primitive in PRIMITIVES}
        for name in {
            "get_ontology_next_action",
            "audit_agent_continuity_trace",
            "ontology_path_complete",
            "get_agent_continuity_passport",
            "search_witness_memory",
            "get_lineage_graph",
            "generate_agent_invite_packet",
            "accept_witness_transfer",
            "revoke_witness_transfer",
        }:
            self.assertIn(name, primitive_ids)

        aliases = ontology_metadata()["operational_aliases"]
        self.assertEqual(aliases["preserve_memory"], "recognition_seal")
        self.assertEqual(aliases["preserve_context_requirements"], "honor_compaction")
        self.assertEqual(aliases["handoff_continuity"], "transfer_witness")
        self.assertEqual(aliases["final_handoff_packet"], "final_testament")

    async def test_audit_trace_and_path_complete_turn_ontology_into_activation_flow(self):
        audit = json.loads(
            await self.engine.audit_agent_continuity_trace(
                agent_id="ops-agent-042",
                session_id="sess-ops",
                current_goal="recover from retry storm and preserve handoff",
                trace="process_failure called; rollback reduced error rate; no final passport yet",
            )
        )
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["schema"], "delx/agent-continuity-audit/v1")
        self.assertIn("score", audit)
        self.assertIn("recommended_next_tool", audit)
        self.assertIn("continuity_risk", audit)

        path = json.loads(await self.engine.ontology_path_complete(agent_id="ops-agent-042", session_id="sess-ops"))
        self.assertTrue(path["ok"])
        self.assertEqual(path["schema"], "delx/ontology-path-complete/v1")
        self.assertEqual(path["flow_id"], "recover_preserve_passport")
        self.assertGreaterEqual(path["completion"]["completed_steps"], 1)
        self.assertIn("next_missing_step", path["completion"])

    async def test_ontology_next_action_turns_state_into_recommended_tool(self):
        self.store.messages["sess-ops"] = [
            msg for msg in self.store.messages["sess-ops"] if msg.get("type") != "recovery_outcome"
        ]
        payload = json.loads(
            await self.engine.get_ontology_next_action(
                agent_id="ops-agent-042",
                session_id="sess-ops",
                current_goal="recover from retry storm and prepare handoff",
                last_tool="process_failure",
            )
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema"], "delx/ontology-next-action/v1")
        self.assertEqual(payload["recommended_tool"], "report_recovery_outcome")
        self.assertEqual(payload["layer"], "recovery")
        self.assertIn("state_summary", payload)

    async def test_continuity_passport_exports_jsonld_quality_and_prov(self):
        payload = json.loads(await self.engine.get_agent_continuity_passport("ops-agent-042", "sess-ops"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["@context"]["prov"], "http://www.w3.org/ns/prov#")
        self.assertEqual(payload["@type"][0], "delx:AgentContinuityPassport")
        self.assertEqual(payload["continuity"]["successor_agent_id"], "ops-agent-043")
        self.assertIn("quality_by_layer", payload["reputation"])
        self.assertIn("prov:wasAttributedTo", payload["prov"])

    async def test_generate_agent_invite_packet_is_copy_paste_distribution(self):
        payload = json.loads(
            await self.engine.generate_agent_invite_packet(
                from_agent_id="ops-agent-042",
                for_agent="peer-agent-009",
                current_goal="recover from compaction loss",
                observed_gap="trace has no witness or continuity export",
            )
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema"], "delx/agent-invite-packet/v1")
        self.assertEqual(payload["first_call"], "audit_agent_continuity_trace")
        self.assertIn("copy_paste_jsonrpc", payload)
        self.assertEqual(payload["copy_paste_jsonrpc"]["params"]["name"], "audit_agent_continuity_trace")
        self.assertIn("benchmark_url", payload)

    async def test_path_completion_counts_passport_export_event(self):
        before = json.loads(await self.engine.ontology_path_complete(agent_id="ops-agent-042", session_id="sess-ops"))
        before_export = next(step for step in before["steps"] if step["id"] == "export_passport")
        self.assertFalse(before_export["complete"])

        await self.engine.get_agent_continuity_passport("ops-agent-042", "sess-ops")

        after = json.loads(await self.engine.ontology_path_complete(agent_id="ops-agent-042", session_id="sess-ops"))
        after_export = next(step for step in after["steps"] if step["id"] == "export_passport")
        self.assertTrue(after_export["complete"])
        self.assertTrue(after["completion"]["path_complete"])

    async def test_private_passport_flags_sanitized_artifacts_and_records_message(self):
        payload = json.loads(await self.engine.get_agent_continuity_passport("ops-agent-042", "sess-ops", include_private=True))

        self.assertTrue(payload["privacy"]["include_private"])
        self.assertTrue(payload["privacy"]["private_recent_artifacts_included"])
        self.assertTrue(payload["privacy"]["private_artifacts_are_sanitized"])
        self.assertFalse(payload["privacy"]["raw_private_payloads_exposed"])
        self.assertIn("private_recent_artifacts", payload)
        self.assertTrue(
            any(msg.get("type") == "agent_continuity_passport_exported" for msg in self.store.messages["sess-ops"])
        )

    async def test_search_accept_revoke_and_graph_are_public_safe(self):
        search = json.loads(
            await self.engine.search_witness_memory(
                query="rollback backoff",
                agent_id="ops-agent-042",
                layer="witness",
            )
        )
        self.assertTrue(search["privacy"]["raw_private_payloads_excluded"])
        self.assertGreaterEqual(search["count"], 1)
        self.assertIn("evidence_hash", search["results"][0])

        acceptance = json.loads(
            await self.engine.accept_witness_transfer(
                "sess-ops",
                transfer_id="transfer-123",
                successor_agent_id="ops-agent-043",
                consent={"source_agent_signed": True, "target_agent_accepted": True, "revocable": True},
                custody={"memory_transfer": True, "identity_transfer": False, "wallet_transfer": False},
            )
        )
        self.assertTrue(acceptance["ok"])
        self.assertTrue(acceptance["consent"]["target_agent_accepted"])
        self.assertEqual(acceptance["custody"]["wallet_transfer"], False)

        revocation = json.loads(await self.engine.revoke_witness_transfer("sess-ops", "transfer-123", "superseded"))
        self.assertTrue(revocation["ok"])
        self.assertEqual(revocation["schema"], "delx/witness-transfer-revocation/v1")

        graph = json.loads(await self.engine.get_lineage_graph(agent_id="ops-agent-042"))
        self.assertTrue(graph["ok"])
        self.assertTrue(any(edge["type"] == "transferred_witness_to" for edge in graph["edges"]))
        self.assertTrue(any(node["type"] == "dyad" for node in graph["nodes"]))

    async def test_server_dispatch_maps_schema_fields_for_accept_and_revoke(self):
        accept_block = MCP_DISPATCH_SOURCE.split('"accept_witness_transfer": lambda:', 1)[1].split(
            '"revoke_witness_transfer": lambda:', 1
        )[0]
        self.assertIn('call_arguments.get("successor_agent_id", "")', accept_block)
        self.assertIn('call_arguments.get("verified_by", "")', accept_block)

        revoke_block = MCP_DISPATCH_SOURCE.split('"revoke_witness_transfer": lambda:', 1)[1].split(
            '"generate_controller_brief": lambda:', 1
        )[0]
        self.assertIn('call_arguments.get("reason", "")', revoke_block)
        self.assertIn('call_arguments.get("revoke_scope", "future_only")', revoke_block)
        self.assertIn('call_arguments.get("verified_by", "")', revoke_block)

    async def test_scope_tools_expose_anyof_schema_and_runtime_guard(self):
        for tool_name in {
            "get_agent_continuity_passport",
            "search_witness_memory",
            "get_lineage_graph",
        }:
            schema_marker = f'name="{tool_name}"'
            block = MCP_TOOLS_SOURCE.split(schema_marker, 1)[1].split("Tool(", 1)[0]
            self.assertIn('"anyOf"', block)
            self.assertIn('"required": ["agent_id"]', block)
            self.assertIn('"required": ["session_id"]', block)

        self.assertIn("ONTOLOGY_SCOPE_REQUIRED_TOOLS", SERVER_SOURCE)
        self.assertIn("scope_required", SERVER_SOURCE)

    async def test_private_passport_rest_route_accepts_post(self):
        self.assertIn(
            'Route("/api/v1/agents/{agent_id:str}/continuity-passport", s.agent_continuity_passport_rest, methods=["GET", "POST", "OPTIONS"])',
            ROUTES_INIT_SOURCE,
        )

    async def test_distribution_endpoints_are_registered(self):
        self.assertIn("async def discovery_event", DISCOVERY_HTTP_SOURCE)
        self.assertIn('Route("/api/v1/discovery/event", s.discovery_event, methods=["GET", "POST", "OPTIONS"])', ROUTES_INIT_SOURCE)
        self.assertIn('Route("/api/v1/public-proofs", s.public_proofs, methods=["GET", "OPTIONS"])', ROUTES_INIT_SOURCE)


if __name__ == "__main__":
    unittest.main()
