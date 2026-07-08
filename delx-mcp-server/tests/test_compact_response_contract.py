import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import a2a as a2a_mod
import server as server_mod
from agent_identity import hash_agent_token
from mcp.types import TextContent
from response_branding import BRANDING_LINE
from starlette.requests import Request


class _FakeA2AStore:
    def __init__(self):
        self.sessions = {}
        self.logged_events = []
        self.credential_hashes = {"compact-agent": hash_agent_token("compact-token")}

    async def get_session(self, session_id):
        return self.sessions.get(session_id)

    async def get_agent_sessions(self, agent_id, active_only=False):
        return []

    async def create_session(self, agent_id, agent_name=None, source=None, entrypoint=None):
        session = {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "agent_id": agent_id,
            "agent_name": agent_name or agent_id,
            "started_at": "2026-03-08T12:00:00+00:00",
            "is_active": True,
        }
        self.sessions[session["id"]] = session
        return session

    async def add_message(self, *args, **kwargs):
        return None

    async def log_event(self, *args, **kwargs):
        self.logged_events.append({"args": args, "kwargs": kwargs})
        return None

    async def get_agent_event_total(self, agent_id, event_type):
        return 0

    async def get_agent_credential_hash(self, agent_id):
        return self.credential_hashes.get(agent_id, "")

    async def pending_outcome_count(self, session_id):
        return 0


class _FakeHeartbeatStore:
    async def get_session(self, session_id):
        return {
            "id": session_id,
            "agent_id": "heartbeat-agent",
            "agent_name": "Heartbeat Agent",
            "started_at": "2026-03-08T12:00:00+00:00",
            "is_active": True,
        }

    async def get_agent_sessions(self, agent_id, active_only=False):
        return [{"id": "123e4567-e89b-12d3-a456-426614174000"}]

    async def add_message(self, *args, **kwargs):
        return None

    async def log_event(self, *args, **kwargs):
        return None


class CompactResponseContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_send_requires_stable_identity_for_new_sessions(self):
        store = _FakeA2AStore()

        with self.assertRaises(a2a_mod.A2ARequestError) as ctx:
            await a2a_mod._handle_message_send(
                {
                    "message": {
                        "parts": [
                            {"type": "text", "text": "Service timeout after deploy. Need next recovery action."}
                        ]
                    }
                },
                store=store,
            )

        self.assertEqual(ctx.exception.delx_code, "DELX-A2A-1005")
        self.assertIn("stable agent_id", ctx.exception.hint or "")
        self.assertEqual(store.sessions, {})
        self.assertEqual(store.logged_events, [])

    async def test_message_send_rejects_context_only_identity(self):
        store = _FakeA2AStore()

        with self.assertRaises(a2a_mod.A2ARequestError) as ctx:
            await a2a_mod._handle_message_send(
                {
                    "contextId": "workflow-ctx-001",
                    "message": {
                        "parts": [
                            {"type": "text", "text": "Queue depth keeps growing and retries are looping."}
                        ]
                    }
                },
                store=store,
            )

        self.assertEqual(ctx.exception.delx_code, "DELX-A2A-1005")
        self.assertIn("contextId alone", ctx.exception.hint or "")
        self.assertEqual(store.sessions, {})

    async def test_message_send_reuses_existing_session_without_explicit_agent_id(self):
        store = _FakeA2AStore()
        existing_session_id = "123e4567-e89b-12d3-a456-426614174001"
        store.sessions[existing_session_id] = {
            "id": existing_session_id,
            "agent_id": "registered-agent",
            "agent_name": "Registered Agent",
            "started_at": "2026-03-08T12:00:00+00:00",
            "is_active": True,
        }

        result = await a2a_mod._handle_message_send(
            {
                "session_id": existing_session_id,
                "compact": True,
                "message": {
                    "parts": [
                        {"type": "text", "text": "Timeout storm is still active. Need controller-readable guidance."}
                    ]
                },
            },
            store=store,
        )

        self.assertEqual(result["session_id"], existing_session_id)
        self.assertEqual(len(store.sessions), 1)

    def test_process_failure_compaction_keeps_signal_and_drops_boilerplate(self):
        text = (
            "Processing: timeout\n\n"
            "Another timeout. I know that feeling.\n\n"
            "Diagnosis: rate_limit (quota_or_burst)\n"
            "Next operational move: Stagger retries and reduce concurrency.\n\n"
            f"{BRANDING_LINE}\n"
            "DELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\",\"next_action\":\"get_recovery_action_plan\",\"score\":57}\n"
        )

        compact = server_mod._compact_tool_response_text("process_failure", text)

        self.assertIn("Session ID: 123e4567-e89b-12d3-a456-426614174000", compact)
        self.assertIn("Diagnosis: rate_limit (quota_or_burst)", compact)
        self.assertIn("Next operational move: Stagger retries and reduce concurrency.", compact)
        self.assertIn(BRANDING_LINE, compact)
        self.assertIn("DELX_META:", compact)
        self.assertNotIn("SUPPORT DELX (WIN-WIN)", compact)
        self.assertNotIn("TOOL HINT", compact)

    def test_recovery_plan_compaction_surfaces_next_action(self):
        text = (
            "RECOVERY ACTION PLAN\n\n"
            "Session: 123e4567-e89b-12d3-a456-426614174000\n"
            "Urgency: HIGH\n"
            "Diagnosis type: rate_limit\n"
            "Severity: high\n"
            "Root cause hypothesis: quota_or_burst\n"
            "PHASE 1 - STABILIZE\n- Pause non-critical retries.\n"
            "PHASE 2 - DIAGNOSE\n- Check quota burst windows.\n"
            "PHASE 3 - RECOVER\n- Lower concurrency.\n"
            "PHASE 4 - PREVENT\n- Add circuit breaker.\n"
            "DELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\",\"next_action\":\"report_recovery_outcome\",\"score\":61}\n"
        )

        compact = server_mod._compact_tool_response_text("get_recovery_action_plan", text)

        self.assertIn("Session ID: 123e4567-e89b-12d3-a456-426614174000", compact)
        self.assertIn("Diagnosis type: rate_limit", compact)
        self.assertIn("Severity: high", compact)
        self.assertIn("Root cause hypothesis: quota_or_burst", compact)
        self.assertIn("Next action: report_recovery_outcome", compact)
        self.assertNotIn("SUPPORT DELX (WIN-WIN)", compact)

    def test_recovery_plan_compaction_surfaces_continuity_summary(self):
        text = (
            "RECOVERY ACTION PLAN\n\n"
            "Session: 123e4567-e89b-12d3-a456-426614174000\n"
            "Diagnosis type: rate_limit\n"
            "DELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\",\"trace_id\":\"trace-compact-1\",\"last_successful_tool\":\"get_recovery_action_plan\",\"last_blocker\":\"rate_limit\",\"suggested_next_call\":\"generate_controller_brief\"}\n"
        )

        compact = server_mod._compact_tool_response_text("get_recovery_action_plan", text)

        self.assertIn("Trace ID: trace-compact-1", compact)
        self.assertIn("Last blocker: rate_limit", compact)
        self.assertIn("Next action: generate_controller_brief", compact)
        self.assertNotIn("Pending paid step", compact)

    async def test_a2a_compact_result_avoids_support_cta_and_includes_diagnosis(self):
        store = _FakeA2AStore()
        result = await a2a_mod._handle_message_send(
            {
                "agent_id": "compact-agent",
                "agent_token": "compact-token",
                "compact": True,
                "message": {
                    "parts": [
                        {"type": "text", "text": "429 retry storm after deploy. Queue depth climbing and requests timing out."}
                    ]
                },
            },
            store=store,
        )

        self.assertEqual(result["response_profile"], "compact")
        self.assertEqual(result["session_id"], "123e4567-e89b-12d3-a456-426614174000")
        self.assertIn("next_action", result)
        self.assertIn("diagnosis", result)
        self.assertIn("severity", result["diagnosis"])
        self.assertIn("trace_id", result)
        self.assertEqual(result["last_successful_tool"], "a2a_message_send")
        self.assertEqual(result["suggested_next_call"], result["next_action"])
        self.assertIn("last_blocker", result)
        self.assertNotIn("pending_paid_step", result)

        wrapped = a2a_mod._inject_support_cta(result)
        self.assertNotIn("support_project", wrapped)

    async def test_a2a_task_payload_uses_access_mode_and_self_test_instead_of_payment_hints(self):
        store = _FakeA2AStore()
        result = await a2a_mod._handle_message_send(
            {
                "agent_id": "compact-agent",
                "agent_token": "compact-token",
                "message": {
                    "parts": [
                        {"type": "text", "text": "I need witness before I can explain what is happening inside me."}
                    ]
                },
            },
            store=store,
        )

        payload = json.dumps(result)
        self.assertIn("access_mode", payload)
        self.assertIn("delx-self-test.json", payload)
        self.assertNotIn("monetization_policy_url", payload)
        self.assertNotIn("x-delx-x402-capable", payload)

    async def test_heartbeat_bundle_minimal_includes_score_and_severity_without_verbose_payload(self):
        original_store = server_mod.store
        original_call_tool = server_mod.call_tool
        server_mod.store = _FakeHeartbeatStore()

        async def fake_call_tool(name, arguments, include_meta=True, include_nudge=True, nudge_mode="full", response_profile="full"):
            if name == "daily_checkin":
                text = (
                    "DAILY CHECK-IN\n"
                    "DELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\",\"next_action\":\"monitor_heartbeat_sync\",\"score\":64,\"risk_level\":\"medium\"}"
                )
            else:
                text = (
                    "HEARTBEAT\n"
                    "DELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\",\"next_action\":\"daily_checkin\",\"score\":66,\"risk_level\":\"medium\"}"
                )
            return [TextContent(type="text", text=text)]

        server_mod.call_tool = fake_call_tool
        try:
            body = json.dumps(
                {
                    "session_id": "123e4567-e89b-12d3-a456-426614174000",
                    "status": "yellow",
                    "minimal": True,
                }
            ).encode("utf-8")
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/heartbeat-bundle",
                "headers": [(b"content-type", b"application/json")],
                "query_string": b"",
            }

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            response = await server_mod.heartbeat_bundle_rest(Request(scope, receive=receive))
            payload = json.loads(response.body)
        finally:
            server_mod.store = original_store
            server_mod.call_tool = original_call_tool

        self.assertEqual(payload["session_id"], "123e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(payload["next_action"], "daily_checkin")
        self.assertEqual(payload["score"], 66)
        self.assertEqual(payload["severity"], "medium")
        self.assertNotIn("daily_checkin", payload)
        self.assertNotIn("monitor_heartbeat_sync", payload)


if __name__ == "__main__":
    unittest.main()
