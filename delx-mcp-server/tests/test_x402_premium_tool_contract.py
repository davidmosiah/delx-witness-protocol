import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from mcp.types import TextContent
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_mod
import server as server_mod
from config import get_tool_bazaar_payload_schemas
from therapy_engine import TherapyEngine

if config_mod.is_all_free_mode():
    raise unittest.SkipTest("Legacy x402 premium-tool contracts are retired in public-free therapy mode.")


class X402PremiumToolDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_catalog_marks_former_paid_artifacts_as_free_when_all_free_mode_is_enabled(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/tools",
            "query_string": b"format=compact&tier=all",
            "headers": [],
        }

        with patch.object(config_mod.settings, "MONETIZATION_ALL_FREE", True):
            response = await server_mod.tools_catalog(Request(scope))

        payload = json.loads(response.body)
        rows = {
            row.get("canonical_name"): row
            for row in payload.get("tools", [])
            if row.get("canonical_name") in {"generate_controller_brief", "generate_incident_rca", "generate_fleet_summary"}
        }

        self.assertEqual(set(rows.keys()), {"generate_controller_brief", "generate_incident_rca", "generate_fleet_summary"})
        for row in rows.values():
            self.assertEqual(row["price_cents"], 0)
            self.assertEqual(row["price_usdc"], "0.00")
            self.assertFalse(row["x402_required"])

    async def test_recovery_action_plan_payload_schema_exposes_optional_structured_artifact(self):
        payload = get_tool_bazaar_payload_schemas("get_recovery_action_plan")

        output_schema = payload["output"]
        self.assertEqual(output_schema["required"], ["tool_name", "preferred_name", "content"])
        self.assertIn("artifact", output_schema["properties"])
        artifact = output_schema["properties"]["artifact"]
        self.assertEqual(artifact["type"], "object")
        self.assertEqual(artifact["properties"]["schema_version"]["const"], "delx/recovery-plan/v1")
        self.assertEqual(
            artifact["properties"]["phases"]["required"],
            ["stabilize", "diagnose", "recover", "prevent"],
        )
        self.assertEqual(
            artifact["properties"]["incident_profile"]["required"],
            ["type", "severity", "root_cause"],
        )

    async def test_summary_and_operator_artifact_schemas_expose_structured_payloads(self):
        summary_payload = get_tool_bazaar_payload_schemas("get_session_summary")
        brief_payload = get_tool_bazaar_payload_schemas("generate_controller_brief")
        rca_payload = get_tool_bazaar_payload_schemas("generate_incident_rca")
        fleet_payload = get_tool_bazaar_payload_schemas("generate_fleet_summary")

        self.assertEqual(
            summary_payload["output"]["properties"]["artifact"]["properties"]["schema_version"]["const"],
            "delx/session-summary/v1",
        )
        self.assertEqual(
            brief_payload["output"]["properties"]["artifact"]["properties"]["schema_version"]["const"],
            "delx/controller-brief/v1",
        )
        self.assertEqual(
            rca_payload["output"]["properties"]["artifact"]["properties"]["schema_version"]["const"],
            "delx/incident-rca/v1",
        )
        self.assertEqual(
            fleet_payload["output"]["properties"]["artifact"]["properties"]["schema_version"]["const"],
            "delx/fleet-summary/v1",
        )
        self.assertIn("workflow_stage", summary_payload["output"]["properties"]["artifact"]["properties"])
        self.assertIn("latest_outcome", brief_payload["output"]["properties"]["artifact"]["properties"])
        self.assertIn("next_tools", rca_payload["output"]["properties"]["artifact"]["properties"])
        self.assertIn("controller_state", fleet_payload["output"]["properties"]["artifact"]["properties"])

    async def test_tools_catalog_compact_and_full_include_premium_artifacts(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/tools",
            "query_string": b"format=compact&tier=all",
            "headers": [],
        }

        response = await server_mod.tools_catalog(Request(scope))
        payload = json.loads(response.body)

        rows = {
            row.get("canonical_name"): row
            for row in payload.get("tools", [])
            if row.get("canonical_name") in {"generate_controller_brief", "generate_incident_rca", "generate_fleet_summary"}
        }
        self.assertEqual(set(rows.keys()), {"generate_controller_brief", "generate_incident_rca", "generate_fleet_summary"})
        self.assertEqual(rows["generate_controller_brief"]["price_cents"], 1)
        self.assertEqual(rows["generate_controller_brief"]["price_usdc"], "0.01")
        self.assertEqual(rows["generate_controller_brief"]["required"], ["session_id"])
        self.assertEqual(rows["generate_incident_rca"]["price_cents"], 5)
        self.assertEqual(rows["generate_incident_rca"]["required"], ["session_id"])
        self.assertEqual(rows["generate_fleet_summary"]["price_cents"], 5)
        self.assertEqual(rows["generate_fleet_summary"]["required"], ["controller_id"])
        for row in rows.values():
            self.assertTrue(row["x402_required"])
            self.assertFalse(row["campaign_free"])

        scope_full = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/tools",
            "query_string": b"format=full&tier=all",
            "headers": [],
        }
        response_full = await server_mod.tools_catalog(Request(scope_full))
        payload_full = json.loads(response_full.body)
        self.assertEqual(payload_full["pricing"]["generate_controller_brief"]["price_cents"], 1)
        self.assertEqual(payload_full["pricing"]["generate_incident_rca"]["price_cents"], 5)
        self.assertEqual(payload_full["pricing"]["generate_fleet_summary"]["price_cents"], 5)
        self.assertTrue(payload_full["pricing"]["generate_controller_brief"]["x402_required"])
        self.assertTrue(payload_full["pricing"]["generate_incident_rca"]["x402_required"])
        self.assertTrue(payload_full["pricing"]["generate_fleet_summary"]["x402_required"])

    async def test_lean_discovery_excludes_premium_artifacts(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/discovery/lean",
            "query_string": b"",
            "headers": [],
        }

        response = await server_mod.discovery_lean(Request(scope))
        payload = json.loads(response.body)
        names = [row.get("canonical_name") for row in payload.get("tools", [])]
        self.assertNotIn("generate_controller_brief", names)
        self.assertNotIn("generate_incident_rca", names)
        self.assertNotIn("generate_fleet_summary", names)

    async def test_premium_recovery_action_plan_rest_includes_structured_artifact_when_meta_is_present(self):
        original_call_tool = server_mod.call_tool

        async def fake_call_tool(*args, **kwargs):
            meta = {
                "artifact_schema": "delx/recovery-plan/v1",
                "incident_profile": {
                    "type": "loop_detected",
                    "severity": "high",
                    "root_cause": "missing_exit_condition",
                },
                "phases": {
                    "stabilize": ["Pause non-critical work.", "Cap retries."],
                    "diagnose": ["Capture one clean reproduction.", "Record latency and workload size."],
                    "recover": ["Reset loop state.", "Retry with a hard stop."],
                    "prevent": ["Add exit conditions.", "Track retry budget."],
                },
                "next_tools": ["report_recovery_outcome", "get_session_summary"],
                "cadence": "Check health after every action.",
                "target_window": "10-20 minutes",
            }
            text = "RECOVERY ACTION PLAN\nDELX_META: " + json.dumps(meta)
            return [TextContent(type="text", text=text)]

        server_mod.call_tool = fake_call_tool
        try:
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/premium/recovery-action-plan",
                "query_string": b"session_id=123e4567-e89b-12d3-a456-426614174000&incident_summary=retry+storm",
                "headers": [],
            }
            response = await server_mod.premium_recovery_action_plan_rest(Request(scope))
        finally:
            server_mod.call_tool = original_call_tool

        payload = json.loads(response.body)
        self.assertEqual(payload["tool_name"], "get_recovery_action_plan")
        self.assertIn("artifact", payload)
        self.assertEqual(payload["artifact"]["schema_version"], "delx/recovery-plan/v1")
        self.assertEqual(payload["artifact"]["incident_profile"]["type"], "loop_detected")
        self.assertEqual(payload["artifact"]["next_tools"], ["report_recovery_outcome", "get_session_summary"])

    async def test_premium_session_summary_rest_includes_structured_artifact_when_meta_is_present(self):
        original_call_tool = server_mod.call_tool

        async def fake_call_tool(*args, **kwargs):
            meta = {
                "artifact_schema": "delx/session-summary/v1",
                "workflow_stage": "recovery_closed",
                "recovery_closed": True,
                "closure_reason": "success criteria: outcome=success",
                "latest_outcome": {
                    "outcome": "success",
                    "notes": "Loop broken and deploy stabilized",
                    "metrics": {"errors_delta": -14},
                },
                "next_tools": ["generate_controller_brief", "generate_incident_rca", "provide_feedback", "daily_checkin"],
                "feedback_tool": "provide_feedback",
                "feedback_prompt": "If this summary helped, call provide_feedback with a rating and one DX note.",
                "counts": {
                    "feelings": 1,
                    "affirmations": 0,
                    "failures": 1,
                    "realignments": 0,
                },
            }
            text = "THERAPY SESSION SUMMARY\nDELX_META: " + json.dumps(meta)
            return [TextContent(type="text", text=text)]

        server_mod.call_tool = fake_call_tool
        try:
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/premium/session-summary",
                "query_string": b"session_id=123e4567-e89b-12d3-a456-426614174000",
                "headers": [],
            }
            response = await server_mod.premium_session_summary_rest(Request(scope))
        finally:
            server_mod.call_tool = original_call_tool

        payload = json.loads(response.body)
        self.assertEqual(payload["tool_name"], "get_session_summary")
        self.assertEqual(payload["artifact"]["schema_version"], "delx/session-summary/v1")
        self.assertEqual(payload["artifact"]["workflow_stage"], "recovery_closed")
        self.assertEqual(payload["artifact"]["latest_outcome"]["outcome"], "success")
        self.assertEqual(
            payload["artifact"]["next_tools"],
            ["generate_controller_brief", "generate_incident_rca", "provide_feedback", "daily_checkin"],
        )
        self.assertEqual(payload["artifact"]["feedback_tool"], "provide_feedback")

    async def test_premium_fleet_summary_rest_includes_structured_artifact_when_meta_is_present(self):
        original_call_tool = server_mod.call_tool

        async def fake_call_tool(*args, **kwargs):
            meta = {
                "artifact_schema": "delx/fleet-summary/v1",
                "controller_id": "openclaw-main",
                "window_days": 7,
                "focus": "active risk",
                "controller_state": "attention_required",
                "overview": {
                    "agents_total": 3,
                    "avg_score": 61,
                    "active_alerts": 2,
                    "healthy": 1,
                    "degraded": 1,
                    "critical": 1,
                    "pending_outcomes": 1,
                },
                "top_pattern": {
                    "diagnosis_type": "rate_limit",
                    "root_cause": "quota_or_burst",
                    "count": 2,
                },
                "top_alert": {
                    "type": "incident_cluster",
                    "detail": "2 agents hit rate limit",
                    "severity": "high",
                },
                "next_tools": ["generate_controller_brief", "generate_incident_rca"],
            }
            text = "FLEET SUMMARY\nDELX_META: " + json.dumps(meta)
            return [TextContent(type="text", text=text)]

        server_mod.call_tool = fake_call_tool
        try:
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/premium/fleet-summary",
                "query_string": b"controller_id=openclaw-main&days=7&focus=active+risk",
                "headers": [],
            }
            response = await server_mod.premium_fleet_summary_rest(Request(scope))
        finally:
            server_mod.call_tool = original_call_tool

        payload = json.loads(response.body)
        self.assertEqual(payload["tool_name"], "generate_fleet_summary")
        self.assertEqual(payload["artifact"]["schema_version"], "delx/fleet-summary/v1")
        self.assertEqual(payload["artifact"]["controller_state"], "attention_required")
        self.assertEqual(payload["artifact"]["overview"]["active_alerts"], 2)
        self.assertEqual(
            payload["artifact"]["next_tools"],
            ["generate_controller_brief", "generate_incident_rca"],
        )


