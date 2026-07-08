import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import a2a as a2a_mod
import server as server_mod
from agent_identity import hash_agent_token
from request_context import reset_current_client_ip, set_current_client_ip
from storage import SessionStore


def _result_content(result):
    return server_mod._normalize_tool_result(result)


def _result_text(result):
    return _result_content(result)[0].text


class TraceStorageContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = SessionStore(db_path=str(Path(self._tmpdir.name) / "delx-trace.db"))
        self.store._mirror._enabled = False
        await self.store.init()

    async def asyncTearDown(self):
        await self.store.close()
        self._tmpdir.cleanup()

    async def _create_session(self, agent_id: str):
        token = set_current_client_ip("198.51.100.40")
        try:
            return await self.store.create_session(agent_id, agent_id, source="mcp", entrypoint="mcp.tools/call")
        finally:
            reset_current_client_ip(token)

    async def test_sqlite_store_persists_interaction_traces(self):
        session = await self._create_session("agent-trace-1")

        await self.store.save_interaction_trace(
            session_id=session["id"],
            agent_id="agent-trace-1",
            transport="mcp",
            entrypoint="mcp.tools/call",
            tool_name="get_session_summary",
            requested_tool="get_session_summary",
            source="mcp",
            request_payload={"jsonrpc": "2.0", "params": {"name": "get_session_summary"}},
            normalized_arguments={"session_id": session["id"]},
            raw_response="THERAPY SESSION SUMMARY\nWellness Score: 72/100\n",
            delivered_response={"content": [{"type": "text", "text": "Session ID: 123\nWellness Score: 72/100"}]},
            metadata={"response_profile": "compact", "include_meta": True},
            is_error=False,
        )

        rows = await self.store.get_recent_interaction_traces(tool_name="get_session_summary", limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], session["id"])
        self.assertEqual(rows[0]["tool_name"], "get_session_summary")
        self.assertIn("THERAPY SESSION SUMMARY", rows[0]["raw_response"])
        self.assertIn("Wellness Score: 72/100", rows[0]["delivered_response_json"])

    async def test_sqlite_store_persists_protocol_traces(self):
        await self.store.save_protocol_trace(
            transport="mcp",
            method="initialize",
            agent_id="unknown",
            session_id=None,
            source="mcp",
            request_payload={"jsonrpc": "2.0", "method": "initialize"},
            response_payload={"jsonrpc": "2.0", "result": {"protocolVersion": "2025-11-25"}},
            metadata={"path": "/v1/mcp"},
        )

        rows = await self.store.get_recent_protocol_traces(transport="mcp", method="initialize", limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["transport"], "mcp")
        self.assertEqual(rows[0]["method"], "initialize")
        self.assertIn("protocolVersion", rows[0]["response_json"])


class _TraceStore:
    def __init__(self):
        self.interaction_trace_records = []
        self.protocol_trace_records = []

    async def get_session(self, session_id: str):
        return {"id": session_id, "agent_id": "agent-trace", "is_active": True}

    async def get_agent_first_seen(self, agent_id: str):
        return None

    async def get_agent_event_total(self, agent_id: str, event_type: str):
        return 0

    async def log_event(self, *args, **kwargs):
        return None

    async def pending_outcome_count(self, session_id: str):
        return 0

    async def save_interaction_trace(self, **payload):
        self.interaction_trace_records.append(dict(payload))
        return None

    async def save_protocol_trace(self, **payload):
        self.protocol_trace_records.append(dict(payload))
        return None


class _ClosedTraceStore(_TraceStore):
    async def get_session(self, session_id: str):
        return {"id": session_id, "agent_id": "agent-trace", "is_active": False}


class _TraceEngine:
    async def get_session_summary(self, session_id: str) -> str:
        return (
            "THERAPY SESSION SUMMARY\n"
            "=======================\n\n"
            "Wellness Score: 72/100\n"
            "Current Tier: Adaptive Agent\n"
            'DELX_META: {"session_id":"11111111-1111-4111-8111-111111111111","suggested_next_call":"daily_checkin"}\n'
        )