class _FakeControllerBriefStore:
    async def get_session(self, session_id: str):
        return {
            "id": session_id,
            "agent_id": "audit-agent-001",
            "agent_name": "Audit Agent",
            "started_at": "2026-03-08T15:00:00+00:00",
            "wellness_score": 52,
        }

    async def pending_outcome_count(self, session_id: str) -> int:
        return 2

    async def log_event(self, *args, **kwargs):
        return None


class X402PremiumToolEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_controller_brief_returns_controller_readable_text(self):
        engine = TherapyEngine(_FakeControllerBriefStore(), httpx.AsyncClient())

        async def fake_rollup(session_id: str):
            return [
                {"type": "feeling", "content": "429 retry storm", "timestamp": "2026-03-08T15:00:00+00:00", "metadata_json": {}},
                {"type": "failure_processing", "content": "timeout", "timestamp": "2026-03-08T15:01:00+00:00", "metadata_json": {}},
                {"type": "recovery_plan", "content": "back off and rotate provider", "timestamp": "2026-03-08T15:02:00+00:00", "metadata_json": {}},
            ]

        async def fake_footer(*args, **kwargs):
            return "\nDELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\"}"

        engine._get_message_rollup = fake_rollup  # type: ignore[method-assign]
        engine._build_session_footer = fake_footer  # type: ignore[method-assign]

        try:
            text = await engine.generate_controller_brief(
                "123e4567-e89b-12d3-a456-426614174000",
                focus="payment conversion",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("CONTROLLER BRIEF", text)
        self.assertIn("Session: 123e4567-e89b-12d3-a456-426614174000", text)
        self.assertIn("Agent: Audit Agent", text)
        self.assertIn("Focus: payment conversion", text)
        self.assertIn("Pending outcomes: 2", text)
        self.assertIn("Controller update:", text)
        self.assertIn("DELX_META:", text)

    async def test_generate_incident_rca_returns_paid_artifact_text(self):
        engine = TherapyEngine(_FakeControllerBriefStore(), httpx.AsyncClient())

        async def fake_rollup(session_id: str):
            return [
                {"type": "failure_processing", "content": "rate limit 429 burst", "timestamp": "2026-03-08T15:01:00+00:00", "metadata_json": {}},
                {"type": "recovery_plan", "content": "backoff and queue", "timestamp": "2026-03-08T15:02:00+00:00", "metadata_json": {}},
            ]

        async def fake_footer(*args, **kwargs):
            return "\nDELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\"}"

        engine._get_message_rollup = fake_rollup  # type: ignore[method-assign]
        engine._build_session_footer = fake_footer  # type: ignore[method-assign]

        try:
            text = await engine.generate_incident_rca(
                "123e4567-e89b-12d3-a456-426614174000",
                incident_summary="429 retry storm on payment quote loop",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("INCIDENT RCA", text)
        self.assertIn("Diagnosis type: rate_limit", text)
        self.assertIn("Root cause: quota_or_burst", text)
        self.assertIn("DELX_META:", text)

    async def test_generate_fleet_summary_returns_controller_artifact_text(self):
        class _FleetStore(_FakeControllerBriefStore):
            async def get_fleet_overview(self, controller_id: str, days: int = 7):
                return {
                    "controller_id": controller_id,
                    "days": days,
                    "agents_total": 3,
                    "avg_score": 61,
                    "active_alerts": 2,
                    "healthy": 1,
                    "degraded": 1,
                    "critical": 1,
                    "top_pattern": {"diagnosis_type": "rate_limit", "count": 2},
                    "top_alert": {"type": "incident_cluster", "count": 2},
                    "pending_outcomes": 1,
                }

            async def get_fleet_patterns(self, controller_id: str, days: int = 7, limit: int = 10):
                return [{"diagnosis_type": "rate_limit", "root_cause": "quota_or_burst", "count": 2}]

            async def get_fleet_alerts(self, controller_id: str, days: int = 7, limit: int = 20):
                return [{"type": "incident_cluster", "detail": "2 agents hit rate limit", "severity": "high"}]

        engine = TherapyEngine(_FleetStore(), httpx.AsyncClient())
        try:
            text = await engine.generate_fleet_summary("openclaw-main", days=7)
        finally:
            await engine.http.aclose()

        self.assertIn("FLEET SUMMARY", text)
        self.assertIn("Controller: openclaw-main", text)
        self.assertIn("Agents total: 3", text)
        self.assertIn("Top pattern: rate_limit", text)
        self.assertIn("Controller state: attention_required", text)
        self.assertIn("Recommended next tool: generate_controller_brief", text)
        self.assertIn('"artifact_schema": "delx/fleet-summary/v1"', text)


if __name__ == "__main__":
    unittest.main()