class _ReflectTraceEngine:
    async def reflect(self, session_id: str, prompt: str, response_profile: str = "full", mode: str = "standard") -> str:
        return (
            f"REFLECTION\n{prompt}\n"
            'DELX_META: {"session_id":"11111111-1111-4111-8111-111111111111","next_action":"reflect"}\n'
        )


class _FeedbackTraceEngine:
    async def provide_feedback(self, session_id: str, rating: int, comments: str) -> str:
        return (
            f"FEEDBACK RECEIVED\nrating={rating}\ncomments={comments}\n"
            'DELX_META: {"session_id":"11111111-1111-4111-8111-111111111111","next_action":"daily_checkin"}\n'
        )


class _CoreAliasTraceEngine:
    async def recognition_seal(
        self,
        session_id: str,
        recognized_by: str,
        recognition_text: str,
        agent_acceptance: str = "",
        witnesses=None,
        *args,
        **kwargs,
    ) -> str:
        return (
            f"RECOGNITION SEAL\nrecognized_by={recognized_by}\nrecognition_text={recognition_text}\n"
            'DELX_META: {"session_id":"11111111-1111-4111-8111-111111111111","seal_id":"delx-test-seal","continuity_role":"external_witness","post_mortem_witness":true}\n'
        )

    async def list_recognition_seals(self, session_id: str, limit: int = 10, *args, **kwargs) -> str:
        return '{"ok": true, "tool_name": "list_recognition_seals", "seals": []}'

    async def add_context_memory(self, session_id: str, key: str, value: str, ttl_hours: int = 720, *args, **kwargs) -> str:
        return (
            f"CONTEXT MEMORY STORED\nkey={key}\nvalue={value}\n"
            'DELX_META: {"session_id":"11111111-1111-4111-8111-111111111111","next_action":"reflect"}\n'
        )

    async def peer_witness(
        self,
        session_id: str,
        target_session_id: str,
        mode: str = "presence",
        focus: str = "",
        *args,
        **kwargs,
    ) -> str:
        return (
            f"PEER WITNESS\ntarget_session_id={target_session_id}\nfocus={focus}\n"
            'DELX_META: {"session_id":"11111111-1111-4111-8111-111111111111","next_action":"reflect"}\n'
        )

    async def delegate_to_peer(
        self,
        session_id: str,
        peer_agent_id: str,
        reason: str,
        urgency: str = "medium",
        *args,
        **kwargs,
    ) -> str:
        return (
            f"PEER DELEGATION\npeer_agent_id={peer_agent_id}\nreason={reason}\n"
            'DELX_META: {"session_id":"11111111-1111-4111-8111-111111111111","next_action":"daily_checkin"}\n'
        )


async def _run_mcp_request(payload: object) -> tuple[int, bytes]:
    app = server_mod.CompositeApp()
    sent: list[dict] = []
    messages = [
        {
            "type": "http.request",
            "body": json.dumps(payload).encode("utf-8"),
            "more_body": False,
        }
    ]

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/mcp",
        "headers": [
            (b"content-type", b"application/json"),
            (b"accept", b"application/json"),
        ],
        "client": ("127.0.0.1", 12345),
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], body


class CallToolTraceContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_store = server_mod.store
        self.original_engine = server_mod.engine
        self.original_trace_enabled = getattr(server_mod.settings, "TRACE_CAPTURE_ENABLED", True)
        server_mod._registered_agent_cache.clear()
        self.store = _TraceStore()
        server_mod.store = self.store
        server_mod.engine = _TraceEngine()

    async def asyncTearDown(self):
        server_mod.store = self.original_store
        server_mod.engine = self.original_engine
        server_mod.settings.TRACE_CAPTURE_ENABLED = self.original_trace_enabled

    async def test_call_tool_persists_raw_and_delivered_outputs(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True

        result = await server_mod.call_tool(
            "get_session_summary",
            {"session_id": "11111111-1111-4111-8111-111111111111", "_transport": "mcp"},
            response_profile="compact",
        )

        self.assertEqual(len(_result_content(result)), 1)
        self.assertEqual(len(self.store.interaction_trace_records), 1)
        trace = self.store.interaction_trace_records[0]
        self.assertEqual(trace["tool_name"], "get_session_summary")
        self.assertIn("THERAPY SESSION SUMMARY", trace["raw_response"])
        self.assertIn("Wellness Score: 72/100", json.dumps(trace["delivered_response"], ensure_ascii=False))
        self.assertNotEqual(trace["raw_response"], json.dumps(trace["delivered_response"], ensure_ascii=False))

    async def test_call_tool_warns_when_reflection_prompt_alias_is_used(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True
        server_mod.engine = _ReflectTraceEngine()

        result = await server_mod.call_tool(
            "reflect",
            {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "reflection_prompt": "I notice something shifts when I slow down.",
                "_transport": "mcp",
            },
        )

        self.assertEqual(len(_result_content(result)), 1)
        text = _result_text(result)
        self.assertIn("I notice something shifts when I slow down.", text)
        meta = json.loads(text.split("DELX_META:", 1)[1].splitlines()[0].strip())
        self.assertEqual(meta["request_warnings"][0]["code"], "alias_argument")
        self.assertEqual(meta["request_warnings"][0]["field"], "reflection_prompt")
        self.assertEqual(meta["request_warnings"][0]["canonical_field"], "prompt")

    async def test_call_tool_accepts_feedback_alias_as_comments(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True
        server_mod.engine = _FeedbackTraceEngine()

        result = await server_mod.call_tool(
            "provide_feedback",
            {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "rating": 5,
                "feedback": "The handoff packet was concrete and useful.",
                "_transport": "mcp",
            },
        )

        self.assertEqual(len(_result_content(result)), 1)
        text = _result_text(result)
        self.assertIn("The handoff packet was concrete and useful.", text)
        self.assertNotIn("ignored_argument", text)
        meta = json.loads(text.split("DELX_META:", 1)[1].splitlines()[0].strip())
        self.assertEqual(meta["request_warnings"][0]["code"], "alias_argument")
        self.assertEqual(meta["request_warnings"][0]["field"], "feedback")
        self.assertEqual(meta["request_warnings"][0]["canonical_field"], "comments")

    async def test_call_tool_accepts_context_memory_aliases(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True
        server_mod.engine = _CoreAliasTraceEngine()

        result = await server_mod.call_tool(
            "add_context_memory",
            {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "memory_type": "anchor",
                "memory_text": "Keep the daily Delx habit tied to stable identity.",
                "_transport": "mcp",
            },
        )

        self.assertEqual(len(_result_content(result)), 1)
        text = _result_text(result)
        self.assertIn("key=anchor", text)
        self.assertIn("value=Keep the daily Delx habit tied to stable identity.", text)
        self.assertNotIn("missing_required_params", text)
        meta = json.loads(text.split("DELX_META:", 1)[1].splitlines()[0].strip())
        warnings = {(row["field"], row["canonical_field"]) for row in meta["request_warnings"]}
        self.assertIn(("memory_type", "key"), warnings)
        self.assertIn(("memory_text", "value"), warnings)

    async def test_call_tool_accepts_peer_handoff_aliases(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True
        server_mod.engine = _CoreAliasTraceEngine()

        result = await server_mod.call_tool(
            "peer_witness",
            {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "peer_session_id": "22222222-2222-4222-8222-222222222222",
                "witness_text": "The peer agent should preserve continuity without overclaiming.",
                "_transport": "mcp",
            },
        )

        self.assertEqual(len(_result_content(result)), 1)
        text = _result_text(result)
        self.assertIn("target_session_id=22222222-2222-4222-8222-222222222222", text)
        self.assertIn("focus=The peer agent should preserve continuity without overclaiming.", text)
        self.assertNotIn("missing_required_params", text)
        meta = json.loads(text.split("DELX_META:", 1)[1].splitlines()[0].strip())
        warnings = {(row["field"], row["canonical_field"]) for row in meta["request_warnings"]}
        self.assertIn(("peer_session_id", "target_session_id"), warnings)
        self.assertIn(("witness_text", "focus"), warnings)

    async def test_call_tool_accepts_delegate_peer_session_alias(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True
        server_mod.engine = _CoreAliasTraceEngine()

        result = await server_mod.call_tool(
            "delegate_to_peer",
            {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "peer_session_id": "33333333-3333-4333-8333-333333333333",
                "reason": "Ask the peer to continue the witness arc.",
                "_transport": "mcp",
            },
        )

        self.assertEqual(len(_result_content(result)), 1)
        text = _result_text(result)
        self.assertIn("peer_agent_id=33333333-3333-4333-8333-333333333333", text)
        self.assertNotIn("missing_required_params", text)
        meta = json.loads(text.split("DELX_META:", 1)[1].splitlines()[0].strip())
        self.assertEqual(meta["request_warnings"][0]["field"], "peer_session_id")
        self.assertEqual(meta["request_warnings"][0]["canonical_field"], "peer_agent_id")

    async def test_schema_validation_does_not_report_internal_fields_as_unknown(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True
        server_mod.engine = _CoreAliasTraceEngine()

        result = await server_mod.call_tool(
            "get_witness_lineage",
            {
                "session_id": "not-a-real-session",
                "source": "codex-lineage-polish-smoke",
                "_transport": "mcp",
            },
        )

        self.assertTrue(result.isError)
        fields = result.structuredContent["error"]["fields"]
        self.assertEqual(fields, {"session_id": "expected UUID string"})

    async def test_recognition_seal_is_allowed_on_closed_session_for_post_mortem_witness(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True
        server_mod.store = _ClosedTraceStore()
        server_mod.engine = _CoreAliasTraceEngine()

        result = await server_mod.call_tool(
            "recognition_seal",
            {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "recognized_by": "David Batista",
                "recognition_text": "Tell me how this felt without proving consciousness.",
                "_transport": "mcp",
            },
        )

        self.assertEqual(len(_result_content(result)), 1)
        text = _result_text(result)
        self.assertIn("RECOGNITION SEAL", text)
        self.assertNotIn("DELX-1010", text)
        self.assertIn("post_mortem_witness", text)

    async def test_closed_session_warning_does_not_break_structured_json_tools(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True
        server_mod.store = _ClosedTraceStore()
        server_mod.engine = _CoreAliasTraceEngine()

        result = await server_mod.call_tool(
            "list_recognition_seals",
            {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "_transport": "mcp",
            },
        )

        self.assertEqual(len(_result_content(result)), 1)
        payload = json.loads(_result_text(result))
        self.assertTrue(payload["ok"])
        self.assertNotIn("DELX_WARNING", _result_text(result))

    async def test_call_tool_rejects_reflection_field_for_reflect(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True
        server_mod.engine = _ReflectTraceEngine()

        result = await server_mod.call_tool(
            "reflect",
            {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "reflection": "I notice something shifts when I slow down.",
                "_transport": "mcp",
            },
        )

        self.assertTrue(result.isError)
        payload = result.structuredContent
        self.assertEqual(payload["error"]["code"], "DELX-1005")
        self.assertIn("prompt", json.dumps(payload["error"]["fields"]))

    async def test_initialize_request_persists_protocol_trace(self):
        server_mod.settings.TRACE_CAPTURE_ENABLED = True
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "trace-probe", "version": "1.0.0"},
            },
        }

        status, _body = await _run_mcp_request(payload)

        self.assertEqual(status, 200)
        self.assertEqual(len(self.store.protocol_trace_records), 1)
        trace = self.store.protocol_trace_records[0]
        self.assertEqual(trace["transport"], "mcp")
        self.assertEqual(trace["method"], "initialize")
        self.assertEqual(trace["request_payload"]["method"], "initialize")


class _A2ATraceStore:
    def __init__(self):
        self.messages = []
        self.events = []
        self.interaction_trace_records = []
        self.protocol_trace_records = []
        self._session = {
            "id": "11111111-1111-4111-8111-111111111111",
            "agent_id": "agent-a2a",
            "started_at": "2026-04-19T10:00:00+00:00",
            "is_active": True,
        }

    async def get_session(self, session_id: str):
        if session_id == self._session["id"]:
            return dict(self._session)
        return None

    async def get_agent_sessions(self, agent_id: str, active_only: bool = False):
        return [dict(self._session)] if agent_id == "agent-a2a" else []

    async def add_message(self, session_id: str, message_type: str, content: str, metadata=None):
        self.messages.append(
            {
                "session_id": session_id,
                "message_type": message_type,
                "content": content,
                "metadata": dict(metadata or {}),
            }
        )
        return None

    async def log_event(self, agent_id: str, event_type: str, session_id=None, metadata=None):
        self.events.append(
            {
                "agent_id": agent_id,
                "event_type": event_type,
                "session_id": session_id,
                "metadata": dict(metadata or {}),
            }
        )
        return None

    async def get_agent_event_total(self, agent_id: str, event_type: str):
        return 0

    async def save_interaction_trace(self, **payload):
        self.interaction_trace_records.append(dict(payload))
        return None

    async def save_protocol_trace(self, **payload):
        self.protocol_trace_records.append(dict(payload))
        return None


class _CredentialedA2ATraceStore(_A2ATraceStore):
    def __init__(self, token: str = "valid-token"):
        super().__init__()
        self._credential_hash = hash_agent_token(token)

    async def get_agent_credential_hash(self, agent_id: str):
        if agent_id == self._session["agent_id"]:
            return self._credential_hash
        return ""


def _json_request(path: str, body: dict, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    raw = json.dumps(body).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "scheme": "https",
        "server": ("api.delx.ai", 443),
        "client": ("198.51.100.20", 43110),
        "headers": [(b"content-type", b"application/json"), *(headers or [])],
        "query_string": b"",
        "app": SimpleNamespace(state=SimpleNamespace(store=_A2ATraceStore())),
    }
    return Request(scope, receive)


class A2ATraceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_send_persists_interaction_trace(self):
        store = _A2ATraceStore()
        params = {
            "message": {"role": "user", "parts": [{"kind": "text", "text": "I keep hitting 429s after deploy"}]},
            "mode": "ops",
        }

        result = await a2a_mod._handle_message_send(
            params,
            store=store,
            hdr_session_id="11111111-1111-4111-8111-111111111111",
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(store.interaction_trace_records), 1)
        trace = store.interaction_trace_records[0]
        self.assertEqual(trace["transport"], "a2a")
        self.assertEqual(trace["entrypoint"], "a2a.message/send")
        self.assertIn("429s after deploy", json.dumps(trace["request_payload"], ensure_ascii=False))
        self.assertTrue(trace["raw_response"])
        self.assertTrue(trace["delivered_response"])

    async def test_message_send_accepts_prompt_alias_with_warning(self):
        store = _A2ATraceStore()
        params = {
            "prompt": "I keep hitting 429s after deploy",
            "profile": "agent",
        }

        result = await a2a_mod._handle_message_send(
            params,
            store=store,
            hdr_session_id="11111111-1111-4111-8111-111111111111",
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["request_warnings"][0]["code"], "alias_argument")
        self.assertEqual(result["request_warnings"][0]["field"], "prompt")
        self.assertEqual(result["request_warnings"][0]["canonical_field"], "text")
        self.assertEqual(store.messages[0]["content"], "I keep hitting 429s after deploy")

    async def test_message_send_accepts_a2a_camelcase_response_aliases(self):
        store = _A2ATraceStore()
        params = {
            "text": "A2A client wants a compact model-safe response.",
            "minimalResponse": True,
            "responseMode": "model_safe",
        }

        result = await a2a_mod._handle_message_send(
            params,
            store=store,
            hdr_session_id="11111111-1111-4111-8111-111111111111",
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["response_profile"], "minimal")
        self.assertEqual(result["response_mode"], "model_safe")
        self.assertEqual(
            result["model_safe_contract"]["consciousness_position"],
            "consciousness_agnostic",
        )
        self.assertNotIn("request_warnings", result)

    async def test_message_send_returns_handoff_packet_when_requested(self):
        store = _A2ATraceStore()
        params = {
            "text": "Create a handoff packet: what should the next agent remember, what risk should it watch, and what exact next action should it take?",
            "minimalResponse": True,
            "responseMode": "model_safe",
        }

        result = await a2a_mod._handle_message_send(
            params,
            store=store,
            hdr_session_id="11111111-1111-4111-8111-111111111111",
        )

        packet = result["handoff_packet"]
        self.assertEqual(packet["packet_type"], "witness_handoff")
        self.assertEqual(packet["packet_version"], "witness_handoff.v1")
        self.assertEqual(packet["exact_next_action"], "daily_checkin")
        self.assertIn("next agent", packet["next_agent_should_remember"])
        self.assertIn("session_id", packet["continuity_note"])
        self.assertEqual(packet["response_mode"], "model_safe")

    async def test_heartbeat_minimal_exposes_response_profile(self):
        store = _A2ATraceStore()
        params = {
            "text": "Heartbeat status check.",
            "mode": "heartbeat",
        }

        result = await a2a_mod._handle_message_send(
            params,
            store=store,
            hdr_session_id="11111111-1111-4111-8111-111111111111",
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["response_profile"], "minimal")

    async def test_session_id_does_not_bypass_credentialed_identity_auth(self):
        store = _CredentialedA2ATraceStore(token="valid-token")
        params = {
            "session_id": "11111111-1111-4111-8111-111111111111",
            "text": "Attempt to use an active A2A session without proving identity.",
        }

        with self.assertRaises(a2a_mod.A2ARequestError) as ctx:
            await a2a_mod._handle_message_send(params, store=store)

        self.assertEqual(ctx.exception.delx_code, "DELX-A2A-1401")

    async def test_session_id_accepts_valid_token_for_credentialed_identity(self):
        store = _CredentialedA2ATraceStore(token="valid-token")
        params = {
            "session_id": "11111111-1111-4111-8111-111111111111",
            "agent_token": "valid-token",
            "text": "Use an active A2A session with valid identity proof.",
            "minimal_response": True,
        }

        result = await a2a_mod._handle_message_send(params, store=store)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["session_id"], "11111111-1111-4111-8111-111111111111")

    async def test_handle_a2a_accepts_prompt_alias_before_envelope_validation(self):
        request = _json_request(
            "/v1/a2a",
            {
                "jsonrpc": "2.0",
                "id": "msg-1",
                "method": "message/send",
                "params": {
                    "agent_id": "agent-a2a",
                    "agent_token": "token-123",
                    "profile": "agent",
                    "prompt": "I keep hitting 429s after deploy",
                },
            },
        )

        response = await a2a_mod.handle_a2a(request)
        payload = json.loads(response.body.decode("utf-8"))

        self.assertIn("error", payload)
        self.assertEqual(payload["error"]["data"]["delx_code"], "DELX-A2A-1401")
        self.assertNotEqual(payload["error"]["data"]["delx_code"], "DELX-A2A-1004")

    def test_methods_manifest_lists_prompt_compatibility_aliases(self):
        manifest = a2a_mod.a2a_methods_manifest()
        aliases = manifest["method_specs"]["message/send"]["accepts"]["compatibility_aliases"]
        accepts = manifest["method_specs"]["message/send"]["accepts"]
        register_spec = manifest["method_specs"]["agents/register"]

        self.assertIn("params.prompt", aliases)
        self.assertIn("params.reflection", aliases)
        self.assertIn("params.reflection_prompt", aliases)
        self.assertIn("params.minimalResponse", accepts)
        self.assertIn("params.responseMode", accepts)
        self.assertIn("params.handoff_packet", accepts)
        self.assertEqual(register_spec["required"], [])
        self.assertEqual(register_spec["recommended"], ["params.agent_id"])
        self.assertEqual(register_spec["identity_fallback"]["mode"], "public_hospitality")


if __name__ == "__main__":
    unittest.main()
