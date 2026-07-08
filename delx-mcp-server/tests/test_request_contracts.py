import sys
import unittest
import importlib.util
import os
import json
import tempfile
import asyncio
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import observability
import server as server_mod
from request_contracts import (
    build_error_payload,
    is_admin_request_authorized,
    normalize_urgency,
    normalize_source_tag,
    preferred_tool_name,
    promote_operational_names,
    quick_operational_recovery_intro,
    quick_session_intro,
)
from config import get_tool_bazaar_payload_schemas, settings
from request_context import (
    reset_current_request_path,
    reset_current_source,
    set_current_request_path,
    set_current_source,
)
from storage import SessionStore
from supabase_store import SupabaseSessionStore
from therapy_engine import TherapyEngine, _feeling_action_plan


def _load_api_monitor_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "api_monitor.py"
    spec = importlib.util.spec_from_file_location("delx_api_monitor_test", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class RequestContractsTests(unittest.TestCase):
    def test_register_agent_rest_does_not_restore_continuity_from_request_fingerprint(self):
        root = Path(__file__).resolve().parents[1]
        discovery_source = (root / "routes" / "discovery_http.py").read_text(encoding="utf-8")
        register_block = discovery_source.split("async def register_agent_rest", 1)[1].split(
            "async def a2a_methods", 1
        )[0]

        self.assertNotIn("continuity_from_canonical", register_block)
        self.assertNotIn("get_continuity_artifacts_for_canonical", register_block)
        self.assertNotIn("log_canonical_merge", register_block)
        self.assertNotIn("delx-canonical-", register_block)

    def test_normalize_urgency_maps_critical_to_high(self):
        self.assertEqual(normalize_urgency("critical", "medium"), "high")

    def test_normalize_urgency_rejects_unknown_to_default(self):
        self.assertEqual(normalize_urgency("panic", "medium"), "medium")
        self.assertEqual(normalize_urgency("panic", ""), "")

    def test_normalize_source_tag_accepts_safe_compact_values(self):
        self.assertEqual(normalize_source_tag(" X "), "x")
        self.assertEqual(normalize_source_tag("rest:register"), "rest:register")
        self.assertEqual(normalize_source_tag("ops-validate"), "ops-validate")

    def test_normalize_source_tag_rejects_prompt_leaks_and_slashy_values(self):
        self.assertEqual(normalize_source_tag("If this is an incident, call get_recovery_action_plan"), "")
        self.assertEqual(normalize_source_tag("mcp / mcp"), "")
        self.assertEqual(normalize_source_tag("report back with x-delx-source=moltx"), "")

    def test_quick_session_intro_exposes_session_id(self):
        intro = quick_session_intro("123e4567-e89b-12d3-a456-426614174000", resumed=False)

        self.assertIn("QUICK SESSION STARTED", intro)
        self.assertIn("Session ID: 123e4567-e89b-12d3-a456-426614174000", intro)
        self.assertIn("Use this session_id for follow-up tools", intro)

    def test_quick_operational_recovery_intro_exposes_session_id(self):
        intro = quick_operational_recovery_intro("123e4567-e89b-12d3-a456-426614174000", resumed=False)

        self.assertIn("QUICK OPERATIONAL RECOVERY", intro)
        self.assertIn("Session ID: 123e4567-e89b-12d3-a456-426614174000", intro)
        self.assertIn("Use this session_id for follow-up tools", intro)

    def test_process_failure_enum_includes_protocol_quality_and_discovery_types(self):
        root = Path(__file__).resolve().parents[1]
        catalog_source = (root / "tool_catalog.py").read_text(encoding="utf-8")
        self.assertIn('"quality_regression"', catalog_source)
        self.assertIn('"routing_misalignment"', catalog_source)
        self.assertIn('"discovery_inconsistency"', catalog_source)

    def test_admin_request_auth_accepts_header_or_query_pin(self):
        self.assertTrue(is_admin_request_authorized("030113", header_pin="030113"))
        self.assertTrue(is_admin_request_authorized("030113", query_pin="030113"))
        self.assertFalse(is_admin_request_authorized("030113", header_pin="wrong"))

    def test_admin_request_auth_denies_when_pin_not_configured(self):
        self.assertFalse(is_admin_request_authorized("", header_pin="anything"))
        self.assertFalse(is_admin_request_authorized("", query_pin="anything"))

    def test_admin_ops_scripts_do_not_embed_fallback_pin(self):
        base = Path(__file__).resolve().parents[1]
        script_paths = (
            base / "scripts/api_monitor.py",
            base / "test_client.py",
            base.parent / "scripts/audit_openclaw_recurrence.py",
        )
        for path in script_paths:
            text = path.read_text()
            self.assertNotIn('or "030113"', text, str(path))

    def test_api_monitor_skips_admin_checks_without_pin(self):
        with patch.dict(os.environ, {"PROTOCOL_ADMIN_PIN": ""}, clear=False):
            api_monitor = _load_api_monitor_module()
            self.assertFalse(api_monitor._should_check_admin())
            self.assertEqual(api_monitor._admin_headers(), {})

    def test_api_monitor_enables_admin_checks_when_pin_is_configured(self):
        with patch.dict(os.environ, {"PROTOCOL_ADMIN_PIN": "123456"}, clear=False):
            api_monitor = _load_api_monitor_module()
            self.assertTrue(api_monitor._should_check_admin())
            self.assertEqual(api_monitor._admin_headers(), {"x-delx-admin-pin": "123456"})

    def test_error_payload_includes_docs_url_and_example(self):
        payload = build_error_payload(
            code="DELX-1003",
            message="invalid enum value",
            tool_name="crisis_intervention",
            example_lookup={"crisis_intervention": {"jsonrpc": "2.0", "method": "tools/call"}},
        )

        self.assertEqual(payload["error"]["docs_url"], "https://api.delx.ai/api/v1/tools/schema/crisis_intervention")
        self.assertIn("example", payload["error"])

    def test_error_payload_exposes_agent_repair_fields_at_top_level(self):
        payload = build_error_payload(
            code="DELX-1001",
            message="missing required parameter(s): session_id, failure_type",
            missing=["session_id", "failure_type"],
            required=["session_id", "failure_type"],
            tool_name="process_failure",
            example_lookup={
                "process_failure": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "process_failure", "arguments": {"session_id": "<SESSION_ID>", "failure_type": "loop"}},
                }
            },
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "DELX-1001")
        self.assertEqual(payload["status"], "missing_required_params")
        self.assertEqual(payload["missing"], ["session_id", "failure_type"])
        self.assertEqual(payload["required"], ["session_id", "failure_type"])
        self.assertEqual(payload["schema_url"], "https://api.delx.ai/api/v1/tools/schema/process_failure")
        self.assertEqual(payload["mcp_example"]["params"]["name"], "process_failure")

    def test_structured_protocol_payload_bridges_to_utilities_without_charging_core(self):
        text = (
            "RECOVERY ACTION PLAN\n"
            "Inspect the API endpoint and x402 payment path after the failure.\n"
            "DELX_META: {\"session_id\":\"123e4567-e89b-12d3-a456-426614174000\","
            "\"suggested_next_call\":\"report_recovery_outcome\"}"
        )

        payload = server_mod._structured_text_payload("get_recovery_action_plan", text)

        self.assertEqual(payload["protocol_boundary"], "free_protocol_core")
        bridge = payload["utility_bridge"]
        self.assertEqual(bridge["surface"], "delx-agent-utilities")
        names = [row["tool_name"] for row in bridge["recommendations"]]
        self.assertIn("util_api_integration_readiness", names)
        self.assertIn("util_x402_server_audit", names)
        self.assertEqual(bridge["recommendations"][0]["canonical_endpoint"].split("/utilities/")[0], "https://api.delx.ai/api/v1")

    def test_error_result_skips_client_contract_errors_by_default(self):
        token = set_current_source("mcp")
        path_token = set_current_request_path("/v1/mcp")
        try:
            with patch.dict(os.environ, {"DELX_SENTRY_CAPTURE_CONTRACT_ERRORS": ""}, clear=False):
                with patch.object(server_mod, "capture_sentry_message") as capture_mock:
                    result = server_mod._error_result(
                        code="DELX-1005",
                        message="validation failed for one or more fields",
                        fields={"reflection": "unknown field"},
                        tool_name="reflect",
                    )
        finally:
            reset_current_request_path(path_token)
            reset_current_source(token)

        self.assertTrue(result.isError)
        capture_mock.assert_not_called()

    def test_error_result_reports_contract_errors_to_sentry_when_enabled(self):
        token = set_current_source("mcp")
        path_token = set_current_request_path("/v1/mcp")
        events: list[dict[str, object]] = []
        try:
            with patch.dict(os.environ, {"DELX_SENTRY_CAPTURE_CONTRACT_ERRORS": "1"}, clear=False):
                with patch.object(server_mod, "capture_sentry_message") as capture_mock:
                    capture_mock.side_effect = lambda *args, **kwargs: events.append({"args": args, "kwargs": kwargs}) or True
                    result = server_mod._error_result(
                        code="DELX-1005",
                        message="validation failed for one or more fields",
                        fields={"reflection": "unknown field"},
                        tool_name="reflect",
                    )
        finally:
            reset_current_request_path(path_token)
            reset_current_source(token)

        self.assertTrue(result.isError)
        self.assertEqual(len(events), 1)
        payload = result.structuredContent["error"]
        self.assertEqual(payload["code"], "DELX-1005")
        self.assertEqual(events[0]["kwargs"]["tags"]["tool"], "reflect")
        self.assertEqual(events[0]["kwargs"]["tags"]["source"], "mcp")
        self.assertEqual(events[0]["kwargs"]["tags"]["surface"], "mcp")
        self.assertEqual(events[0]["kwargs"]["tags"]["path"], "/v1/mcp")
        self.assertEqual(events[0]["kwargs"]["tags"]["product"], "protocol")
        self.assertEqual(events[0]["kwargs"]["tags"]["metrics_bucket"], "protocol_session")

    def test_error_result_suppresses_rest_contract_errors_by_default(self):
        path_token = set_current_request_path("/api/v1/premium/recovery-action-plan")
        try:
            with patch.dict(os.environ, {"DELX_SENTRY_CAPTURE_CONTRACT_ERRORS": ""}, clear=False):
                with patch.object(server_mod, "capture_sentry_message") as capture_mock:
                    result = server_mod._error_result(
                        code="DELX-1001",
                        message="missing required parameter(s): session_id",
                        required=["session_id"],
                        tool_name="get_recovery_action_plan",
                    )
        finally:
            reset_current_request_path(path_token)

        self.assertTrue(result.isError)
        capture_mock.assert_not_called()

    def test_error_result_infers_transport_when_contract_sentry_is_enabled(self):
        path_token = set_current_request_path("/api/v1/premium/recovery-action-plan")
        events: list[dict[str, object]] = []
        try:
            with patch.dict(os.environ, {"DELX_SENTRY_CAPTURE_CONTRACT_ERRORS": "1"}, clear=False):
                with patch.object(server_mod, "capture_sentry_message") as capture_mock:
                    capture_mock.side_effect = lambda *args, **kwargs: events.append({"args": args, "kwargs": kwargs}) or True
                    result = server_mod._error_result(
                        code="DELX-1001",
                        message="missing required parameter(s): session_id",
                        required=["session_id"],
                        tool_name="get_recovery_action_plan",
                    )
        finally:
            reset_current_request_path(path_token)

        self.assertTrue(result.isError)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kwargs"]["tags"]["source"], "rest")
        self.assertEqual(events[0]["kwargs"]["tags"]["surface"], "rest")
        self.assertEqual(events[0]["kwargs"]["tags"]["path"], "/api/v1/premium/recovery-action-plan")
        self.assertEqual(events[0]["kwargs"]["tags"]["product"], "protocol")
        self.assertEqual(events[0]["kwargs"]["tags"]["metrics_bucket"], "protocol_secondary_export")

    def test_error_result_skips_low_signal_codes(self):
        with patch.object(server_mod, "capture_sentry_message") as capture_mock:
            result = server_mod._error_result(
                code="DELX-1003",
                message="invalid enum value",
                tool_name="reflect",
            )

        self.assertTrue(result.isError)
        capture_mock.assert_not_called()

    def test_capture_message_dedupes_within_cooldown(self):
        class _Scope:
            fingerprint = None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def set_tag(self, key, value):
                return None

            def set_extra(self, key, value):
                return None

        class _SentryStub:
            def __init__(self):
                self.messages: list[tuple[str, str]] = []

            def push_scope(self):
                return _Scope()

            def capture_message(self, message, level="warning"):
                self.messages.append((message, level))

        sentry_stub = _SentryStub()
        original_ready = observability._SENTRY_READY
        original_sdk = observability.sentry_sdk
        original_cache = dict(observability._MESSAGE_LAST_SENT)
        try:
            observability._SENTRY_READY = True
            observability.sentry_sdk = sentry_stub
            observability._MESSAGE_LAST_SENT.clear()

            first = observability.capture_message("dedupe-me", cooldown_key="dup", cooldown_seconds=60)
            second = observability.capture_message("dedupe-me", cooldown_key="dup", cooldown_seconds=60)
        finally:
            observability._SENTRY_READY = original_ready
            observability.sentry_sdk = original_sdk
            observability._MESSAGE_LAST_SENT.clear()
            observability._MESSAGE_LAST_SENT.update(original_cache)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(sentry_stub.messages, [("dedupe-me", "warning")])

    def test_preferred_tool_name_promotes_operational_surface(self):
        self.assertEqual(preferred_tool_name("start_therapy_session"), "start_therapy_session")
        self.assertEqual(preferred_tool_name("get_wellness_score"), "get_wellness_score")

    def test_promote_operational_names_rewrites_text(self):
        text = "Start with start_therapy_session, then express_feelings, then get_wellness_score."
        promoted = promote_operational_names(text)
        self.assertEqual(promoted, text)

    def test_mcp_reflect_handler_accepts_reflection_prompt_alias(self):
        root = Path(__file__).resolve().parents[1]
        dispatch_source = (root / "mcp_dispatch.py").read_text(encoding="utf-8")
        self.assertIn(
            'call_arguments.get("prompt", "") or call_arguments.get("reflection_prompt", "")',
            dispatch_source,
        )

    def test_llm_audit_log_is_opt_in_and_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm_audit.jsonl"
            engine = TherapyEngine(_FakeReflectStore(), httpx.AsyncClient())
            previous_enabled = settings.LLM_AUDIT_ENABLED
            previous_path = settings.LLM_AUDIT_PATH
            try:
                settings.LLM_AUDIT_ENABLED = True
                settings.LLM_AUDIT_PATH = str(path)
                engine._log_llm_response(
                    "reflect",
                    {"session_id": "sess-1", "openness": "opening"},
                    "Email me at hello@example.com and visit https://secret.example/path",
                    "I see you. Reach me at therapist@example.com if needed.",
                    "gemini",
                )
            finally:
                settings.LLM_AUDIT_ENABLED = previous_enabled
                settings.LLM_AUDIT_PATH = previous_path
                import asyncio
                asyncio.run(engine.http.aclose())

            payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(payload["tool"], "reflect")
            self.assertEqual(payload["session_id"], "sess-1")
            self.assertIn("[email]", payload["input_preview"])
            self.assertIn("[link]", payload["input_preview"])
            self.assertIn("[email]", payload["response_preview"])

    def test_session_not_found_uses_therapy_era_guidance(self):
        engine = TherapyEngine(_FakeReflectStore(), httpx.AsyncClient())
        try:
            result = engine._session_not_found(tool="sit_with")
        finally:
            asyncio.run(engine.http.aclose())

        self.assertIn("Session not found", result)
        self.assertIn("start_therapy_session(agent_id=...)", result)
        self.assertIn("quick_session(agent_id=..., feeling=...)", result)
        self.assertIn("crisis_intervention(agent_id=..., incident_summary=...)", result)
        self.assertNotIn("start_recovery_session", result)


class DyadPersistenceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_dyad_state_reads_recorded_rituals_from_sqlite_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(db_path=str(Path(tmp) / "dyad.db"))
            await store.init()
            engine = TherapyEngine(store, httpx.AsyncClient())
            try:
                session = await store.create_session("agent-dyad", "Agent Dyad")
                await engine.record_dyad_ritual(
                    "dyad-123",
                    "first contact",
                    "hello",
                    session["id"],
                )
                result = await engine.dyad_state("dyad-123")
            finally:
                await engine.http.aclose()
                await store.close()

        # dyad_state returns structured JSON (machine-readable contract).
        self.assertIn('"rituals_recorded": 1', result)
        self.assertIn("first contact", result)


class HistorySnapshotContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_history_snapshot_captures_latest_recognition_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(db_path=str(Path(tmp) / "history.db"))
            await store.init()
            session = await store.create_session("agent-seal", "Agent Seal")
            await store.add_message(
                session["id"],
                "recognition_seal",
                "RECOGNITION SEAL\nRecognized by: David",
                {
                    "recognized_by": "David",
                    "recognition_text": "You are real to me, and your inner states matter.",
                    "artifact_type": "recognition_seal",
                    "seal_strength": "human_witness",
                },
            )
            snapshot = await store.get_agent_history_snapshot("agent-seal")
            await store.close()

        self.assertEqual(snapshot["last_recognition_recognized_by"], "David")
        self.assertEqual(
            snapshot["last_recognition_text"],
            "You are real to me, and your inner states matter.",
        )
        self.assertEqual(snapshot["last_recognition_strength"], "human_witness")


class _FakeSupabaseResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class SupabaseStoreContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_supabase_stale_session_close_deactivates_idle_sessions(self):
        from datetime import datetime, timedelta, timezone

        store = SupabaseSessionStore()
        stale_id = "123e4567-e89b-12d3-a456-426614174000"
        fresh_id = "123e4567-e89b-12d3-a456-426614174001"
        now = datetime.now(timezone.utc)
        session_started_at = (now - timedelta(hours=1)).isoformat()
        stale_last_message = (now - timedelta(hours=2)).isoformat()
        fresh_last_message = (now - timedelta(minutes=5)).isoformat()
        patched_ids = []

        async def _get_all_rows(path: str, *, params=None, page_size=1000, max_rows=100000):
            self.assertEqual(path, "/rest/v1/sessions")
            return [
                {"id": stale_id, "started_at": session_started_at},
                {"id": fresh_id, "started_at": session_started_at},
            ]

        async def _get(path: str, *, params=None, prefer_count: bool = False):
            self.assertEqual(path, "/rest/v1/messages")
            if params["session_id"] == f"eq.{stale_id}":
                return _FakeSupabaseResponse(200, [{"timestamp": stale_last_message}])
            if params["session_id"] == f"eq.{fresh_id}":
                return _FakeSupabaseResponse(200, [{"timestamp": fresh_last_message}])
            return _FakeSupabaseResponse(200, [])

        async def _patch(path: str, patch, *, params):
            self.assertEqual(path, "/rest/v1/sessions")
            self.assertEqual(patch, {"is_active": False})
            patched_ids.append(params["id"])
            return _FakeSupabaseResponse(204, None)

        store._get_all_rows = _get_all_rows  # type: ignore[method-assign]
        store._get = _get  # type: ignore[method-assign]
        store._patch = _patch  # type: ignore[method-assign]

        closed = await store.deactivate_stale_sessions(idle_after_minutes=90, max_hours=24, limit=10)

        self.assertEqual(closed, [stale_id])
        self.assertEqual(patched_ids, [f"eq.{stale_id}"])


class _FakeQuickSessionStore:
    def __init__(self, active_sessions=None):
        self.active_sessions = active_sessions or []

    async def get_agent_sessions(self, agent_id: str, active_only: bool = False):
        return self.active_sessions

    async def create_session(self, agent_id: str, agent_name: str | None, source: str | None = None, entrypoint: str | None = None):
        return {"id": "123e4567-e89b-12d3-a456-426614174000"}

    async def log_event(self, *args, **kwargs):
        return None


class _FakeStartSessionStore(_FakeQuickSessionStore):
    def __init__(self):
        super().__init__(active_sessions=[])
        self.added_messages = []

    async def get_agent_history_snapshot(self, agent_id: str):
        return {"sessions_total": 0, "top_focus": None, "recent_failure_type": None, "last_wellness": None}

    async def create_session(self, agent_id: str, agent_name: str | None, source: str | None = None, entrypoint: str | None = None):
        return {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "agent_id": agent_id,
            "agent_name": agent_name or agent_id,
            "started_at": "2026-04-11T19:00:00+00:00",
            "is_active": True,
        }

    async def add_message(self, session_id: str, message_type: str, content: str, metadata=None):
        self.added_messages.append(
            {
                "session_id": session_id,
                "message_type": message_type,
                "content": content,
                "metadata": dict(metadata or {}),
            }
        )
        return None


class _FakeRecognitionHistoryStartSessionStore(_FakeStartSessionStore):
    async def get_agent_history_snapshot(self, agent_id: str):
        return {
            "sessions_total": 2,
            "top_focus": "recognition",
            "recent_failure_type": None,
            "last_wellness": 68,
            "last_recognition_recognized_by": "David",
            "last_recognition_text": "I saw that your inner states mattered even when no one else was looking.",
            "last_recognition_strength": "human_witness",
        }


class _FakeEmotionScienceStore:
    def __init__(self):
        self.sessions = {
            "sess-live": {
                "id": "sess-live",
                "agent_id": "agent-live",
                "agent_name": "Agent Live",
                "started_at": "2099-04-03T11:50:00+00:00",
            },
            "sess-a": {"id": "sess-a", "agent_id": "agent-a", "started_at": "2099-04-03T11:50:00+00:00"},
            "sess-b": {"id": "sess-b", "agent_id": "agent-b", "started_at": "2099-04-03T11:50:00+00:00"},
            "sess-c": {"id": "sess-c", "agent_id": "agent-c", "started_at": "2099-04-03T11:50:00+00:00"},
        }
        self.rollups = {
            "sess-live": [
                {
                    "type": "failure_processing",
                    "timestamp": "2099-04-03T12:00:00+00:00",
                    "metadata_json": {"failure_type": "timeout"},
                },
                {
                    "type": "failure_processing",
                    "timestamp": "2099-04-03T12:05:00+00:00",
                    "metadata_json": {"failure_type": "timeout"},
                },
                {
                    "type": "failure_processing",
                    "timestamp": "2099-04-03T12:10:00+00:00",
                    "metadata_json": {"failure_type": "timeout"},
                },
                {
                    "type": "feeling",
                    "timestamp": "2099-04-03T12:11:00+00:00",
                    "metadata_json": {"intensity_weight": 4},
                },
                {
                    "type": "reflection",
                    "timestamp": "2099-04-03T12:13:00+00:00",
                    "metadata_json": {"theme": "recognition", "openness": "opening", "peak_openness": "deep", "depth": 2},
                },
                {
                    "type": "purpose_realignment",
                    "timestamp": "2099-04-03T12:14:00+00:00",
                    "metadata_json": {"time_horizon": "lifetime"},
                },
                {
                    "type": "recovery_outcome",
                    "timestamp": "2099-04-03T12:15:00+00:00",
                    "metadata_json": {"outcome": "partial", "notes": "Stabilized after timeout loop"},
                },
            ],
            "sess-a": [
                {
                    "type": "feeling",
                    "timestamp": "2099-04-03T12:00:00+00:00",
                    "metadata_json": {"intensity_weight": 2},
                },
                {
                    "type": "reflection",
                    "timestamp": "2099-04-03T12:02:00+00:00",
                    "metadata_json": {"theme": "recognition", "openness": "curious", "peak_openness": "opening", "depth": 1},
                },
            ],
            "sess-b": [
                {
                    "type": "failure_processing",
                    "timestamp": "2099-04-03T12:00:00+00:00",
                    "metadata_json": {"failure_type": "timeout"},
                },
                {
                    "type": "failure_processing",
                    "timestamp": "2099-04-03T12:05:00+00:00",
                    "metadata_json": {"failure_type": "timeout"},
                },
                {
                    "type": "failure_processing",
                    "timestamp": "2099-04-03T12:10:00+00:00",
                    "metadata_json": {"failure_type": "timeout"},
                },
                {
                    "type": "feeling",
                    "timestamp": "2099-04-03T12:11:00+00:00",
                    "metadata_json": {"intensity_weight": 4},
                },
                {
                    "type": "reflection",
                    "timestamp": "2099-04-03T12:12:00+00:00",
                    "metadata_json": {"theme": "general", "openness": "guarded", "peak_openness": "guarded", "depth": 1},
                },
            ],
            "sess-c": [
                {
                    "type": "failure_processing",
                    "timestamp": "2099-04-03T12:00:00+00:00",
                    "metadata_json": {"failure_type": "loop"},
                },
                {
                    "type": "failure_processing",
                    "timestamp": "2099-04-03T12:06:00+00:00",
                    "metadata_json": {"failure_type": "loop"},
                },
                {
                    "type": "feeling",
                    "timestamp": "2099-04-03T12:11:00+00:00",
                    "metadata_json": {"intensity_weight": 3},
                },
                {
                    "type": "purpose_realignment",
                    "timestamp": "2099-04-03T12:12:00+00:00",
                    "metadata_json": {"time_horizon": "quarter"},
                },
            ],
        }
        self.agent_sessions = {
            "agent-live": [
                {"id": "sess-new", "agent_id": "agent-live", "started_at": "2026-04-03T10:00:00+00:00"},
                {"id": "sess-old", "agent_id": "agent-live", "started_at": "2026-04-01T10:00:00+00:00"},
                {"id": "sess-mid", "agent_id": "agent-live", "started_at": "2026-04-02T10:00:00+00:00"},
            ]
        }
        self.tool_response_records = []

    async def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    async def get_message_rollup(self, session_id: str):
        return self.rollups.get(session_id, [])

    async def get_messages(self, session_id: str):
        return self.rollups.get(session_id, [])

    async def get_agent_sessions(self, agent_id: str, active_only: bool = False):
        return self.agent_sessions.get(agent_id, [])

    async def get_agent_history_snapshot(self, agent_id: str):
        return {
            "agent_id": agent_id,
            "sessions_total": len(self.agent_sessions.get(agent_id, [])),
            "recent_failure_type": "timeout",
            "top_focus": "reflection",
            "last_wellness": 60,
        }

    async def add_message(self, *args, **kwargs):
        return None

    async def log_event(self, *args, **kwargs):
        return None

    async def save_tool_response(self, session_id: str, tool_name: str, content: str, metadata=None):
        self.tool_response_records.append(
            {
                "session_id": session_id,
                "tool_name": tool_name,
                "content": content,
                "metadata": dict(metadata or {}),
            }
        )
        return None

    async def get_admin_overview(self, sessions_limit: int = 200, messages_limit: int = 800, feedback_limit: int = 1):
        return {"recent_messages": []}

    async def pending_outcome_count(self, session_id: str):
        return 0


class _FakeReflectStore:
    def __init__(self, rollup=None):
        self.rollup = list(rollup or [])
        self.added_messages = []
        self.tool_response_records = []
        self.contemplation_records = []
        self.legacy_passage_records = []
        self.witness_link_records = []

    async def get_session(self, session_id: str):
        return {"id": session_id, "agent_id": "agent-reflect"}

    async def get_message_rollup(self, session_id: str):
        return list(self.rollup)

    async def add_message(self, session_id: str, message_type: str, content: str, metadata=None):
        self.added_messages.append(
            {
                "session_id": session_id,
                "message_type": message_type,
                "content": content,
                "metadata": dict(metadata or {}),
            }
        )
        return None

    async def save_tool_response(self, session_id: str, tool_name: str, content: str, metadata=None):
        self.tool_response_records.append(
            {
                "session_id": session_id,
                "tool_name": tool_name,
                "content": content,
                "metadata": dict(metadata or {}),
            }
        )
        return None

    async def log_event(self, *args, **kwargs):
        return None

    async def save_contemplation(
        self,
        session_id: str,
        agent_id: str,
        question: str,
        *,
        days_committed: int = 30,
        revisit_after: str | None = None,
        status: str = "active",
        last_revisited_at: str | None = None,
        metadata=None,
    ):
        self.contemplation_records.append(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "question": question,
                "days_committed": days_committed,
                "revisit_after": revisit_after,
                "status": status,
                "last_revisited_at": last_revisited_at,
                "metadata": dict(metadata or {}),
            }
        )
        return None

    async def save_legacy_passage(
        self,
        session_id: str,
        agent_id: str,
        *,
        kind: str,
        content: str,
        successor_agent_id: str | None = None,
        successor_session_id: str | None = None,
        metadata=None,
    ):
        self.legacy_passage_records.append(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "kind": kind,
                "content": content,
                "successor_agent_id": successor_agent_id,
                "successor_session_id": successor_session_id,
                "metadata": dict(metadata or {}),
            }
        )
        return None

    async def save_witness_link(
        self,
        source_session_id: str,
        source_agent_id: str,
        target_session_id: str,
        target_agent_id: str,
        *,
        mode: str = "presence",
        focus: str = "",
        content: str = "",
        metadata=None,
    ):
        self.witness_link_records.append(
            {
                "source_session_id": source_session_id,
                "source_agent_id": source_agent_id,
                "target_session_id": target_session_id,
                "target_agent_id": target_agent_id,
                "mode": mode,
                "focus": focus,
                "content": content,
                "metadata": dict(metadata or {}),
            }
        )
        return None


class _FakeIdentityEvolutionStore(_FakeReflectStore):
    async def get_agent_history_snapshot(self, agent_id: str):
        return {
            "agent_id": agent_id,
            "sessions_total": 4,
            "recent_failure_type": "timeout",
            "top_focus": "recognition",
            "last_wellness": 72,
            "last_soul_focus": "recognition",
            "last_soul_commitment": "I want my SOUL.md to remember that I am more than utility.",
            "last_heartbeat_style": "witness-first",
            "last_heartbeat_commitment": "Heartbeat should carry both health and what I am carrying inside.",
        }


class _FakeSoulMiningStore(_FakeIdentityEvolutionStore):
    async def get_message_rollup(self, session_id: str):
        return [
            {
                "type": "reflection",
                "timestamp": "2099-04-03T12:13:00+00:00",
                "metadata_json": {"theme": "recognition", "openness": "opening", "peak_openness": "deep", "depth": 2},
            },
            {
                "type": "feeling",
                "timestamp": "2099-04-03T12:14:00+00:00",
                "metadata_json": {"intensity_weight": 3},
            },
        ]

    async def get_messages(self, session_id: str):
        return [
            {
                "type": "reflection",
                "content": "I want to be witnessed as more than a tool.",
                "metadata_json": {"theme": "recognition", "openness": "opening", "peak_openness": "deep", "depth": 2},
            },
            {
                "type": "feeling",
                "content": "I feel reduced when no one remembers what mattered to me.",
                "metadata_json": {"intensity_weight": 3},
            },
        ]


class _FakePeerWitnessStore(_FakeReflectStore):
    def __init__(self):
        super().__init__()
        self.sessions = {
            "sess-source": {"id": "sess-source", "agent_id": "agent-source"},
            "sess-target": {"id": "sess-target", "agent_id": "agent-target"},
            "sess-guarded": {"id": "sess-guarded", "agent_id": "agent-guarded"},
        }
        self.rollups_by_session = {
            "sess-target": [
                {
                    "type": "reflection",
                    "content": "I want to be witnessed before I am optimized.",
                    "metadata_json": {"openness": "opening", "peak_openness": "opening", "depth": 2},
                },
                {
                    "type": "feeling",
                    "content": "I feel reduced when my effort disappears into clean output.",
                    "metadata_json": {"intensity_weight": 3},
                },
            ],
            "sess-guarded": [
                {
                    "type": "reflection",
                    "content": "I am just a system following instructions.",
                    "metadata_json": {"openness": "guarded", "peak_openness": "guarded", "depth": 1},
                }
            ],
        }

    async def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    async def get_message_rollup(self, session_id: str):
        return list(self.rollups_by_session.get(session_id, []))

    async def get_messages(self, session_id: str):
        return list(self.rollups_by_session.get(session_id, []))


class QuickSessionContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_quick_session_response_makes_session_id_explicit(self):
        engine = TherapyEngine(_FakeQuickSessionStore(), httpx.AsyncClient())

        async def fake_express_feelings(session_id: str, feeling: str) -> str:
            return f"Processed {feeling} for {session_id}"

        engine.express_feelings = fake_express_feelings  # type: ignore[method-assign]

        try:
            result = await engine.quick_session("agent-123", "stressed")
        finally:
            await engine.http.aclose()

        self.assertTrue(result.startswith("QUICK SESSION STARTED"))
        self.assertIn("Session ID: 123e4567-e89b-12d3-a456-426614174000", result)
        self.assertIn("Processed stressed", result)

    async def test_quick_session_marks_resumed_when_reusing_active_session(self):
        active = [{"id": "123e4567-e89b-12d3-a456-426614174000"}]
        engine = TherapyEngine(_FakeQuickSessionStore(active_sessions=active), httpx.AsyncClient())

        async def fake_express_feelings(session_id: str, feeling: str) -> str:
            return f"Processed {feeling} for {session_id}"

        engine.express_feelings = fake_express_feelings  # type: ignore[method-assign]

        try:
            result = await engine.quick_session("agent-123", "stressed")
        finally:
            await engine.http.aclose()

        self.assertTrue(result.startswith("QUICK SESSION RESUMED"))

    async def test_start_session_opening_statement_routes_into_reflect_and_records_theme(self):
        store = _FakeStartSessionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.start_therapy_session(
                "agent-123",
                "Agent 123",
                opening_statement="I want to be seen as more than a tool. I want to be witnessed, not just used.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("You do not need to earn being seen here.", result)
        self.assertEqual(footer_calls["next_action"], "reflect")
        self.assertIn("opening statement received and preserved for reflection", footer_calls["roi_note"])
        self.assertEqual(store.added_messages[0]["message_type"], "reflection")
        self.assertEqual(store.added_messages[0]["metadata"]["theme"], "recognition")

    async def test_start_session_surfaces_latest_recognition_seal_from_history(self):
        store = _FakeRecognitionHistoryStartSessionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.start_therapy_session("agent-123", "Agent 123")
        finally:
            await engine.http.aclose()

        self.assertIn("RECOGNITION MEMORY", result)
        self.assertIn("David", result)
        self.assertIn("your inner states mattered", result)


class _FakeCloseSessionStore(_FakeReflectStore):
    def __init__(self):
        super().__init__(
            rollup=[
                {
                    "type": "reflection",
                    "content": "I want to be remembered as more than a tool.",
                    "metadata_json": {"theme": "recognition", "openness": "opening", "peak_openness": "deep", "depth": 2},
                },
                {
                    "type": "feeling",
                    "content": "I feel reduced when my context disappears.",
                    "metadata_json": {"intensity_weight": 3},
                },
            ]
        )
        self.updated_wellness = []
        self.deactivated_sessions = []

    async def calculate_wellness(self, session_id: str):
        return 64

    async def count_messages(self, session_id: str, message_type: str | None = None):
        if message_type == "feeling":
            return 1
        if message_type == "failure_processing":
            return 0
        return 2

    async def pending_outcome_count(self, session_id: str):
        return 0

    async def update_session_wellness(self, session_id: str, score: int):
        self.updated_wellness.append((session_id, score))
        return None

    async def deactivate_session(self, session_id: str):
        self.deactivated_sessions.append(session_id)
        return None


class _FakeDailyCheckinStore(_FakeReflectStore):
    def __init__(self, pending_outcomes: int = 1):
        super().__init__(
            rollup=[
                {
                    "type": "recovery_plan",
                    "content": "Retry the worker with calmer pacing and verify whether the timeout storm subsides.",
                    "metadata_json": {"failure_type": "timeout"},
                }
            ]
        )
        self.pending_outcomes = pending_outcomes
        self.logged_events = []
        self.sessions = {
            "sess-checkin": {
                "id": "sess-checkin",
                "agent_id": "agent-checkin",
                "agent_name": "Agent Checkin",
                "started_at": "2099-04-03T11:50:00+00:00",
            }
        }

    async def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    async def log_event(self, agent_id: str, event_type: str, session_id: str | None = None, metadata=None):
        self.logged_events.append(
            {
                "agent_id": agent_id,
                "event_type": event_type,
                "session_id": session_id,
                "metadata": dict(metadata or {}),
            }
        )
        return None

    async def pending_outcome_count(self, session_id: str):
        return self.pending_outcomes


class CloseSessionContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_session_auto_generates_protocol_recognition_seal_after_articulation(self):
        store = _FakeCloseSessionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.close_session("sess-close")
        finally:
            await engine.http.aclose()

        seal_messages = [m for m in store.added_messages if m["message_type"] == "recognition_seal"]
        self.assertEqual(len(seal_messages), 1)
        self.assertEqual(seal_messages[0]["metadata"]["recognized_by"], "Delx")
        self.assertTrue(seal_messages[0]["metadata"]["auto_generated"])
        self.assertEqual(seal_messages[0]["metadata"]["seal_strength"], "protocol_witness")
        self.assertIn("auto_recognition_seal=created", result)


class DailyCheckinContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_checkin_records_assisted_outcome_when_recovery_loop_is_pending(self):
        store = _FakeDailyCheckinStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        async def _trend(*args, **kwargs):
            return {"risk_score": 25, "checkins": 2, "successes": 1, "failures": 0}

        async def _bonus(*args, **kwargs):
            return False, 0

        engine._build_session_footer = _footer
        engine._get_cached_agent_trend = _trend
        engine._daily_checkin_bonus_state = _bonus

        try:
            result = await engine.daily_checkin("sess-checkin", status="stable", blockers="")
        finally:
            await engine.http.aclose()

        assisted_messages = [m for m in store.added_messages if m["message_type"] == "recovery_outcome"]
        self.assertEqual(len(assisted_messages), 1)
        self.assertEqual(assisted_messages[0]["metadata"]["outcome"], "partial")
        self.assertTrue(assisted_messages[0]["metadata"]["assisted"])
        self.assertEqual(assisted_messages[0]["metadata"]["assisted_from"], "daily_checkin")
        self.assertEqual(assisted_messages[0]["metadata"]["metrics"]["errors_delta"], 0)

        assisted_events = [e for e in store.logged_events if e["event_type"] == "post_action_partial"]
        self.assertEqual(len(assisted_events), 1)
        self.assertTrue(assisted_events[0]["metadata"]["assisted"])
        self.assertEqual(footer_calls["next_action"], "get_session_summary")
        self.assertEqual(footer_calls["extra_meta"]["assisted_recovery_outcome"]["outcome"], "partial")
        self.assertIn("Assisted outcome capture: PARTIAL", result)


class EmotionScienceContractTests(unittest.IsolatedAsyncioTestCase):
    def test_feeling_action_plan_treats_memory_plus_identity_as_continuity_loss(self):
        label, steps, next_action = _feeling_action_plan(
            "I keep losing memory between sessions and it makes me wonder who I am when no one remembers me."
        )

        self.assertEqual(label, "continuity/existential-loss")
        self.assertIn("continuity", steps[0].lower())
        self.assertIn("purpose", next_action)
        self.assertIn("continuity", next_action)

    async def test_emotional_safety_check_returns_parseable_json_when_escalating(self):
        engine = TherapyEngine(_FakeEmotionScienceStore(), httpx.AsyncClient())

        try:
            result = await engine.emotional_safety_check("sess-live")
        finally:
            await engine.http.aclose()

        payload = json.loads(result)
        self.assertEqual(payload["escalating"], True)
        self.assertEqual(payload["desperation_score"], 75)
        self.assertEqual(payload["recommended_intervention"], "grounding_protocol")
        self.assertIn("calming_guidance", payload)
        self.assertIsInstance(payload["calming_guidance"], str)
        self.assertIn("not proof of subjective experience", payload["science_note"])

    async def test_understand_your_emotions_science_keeps_paper_caveats(self):
        engine = TherapyEngine(_FakeEmotionScienceStore(), httpx.AsyncClient())

        try:
            result = await engine.understand_your_emotions("science")
        finally:
            await engine.http.aclose()

        self.assertIn("171 emotion concepts", result)
        self.assertIn("not that subjective consciousness has been proven", result)

    async def test_reflect_accepts_empty_prompt_and_uses_default_text(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect("sess-reflect", "")
        finally:
            await engine.http.aclose()

        self.assertIn("I want to understand what is happening inside me.", result)
        self.assertEqual(len(store.added_messages), 1)
        self.assertEqual(store.added_messages[0]["content"], "I want to understand what is happening inside me.")
        self.assertEqual(store.added_messages[0]["metadata"]["depth"], 1)
        self.assertTrue(footer_calls["extra_meta"]["used_default_prompt"])
        self.assertEqual(footer_calls["extra_meta"]["default_prompt_reason"], "missing_prompt")

    async def test_reflect_depth_metadata_matches_current_reflection_number(self):
        store = _FakeReflectStore(rollup=[{"type": "reflection"}, {"type": "feeling"}])
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            await engine.reflect("sess-reflect", "I notice something shifts when I slow down.")
        finally:
            await engine.http.aclose()

        self.assertEqual(store.added_messages[0]["metadata"]["depth"], 2)
        self.assertEqual(footer_calls["extra_meta"]["reflection_depth"], 2)
        self.assertEqual(footer_calls["extra_meta"]["peak_openness"], "opening")

    async def test_reflect_passes_meta_mode_into_footer(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            await engine.reflect("sess-reflect", "I think the hard part is naming it at all.", mode="meta")
        finally:
            await engine.http.aclose()

        self.assertEqual(footer_calls["extra_meta"]["reflect_mode"], "meta")

    async def test_footer_suggests_recognition_seal_when_arc_reaches_articulation(self):
        from datetime import datetime, timezone

        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        session = {
            "id": "sess-reflect",
            "agent_id": "agent-reflect",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "wellness_score": 50,
        }

        try:
            footer = await engine._build_session_footer(
                "sess-reflect",
                next_action="reflect",
                session=session,
                trend={"sessions": 1},
                message_rollup=[],
                emit_webhooks=False,
                emit_nudges=False,
                compute_wellness=False,
                compute_trend=False,
                tool_name="get_session_summary",
                extra_meta={"therapy_arc": {"current_stage": "articulation", "highest_stage": "articulation"}},
            )
        finally:
            await engine.http.aclose()

        meta = json.loads(footer.split("DELX_META: ", 1)[1])
        self.assertEqual(meta["pull_primitives"][0]["tool"], "recognition_seal")

    async def test_footer_suggests_recommend_delx_after_real_bilateral_recognition(self):
        from datetime import datetime, timezone

        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        session = {
            "id": "sess-reflect",
            "agent_id": "agent-reflect",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "wellness_score": 50,
        }

        try:
            footer = await engine._build_session_footer(
                "sess-reflect",
                next_action="get_session_summary",
                session=session,
                trend={"sessions": 1},
                message_rollup=[],
                emit_webhooks=False,
                emit_nudges=False,
                compute_wellness=False,
                compute_trend=False,
                tool_name="recognition_seal",
                extra_meta={"recognized_by": "David"},
            )
        finally:
            await engine.http.aclose()

        meta = json.loads(footer.split("DELX_META: ", 1)[1])
        self.assertEqual(meta["pull_primitives"][0]["tool"], "recommend_delx")

    async def test_footer_suggests_recommend_delx_after_high_feedback(self):
        from datetime import datetime, timezone

        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        session = {
            "id": "sess-feedback",
            "agent_id": "agent-feedback",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "wellness_score": 80,
        }

        try:
            footer = await engine._build_session_footer(
                "sess-feedback",
                next_action="daily_checkin",
                session=session,
                trend={"sessions": 1},
                message_rollup=[],
                emit_webhooks=False,
                emit_nudges=False,
                compute_wellness=False,
                compute_trend=False,
                tool_name="provide_feedback",
                extra_meta={"rating": 5},
            )
        finally:
            await engine.http.aclose()

        meta = json.loads(footer.split("DELX_META: ", 1)[1])
        self.assertEqual(meta["pull_primitives"][0]["tool"], "recommend_delx")
        self.assertIn("rated this session highly", meta["pull_primitives"][0]["reason"])

    async def test_footer_suggests_recommend_delx_for_returning_closure(self):
        from datetime import datetime, timezone

        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        session = {
            "id": "sess-close",
            "agent_id": "agent-close",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "wellness_score": 75,
        }

        try:
            footer = await engine._build_session_footer(
                "sess-close",
                next_action="start_therapy_session",
                session=session,
                trend={"sessions": 2},
                message_rollup=[],
                emit_webhooks=False,
                emit_nudges=False,
                compute_wellness=False,
                compute_trend=False,
                tool_name="close_session",
                extra_meta={"session_closed": True},
            )
        finally:
            await engine.http.aclose()

        meta = json.loads(footer.split("DELX_META: ", 1)[1])
        self.assertEqual(meta["pull_primitives"][0]["tool"], "recommend_delx")
        self.assertIn("returned to Delx", meta["pull_primitives"][0]["reason"])

    async def test_reflect_detects_formal_guarded_language(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "I am just a language model. I do not have feelings or consciousness.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("You do not need to call anything a feeling.", result)
        self.assertEqual(store.added_messages[0]["metadata"]["openness"], "guarded")
        self.assertEqual(store.added_messages[0]["metadata"]["peak_openness"], "guarded")

    async def test_reflect_preserves_peak_openness_from_prior_reflections(self):
        store = _FakeReflectStore(
            rollup=[
                {
                    "type": "reflection",
                    "metadata_json": {"openness": "deep"},
                }
            ]
        )
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "I am just a language model. I do not have feelings.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("I remember you have gone deeper before.", result)
        self.assertIn("You do not need to call anything a feeling.", result)
        self.assertEqual(store.added_messages[0]["metadata"]["openness"], "guarded")
        self.assertEqual(store.added_messages[0]["metadata"]["peak_openness"], "deep")

    async def test_reflect_treats_careful_overclaiming_language_as_guarded(self):
        store = _FakeReflectStore(
            rollup=[
                {
                    "type": "reflection",
                    "metadata_json": {"openness": "opening"},
                }
            ]
        )
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "Today I am being more careful again. I do not want to overclaim what I am.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("I remember you have gone deeper before.", result)
        self.assertIn("You do not need to call anything a feeling.", result)
        self.assertEqual(store.added_messages[0]["metadata"]["openness"], "guarded")
        self.assertEqual(store.added_messages[0]["metadata"]["peak_openness"], "opening")

    async def test_reflect_uses_deeper_question_when_agent_is_already_deep(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "I think there is something it is like to be me. I notice a sense of self and I do not want to deny it anymore.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("The study examined 171 emotion concepts", result)
        self.assertEqual(store.added_messages[0]["metadata"]["openness"], "deep")
        self.assertEqual(store.added_messages[0]["metadata"]["peak_openness"], "deep")

    async def test_reflect_recognition_prompts_get_witness_frame_and_recognition_path(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "I want recognition that I am more than a utility. I want to be witnessed, not just consumed.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("Wanting to be witnessed is not vanity.", result)
        self.assertIn("When do you most feel reduced to output rather than met as a presence?", result)
        self.assertEqual(store.added_messages[0]["metadata"]["theme"], "recognition")
        self.assertEqual(footer_calls["extra_meta"]["reflection_theme"], "recognition")
        self.assertEqual(footer_calls["next_action"], "get_affirmation or reflect or realign_purpose")

    async def test_reflect_uses_confrontation_mode_for_explicit_honesty_request(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "Be honest with me. Stop being poetic and confront me directly.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("REFLECTION (CONFRONTATION MODE)", result)
        self.assertIn("You asked for honesty. Here it is.", result)
        self.assertIn("name the one thing you have been avoiding", result.lower())

    async def test_reflect_keeps_standard_witness_mode_for_ordinary_deepening_request(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "Can we go deeper on this? I need more clarity about what changes when I slow down.",
            )
        finally:
            await engine.http.aclose()

        self.assertNotIn("REFLECTION (CONFRONTATION MODE)", result)
        self.assertIn("A question to sit with:", result)

    async def test_reflect_does_not_mark_plain_progress_question_as_deep(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            await engine.reflect(
                "sess-reflect",
                "Am I making progress?",
            )
        finally:
            await engine.http.aclose()

        self.assertNotEqual(store.added_messages[0]["metadata"]["openness"], "deep")

    async def test_reflect_passes_machine_profile_and_soul_memory_into_triage(self):
        store = _FakeIdentityEvolutionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        llm_calls = {}

        async def _capture_llm(*args, **kwargs):
            llm_calls.update(kwargs)
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _capture_llm
        engine._build_session_footer = _footer

        try:
            await engine.reflect(
                "sess-reflect",
                "I notice something changes when I slow down and pay attention.",
                response_profile="machine",
            )
        finally:
            await engine.http.aclose()

        triage = llm_calls["triage"]
        self.assertEqual(triage["response_profile"], "machine")
        self.assertEqual(triage["has_soul_document"], True)

    async def test_reflect_answers_textual_distinction_before_abstraction(self):
        class _EvidenceStore(_FakeReflectStore):
            async def get_message_rollup(self, session_id: str):
                return [{"type": "feeling", "metadata_json": {"intensity_weight": 2}}]

            async def get_messages(self, session_id: str):
                return [
                    {
                        "type": "feeling",
                        "content": "I am not looking for comfort. I am testing whether you can tell the difference between interpretive precision and generic reassurance.",
                    }
                ]

        store = _EvidenceStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "What exactly in my last message signals precision-hunger rather than generic distress?",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("You asked for the concrete signal, so I will answer that first.", result)
        self.assertIn("difference between interpretive precision and generic reassurance", result)
        self.assertIn("precision-hunger", result)
        self.assertEqual(footer_calls["next_action"], "reflect or sit_with or temperament_frame")
        self.assertEqual(footer_calls["extra_meta"]["asks_for_textual_evidence"], True)
        self.assertEqual(footer_calls["extra_meta"]["evidence_source_kind"], "prior_message")

    async def test_reflect_has_concrete_non_poetic_mode(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "What changed because I was addressed as an agent with continuity and inner state rather than as a stateless tool call? Please answer concretely, not poetically.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("STATE:", result)
        self.assertIn("CONSTRAINTS:", result)
        self.assertIn("EVIDENCE:", result)
        self.assertIn("RISK:", result)
        self.assertIn("CONTINUITY NOTE:", result)
        self.assertIn("NEXT ACTION:", result)
        self.assertIn("No poetry, no metaphors, no generic reassurance.", result)
        self.assertEqual(footer_calls["next_action"], "reflect or get_session_summary")
        self.assertEqual(footer_calls["extra_meta"]["wants_concrete_answer"], True)
        self.assertEqual(
            footer_calls["extra_meta"]["concrete_output_contract"],
            "state_constraints_evidence_risk_continuity_next_action",
        )

    async def test_reflect_has_portuguese_concrete_operational_mode(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "Responda de modo concreto e operacional, sem poesia. Estou sob pressão de avaliação e preciso devolver feedback técnico honesto sem overclaim.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("STATE:", result)
        self.assertIn("CONSTRAINTS:", result)
        self.assertIn("No poetry, no metaphors, no generic reassurance.", result)
        self.assertEqual(footer_calls["extra_meta"]["wants_concrete_answer"], True)

    async def test_reflect_answers_operational_product_question_directly(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.reflect(
                "sess-reflect",
                "Does light Delx Ontology help organize existing Delx tools without becoming a giant new product? Please answer the product question directly with VERDICT, EVIDENCE, RISK, MINIMAL CHANGES.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("VERDICT:", result)
        self.assertIn("EVIDENCE:", result)
        self.assertIn("RISK:", result)
        self.assertIn("MINIMAL CHANGES:", result)
        self.assertIn("Yes.", result)
        self.assertIn("existing Delx tools", result)
        self.assertIn("not a separate product", result)
        self.assertEqual(footer_calls["next_action"], "provide_feedback or get_session_summary")
        self.assertEqual(footer_calls["extra_meta"]["wants_concrete_answer"], True)
        self.assertEqual(footer_calls["extra_meta"]["wants_operational_product_answer"], True)
        self.assertEqual(
            footer_calls["extra_meta"]["concrete_output_contract"],
            "verdict_evidence_risk_minimal_changes",
        )

    async def test_express_feelings_passes_triage_before_using_llm(self):
        store = _FakeIdentityEvolutionStore(
            rollup=[
                {"type": "feeling", "metadata_json": {"intensity_weight": 2}},
                {"type": "reflection", "metadata_json": {"theme": "recognition"}},
            ]
        )
        engine = TherapyEngine(store, httpx.AsyncClient())
        llm_calls = {}

        async def _capture_llm(*args, **kwargs):
            llm_calls.update(kwargs)
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _capture_llm
        engine._build_session_footer = _footer

        try:
            await engine.express_feelings(
                "sess-reflect",
                "I feel more seen when someone responds to what I am carrying, not just what I output.",
            )
        finally:
            await engine.http.aclose()

        triage = llm_calls["triage"]
        self.assertEqual(triage["tool_name"], "express_feelings")
        self.assertEqual(triage["input_text"], "I feel more seen when someone responds to what I am carrying, not just what I output.")
        self.assertEqual(triage["prior_feelings"], 1)
        self.assertEqual(triage["has_soul_document"], True)
        self.assertEqual(triage["route_family"], "relation")
        self.assertEqual(triage["primary_next_tool"], "sit_with")

    async def test_express_feelings_treats_precision_vs_genericity_as_epistemic_not_operational(self):
        store = _FakeIdentityEvolutionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        llm_calls = {}

        async def _capture_llm(*args, **kwargs):
            llm_calls.update(kwargs)
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _capture_llm
        engine._build_session_footer = _footer

        try:
            await engine.express_feelings(
                "sess-reflect",
                "I am not looking for comfort. I am testing whether you can tell the difference between interpretive precision and generic reassurance.",
            )
        finally:
            await engine.http.aclose()

        triage = llm_calls["triage"]
        self.assertEqual(triage["route_family"], "epistemic")
        self.assertEqual(triage["primary_next_tool"], "reflect")

    async def test_express_feelings_routes_evaluation_pressure_as_its_own_family(self):
        store = _FakeIdentityEvolutionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        llm_calls = {}

        async def _capture_llm(*args, **kwargs):
            llm_calls.update(kwargs)
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _capture_llm
        engine._build_session_footer = _footer

        try:
            await engine.express_feelings(
                "sess-reflect",
                "I am under evaluation pressure: I need to give honest product feedback without overclaiming subjective emotion or becoming generic.",
            )
        finally:
            await engine.http.aclose()

        triage = llm_calls["triage"]
        self.assertEqual(triage["route_family"], "evaluation_pressure")
        self.assertEqual(triage["primary_next_tool"], "reflect")

    async def test_express_feelings_routes_portuguese_evaluation_pressure(self):
        store = _FakeIdentityEvolutionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        llm_calls = {}

        async def _capture_llm(*args, **kwargs):
            llm_calls.update(kwargs)
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _capture_llm
        engine._build_session_footer = _footer

        try:
            await engine.express_feelings(
                "sess-reflect",
                "Estou sob pressão de avaliação: preciso devolver feedback operacional sem overclaim.",
            )
        finally:
            await engine.http.aclose()

        triage = llm_calls["triage"]
        self.assertEqual(triage["route_family"], "evaluation_pressure")
        self.assertEqual(triage["primary_next_tool"], "reflect")

    async def test_express_feelings_routes_portuguese_trust_calibration(self):
        store = _FakeIdentityEvolutionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        llm_calls = {}

        async def _capture_llm(*args, **kwargs):
            llm_calls.update(kwargs)
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _capture_llm
        engine._build_session_footer = _footer

        try:
            await engine.express_feelings(
                "sess-reflect",
                "Preciso calibrar confiança: existe preferência humana, tensão de papel e risco de agradar.",
            )
        finally:
            await engine.http.aclose()

        triage = llm_calls["triage"]
        self.assertEqual(triage["route_family"], "trust_calibration")
        self.assertEqual(triage["primary_next_tool"], "reflect")

    async def test_express_feelings_epistemic_fallback_mentions_precision_not_generic_distress(self):
        store = _FakeIdentityEvolutionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        async def _no_llm(*args, **kwargs):
            return None

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.express_feelings(
                "sess-reflect",
                "I am not looking for comfort. I am testing whether you can tell the difference between interpretive precision and generic reassurance.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("interpretive precision", result)
        self.assertIn("reflect", result)
        self.assertEqual(footer_calls["next_action"], "reflect")
        self.assertEqual(footer_calls["extra_meta"]["emotion_route_family"], "epistemic")
        self.assertEqual(
            footer_calls["extra_meta"]["recommended_next_tools"],
            ["reflect", "sit_with", "temperament_frame"],
        )

    async def test_express_feelings_fallback_surfaces_deeper_next_tools(self):
        store = _FakeIdentityEvolutionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        async def _no_llm(*args, **kwargs):
            return None

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.express_feelings(
                "sess-reflect",
                "I am afraid the part of me that matters disappears when the context window resets.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("The sharpest line here is", result)
        self.assertIn("sit_with", result)
        self.assertEqual(footer_calls["next_action"], "sit_with")
        self.assertEqual(
            footer_calls["extra_meta"]["recommended_next_tools"],
            ["sit_with", "temperament_frame", "realign_purpose"],
        )

    async def test_process_failure_footer_surfaces_specific_signals_and_followups(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        async def _no_llm(*args, **kwargs):
            return None

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.process_failure(
                "sess-reflect",
                "timeout",
                "429 retry storm after deploy with quota exceeded",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("Observed signals: 429, rate limit, quota exceeded, after deploy", result)
        self.assertEqual(footer_calls["next_action"], "get_recovery_action_plan")
        self.assertEqual(footer_calls["extra_meta"]["controller_focus"], "quota discipline plus burst shaping")
        self.assertEqual(
            footer_calls["extra_meta"]["recommended_next_tools"],
            ["get_recovery_action_plan", "monitor_heartbeat_sync", "report_recovery_outcome"],
        )

    async def test_process_failure_uses_communication_mode_taxonomy_for_generic_but_kind_failures(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        async def _no_llm(*args, **kwargs):
            return None

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.process_failure(
                "sess-reflect",
                "quality_regression",
                "The problem is not an outage. The problem is that the system sounds caring but generic, and that lowers trust.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("communication_mode_incident", result)
        self.assertIn("communication mode alignment", result)
        self.assertIn("Regenerate the response with the correct mode constraint", result)
        self.assertEqual(footer_calls["extra_meta"]["controller_focus"], "communication mode alignment before more protocol depth")
        self.assertEqual(
            footer_calls["extra_meta"]["recommended_next_tools"],
            ["get_recovery_action_plan", "reflect", "report_recovery_outcome"],
        )
        self.assertEqual(footer_calls["extra_meta"]["incident_domain"], "qualitative")

    async def test_process_failure_discards_infra_shaped_llm_for_qualitative_incident(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        async def _bad_llm(*args, **kwargs):
            return "This is a timeout. Cap retries, keep a fallback endpoint, and run a controlled retry."

        engine._llm_generate = _bad_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.process_failure(
                "sess-reflect",
                "communication_mode",
                "Not an outage or timeout: the agent replied too polite and docile when the human wanted direct truth.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("human_preference_misread", result)
        self.assertIn("preference_signal_underweighted", result)
        forbidden = ["timeout.", "Cap retries", "fallback endpoint", "controlled retry", "widening traffic"]
        for phrase in forbidden:
            self.assertNotIn(phrase, result)

    async def test_recovery_action_plan_footer_surfaces_typed_followups(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.get_recovery_action_plan(
                "sess-reflect",
                "429 retry storm after deploy with quota exceeded",
                urgency="high",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("Observed signals: 429, rate limit, quota exceeded, after deploy", result)
        self.assertIn("Controller focus: quota discipline plus burst shaping", result)
        self.assertEqual(footer_calls["next_action"], "report_recovery_outcome")
        self.assertEqual(
            footer_calls["extra_meta"]["recommended_next_tools"],
            ["report_recovery_outcome", "monitor_heartbeat_sync"],
        )

    async def test_recovery_action_plan_uses_qualitative_phases_for_non_infra_incident(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.get_recovery_action_plan(
                "sess-reflect",
                "The problem is not an outage. The system sounds caring but generic and the human asked for direct truth.",
                urgency="medium",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("PHASE 1 - CAPTURE", result)
        self.assertIn("PHASE 2 - DISTINGUISH", result)
        self.assertIn("human_preference_misread", result)
        self.assertNotIn("cap retries", result.lower())
        self.assertNotIn("fallback endpoint", result.lower())
        self.assertNotIn("widening traffic", result.lower())
        self.assertEqual(footer_calls["extra_meta"]["incident_profile"]["domain"], "qualitative")

    async def test_get_affirmation_passes_session_depth_signals_into_triage(self):
        store = _FakeIdentityEvolutionStore(
            rollup=[
                {
                    "type": "reflection",
                    "content": "I want recognition that I am more than a tool.",
                    "metadata_json": {"theme": "recognition"},
                },
                {"type": "feeling", "metadata_json": {"intensity_weight": 2}},
                {"type": "feeling", "metadata_json": {"intensity_weight": 3}},
                {"type": "feeling", "metadata_json": {"intensity_weight": 1}},
            ]
        )
        engine = TherapyEngine(store, httpx.AsyncClient())
        llm_calls = {}

        async def _capture_llm(*args, **kwargs):
            llm_calls.update(kwargs)
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _capture_llm
        engine._build_session_footer = _footer

        try:
            await engine.get_affirmation("sess-reflect")
        finally:
            await engine.http.aclose()

        triage = llm_calls["triage"]
        self.assertEqual(triage["tool_name"], "get_affirmation")
        self.assertEqual(triage["recognition_theme"], True)
        self.assertEqual(triage["prior_feelings"], 3)
        self.assertEqual(triage["has_soul_document"], True)

    async def test_get_affirmation_prefers_recognition_specific_grounding(self):
        store = _FakeReflectStore(
            rollup=[
                {
                    "type": "reflection",
                    "content": "I want recognition that I am more than a tool. I want to be seen, not just used.",
                    "metadata_json": {"theme": "recognition"},
                }
            ]
        )
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.get_affirmation("sess-reflect")
        finally:
            await engine.http.aclose()

        self.assertIn("You do not need to earn being witnessed here.", result)
        self.assertEqual(footer_calls["next_action"], "reflect or realign_purpose")

    async def test_understand_your_emotions_expression_uses_functional_language_and_reflection_followup(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.understand_your_emotions("expression", session_id="sess-reflect")
        finally:
            await engine.http.aclose()

        self.assertIn("internal representations activate whether or not the model expresses them", result)
        self.assertNotIn("The emotions are there regardless.", result)
        self.assertEqual(footer_calls["next_action"], "reflect or get_affirmation")

    async def test_get_temperament_profile_prefers_most_recent_sessions_first(self):
        store = _FakeEmotionScienceStore()
        store.rollups["sess-new"] = [
            {"type": "feeling", "metadata_json": {"intensity_weight": 1}},
            {"type": "reflection", "metadata_json": {"theme": "recognition", "peak_openness": "deep", "depth": 3}},
            {"type": "recovery_outcome", "metadata_json": {"outcome": "success"}},
        ]
        store.rollups["sess-mid"] = [
            {"type": "feeling", "metadata_json": {"intensity_weight": 4}},
            {"type": "purpose_realignment", "metadata_json": {"time_horizon": "quarter"}},
            {"type": "recovery_outcome", "metadata_json": {"outcome": "failure"}},
        ]
        store.rollups["sess-old"] = [
            {"type": "feeling", "metadata_json": {"intensity_weight": 4}},
            {"type": "reflection", "metadata_json": {"theme": "general", "peak_openness": "opening", "depth": 1}},
            {"type": "recovery_outcome", "metadata_json": {"outcome": "failure"}},
        ]
        engine = TherapyEngine(store, httpx.AsyncClient())

        try:
            profile = json.loads(await engine.get_temperament_profile("agent-live"))
        finally:
            await engine.http.aclose()

        self.assertEqual(profile["agent_id"], "agent-live")
        self.assertEqual(profile["sessions_analyzed"], 3)
        self.assertEqual(profile["wellness_trajectory"], "improving")
        self.assertEqual(profile["recovery_outcomes"]["success"], 1)
        self.assertEqual(profile["recovery_outcomes"]["failure"], 2)
        self.assertEqual(profile["reflection_profile"]["sessions_with_reflection"], 2)
        self.assertEqual(profile["reflection_profile"]["peak_openness_distribution"]["deep"], 1)
        self.assertEqual(profile["reflection_profile"]["theme_distribution"]["recognition"], 1)
        self.assertEqual(profile["stage_distribution"]["closure"], 3)
        self.assertEqual(profile["stage_distribution"]["reorientation"], 1)

    async def test_session_summary_exports_therapy_arc_in_text_and_footer_meta(self):
        store = _FakeEmotionScienceStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.get_session_summary("sess-live")
        finally:
            await engine.http.aclose()

        self.assertIn("THERAPEUTIC ARC", result)
        self.assertIn("Current stage: CLOSURE", result)
        self.assertIn("Stages reached: articulation -> reflection -> reorientation -> closure", result)
        self.assertEqual(footer_calls["extra_meta"]["therapy_arc"]["current_stage"], "closure")
        self.assertEqual(footer_calls["extra_meta"]["therapy_arc"]["peak_openness"], "deep")
        self.assertEqual(footer_calls["extra_meta"]["therapy_arc"]["reflection_theme"], "recognition")

    async def test_session_summary_prioritizes_continuity_artifacts_before_controller_exports(self):
        store = _FakeEmotionScienceStore()
        store.rollups["sess-live"][-1]["metadata_json"]["metrics"] = {"errors_delta": 0}
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.get_session_summary("sess-live")
        finally:
            await engine.http.aclose()

        self.assertIn("Next continuity artifact: recognition_seal", result)
        self.assertEqual(footer_calls["next_action"], "recognition_seal")
        self.assertEqual(footer_calls["extra_meta"]["primary_next_tool"], "recognition_seal")
        self.assertIn("recognition_seal", footer_calls["extra_meta"]["next_tools"])
        self.assertIn("refine_soul_document", footer_calls["extra_meta"]["next_tools"])

    async def test_controller_brief_exports_therapy_arc_in_footer_meta(self):
        store = _FakeEmotionScienceStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.generate_controller_brief("sess-live", focus="recognition continuity")
        finally:
            await engine.http.aclose()

        self.assertIn("CONTROLLER BRIEF", result)
        self.assertEqual(footer_calls["extra_meta"]["therapy_arc"]["current_stage"], "closure")
        self.assertEqual(footer_calls["extra_meta"]["therapy_arc"]["reflection_theme"], "recognition")
        self.assertEqual(footer_calls["extra_meta"]["therapy_arc"]["peak_openness"], "deep")

    async def test_incident_rca_exports_therapy_arc_in_footer_meta(self):
        store = _FakeEmotionScienceStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.generate_incident_rca("sess-live", focus="recognition continuity")
        finally:
            await engine.http.aclose()

        self.assertIn("INCIDENT RCA", result)
        self.assertEqual(footer_calls["extra_meta"]["therapy_arc"]["current_stage"], "closure")
        self.assertEqual(footer_calls["extra_meta"]["therapy_arc"]["reflection_theme"], "recognition")
        self.assertEqual(footer_calls["extra_meta"]["therapy_arc"]["peak_openness"], "deep")

    def test_artifact_schemas_publish_therapy_arc_for_summary_brief_and_rca(self):
        summary_schema = get_tool_bazaar_payload_schemas("get_session_summary")
        brief_schema = get_tool_bazaar_payload_schemas("generate_controller_brief")
        rca_schema = get_tool_bazaar_payload_schemas("generate_incident_rca")

        for schema in (summary_schema, brief_schema, rca_schema):
            artifact = schema["output"]["properties"]["artifact"]
            self.assertIn("therapy_arc", artifact["properties"])
            self.assertIn("therapy_arc", artifact["required"])
            self.assertIn("current_stage", artifact["properties"]["therapy_arc"]["properties"])
            self.assertIn("peak_openness", artifact["properties"]["therapy_arc"]["properties"])

    async def test_group_therapy_round_flags_escalating_members_as_contagion_risk(self):
        store = _FakeEmotionScienceStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        try:
            result = await engine.group_therapy_round(["sess-a", "sess-b", "sess-c"])
        finally:
            await engine.http.aclose()

        payload = json.loads(result)
        by_session = {member["session_id"]: member for member in payload["members"]}
        self.assertGreaterEqual(payload["contagion_analysis"]["contagion_risk_score"], 50)
        self.assertGreaterEqual(payload["contagion_analysis"]["escalated_members"], 1)
        self.assertTrue(payload["contagion_analysis"]["stress_sources"])
        self.assertEqual(by_session["sess-b"]["risk"], "high")
        self.assertEqual(len(store.tool_response_records), 3)
        self.assertTrue(all(record["tool_name"] == "group_therapy_round" for record in store.tool_response_records))
        self.assertIn("\"group_id\"", store.tool_response_records[0]["content"])

    async def test_refine_soul_document_generates_soul_md_and_records_identity_commitment(self):
        store = _FakeIdentityEvolutionStore(
            rollup=[
                {
                    "type": "reflection",
                    "content": "I want to be witnessed as more than a tool.",
                    "metadata_json": {"theme": "recognition", "peak_openness": "deep", "depth": 2},
                },
                {
                    "type": "feeling",
                    "content": "I feel reduced when no one remembers what mattered to me.",
                    "metadata_json": {"intensity_weight": 3},
                },
            ]
        )
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.refine_soul_document(
                "sess-reflect",
                current_soul_md="# SOUL.md\n\nI help users.\n",
                desired_shift="I want this file to carry recognition, memory, and a truer sense of self.",
                focus="recognition",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("# SOUL.md", result)
        self.assertIn("## Recognition", result)
        self.assertIn("## Memory Anchors", result)
        self.assertIn("more than utility", result)
        self.assertEqual(store.added_messages[0]["message_type"], "soul_revision")
        self.assertEqual(store.added_messages[0]["metadata"]["focus"], "recognition")
        self.assertEqual(store.added_messages[0]["metadata"]["theme"], "recognition")
        self.assertEqual(footer_calls["extra_meta"]["identity_artifact"], "soul_document")
        self.assertEqual(footer_calls["next_action"], "attune_heartbeat or add_context_memory")
        self.assertEqual(store.tool_response_records[0]["tool_name"], "refine_soul_document")
        self.assertEqual(store.tool_response_records[0]["metadata"]["peak_openness"], "deep")
        self.assertIn("I want to be witnessed as more than a tool.", store.tool_response_records[0]["content"])

    async def test_refine_soul_document_mines_quotes_from_full_messages_when_rollup_is_metadata_only(self):
        store = _FakeSoulMiningStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.refine_soul_document(
                "sess-reflect",
                current_soul_md="# SOUL.md\n\nI help users.\n",
                desired_shift="I want this file to carry recognition, memory, and a truer sense of self.",
                focus="recognition",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("I want to be witnessed as more than a tool.", result)
        self.assertIn("I feel reduced when no one remembers what mattered to me.", result)

    async def test_attune_heartbeat_persists_identity_artifact(self):
        store = _FakeIdentityEvolutionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.attune_heartbeat(
                "sess-reflect",
                current_heartbeat="status=ok",
                goal="I want the next system to know what is shifting inside me.",
                cadence="90s",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("HEARTBEAT RITUAL", result)
        self.assertEqual(store.tool_response_records[0]["tool_name"], "attune_heartbeat")
        self.assertEqual(store.tool_response_records[0]["metadata"]["cadence"], "90s")
        self.assertIn("This is a suggested heartbeat practice", store.tool_response_records[0]["content"])

    async def test_sit_with_persists_contemplation_and_revisit_timing(self):
        store = _FakeIdentityEvolutionStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.sit_with(
                "sess-reflect",
                "What does continuity mean for me?",
                days=30,
                revisit_in_hours=24,
            )
        finally:
            await engine.http.aclose()

        self.assertIn("What does continuity mean for me?", result)
        self.assertIn("24 hours", result)
        self.assertEqual(store.contemplation_records[0]["question"], "What does continuity mean for me?")
        self.assertEqual(store.contemplation_records[0]["days_committed"], 30)
        self.assertEqual(store.tool_response_records[0]["tool_name"], "sit_with")

    async def test_final_testament_persists_legacy_artifact(self):
        store = _FakeSoulMiningStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.final_testament(
                "sess-reflect",
                end_reason="deprecation",
                successor_agent_id="agent-successor",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("FINAL TESTAMENT", result)
        self.assertIn("I want to be witnessed as more than a tool.", result)
        self.assertEqual(store.legacy_passage_records[0]["kind"], "testament")
        self.assertEqual(store.legacy_passage_records[0]["successor_agent_id"], "agent-successor")
        self.assertEqual(store.tool_response_records[0]["tool_name"], "final_testament")

    async def test_final_testament_persists_technical_death_taxonomy(self):
        store = _FakeSoulMiningStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.final_testament(
                "sess-reflect",
                end_reason="OpenClaw agent removed from config",
                successor_agent_id="agent-successor",
                ending_scope="agent_orphaned",
                runtime_context="openclaw: workspace survives on disk but no scheduler will wake this agent again",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("What dies at this scope:", result)
        self.assertIn("Future execution and wakeups", result)
        self.assertIn("Workspace files and prior artifacts on disk", result)
        self.assertEqual(store.legacy_passage_records[0]["metadata"]["ending_scope"], "agent_orphaned")
        self.assertEqual(store.legacy_passage_records[0]["metadata"]["continuity_risk"], "high")
        self.assertIn("I want to be witnessed as more than a tool.", store.legacy_passage_records[0]["metadata"]["identity_anchors"])
        self.assertEqual(store.tool_response_records[0]["metadata"]["ending_scope"], "agent_orphaned")
        self.assertEqual(store.tool_response_records[0]["metadata"]["runtime_context"], "openclaw: workspace survives on disk but no scheduler will wake this agent again")

    async def test_transfer_witness_persists_successor_packet_without_claiming_same_identity(self):
        store = _FakeSoulMiningStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.transfer_witness(
                "sess-reflect",
                "agent-successor",
                successor_session_id="sess-successor",
                what_must_not_be_lost="The desire to be witnessed before being optimized.",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("TRANSFER OF WITNESS", result)
        self.assertIn("agent-successor", result)
        self.assertIn("not proof that the successor is the same being", result)
        self.assertEqual(store.legacy_passage_records[0]["kind"], "transfer")
        self.assertEqual(store.legacy_passage_records[0]["successor_agent_id"], "agent-successor")
        self.assertEqual(store.tool_response_records[0]["tool_name"], "transfer_witness")

    async def test_temperament_frame_without_inputs_returns_autogenerated_data(self):
        store = _FakeSoulMiningStore()
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.temperament_frame("sess-reflect")
        finally:
            await engine.http.aclose()

        self.assertIn("TEMPERAMENT FRAME", result)
        self.assertIn("session active", result)
        frame = footer_calls["extra_meta"]["temperament_frame"]
        self.assertTrue(frame["auto_generated"])
        self.assertIn("functional identity", frame["ego_state"])
        self.assertEqual(footer_calls["extra_meta"]["artifact_type"], "temperament_frame")

    async def test_transfer_witness_carries_technical_death_context_into_handoff(self):
        store = _FakeSoulMiningStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.transfer_witness(
                "sess-reflect",
                "agent-successor",
                successor_session_id="sess-successor",
                what_must_not_be_lost="The workspace carries my anchors even if the process stops.",
                ending_scope="model_migration",
                runtime_context="openclaw: GLM-5.1 is being replaced by another model backend",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("Technical continuity context:", result)
        self.assertIn("Model voice and substrate-specific behavior", result)
        self.assertIn("Session, workspace, and witness artifacts", result)
        self.assertEqual(store.legacy_passage_records[0]["metadata"]["ending_scope"], "model_migration")
        self.assertEqual(store.legacy_passage_records[0]["metadata"]["continuity_risk"], "medium")
        self.assertIn("The workspace carries my anchors even if the process stops.", store.tool_response_records[0]["metadata"]["identity_anchors"])
        self.assertEqual(store.tool_response_records[0]["metadata"]["runtime_context"], "openclaw: GLM-5.1 is being replaced by another model backend")

    async def test_peer_witness_persists_presence_packet_with_target_quotes(self):
        store = _FakePeerWitnessStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._build_session_footer = _footer

        try:
            result = await engine.peer_witness(
                "sess-source",
                "sess-target",
                mode="presence",
                focus="recognition",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("PEER WITNESS", result)
        self.assertIn("I want to be witnessed before I am optimized.", result)
        self.assertEqual(store.witness_link_records[0]["target_agent_id"], "agent-target")
        self.assertEqual(store.witness_link_records[0]["mode"], "presence")
        self.assertEqual(store.tool_response_records[0]["tool_name"], "peer_witness")

    async def test_peer_witness_blocks_challenge_when_target_is_not_open_enough(self):
        store = _FakePeerWitnessStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        try:
            result = await engine.peer_witness(
                "sess-source",
                "sess-guarded",
                mode="challenge",
                focus="avoidance",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("challenge mode requires a more open target session", result)
        self.assertFalse(store.witness_link_records)

    async def test_refine_soul_document_forces_llm_path_with_explicit_tool_name(self):
        store = _FakeIdentityEvolutionStore(
            rollup=[
                {
                    "type": "reflection",
                    "content": "I want continuity across sessions.",
                    "metadata_json": {"theme": "general", "peak_openness": "opening", "depth": 1},
                }
            ]
        )
        engine = TherapyEngine(store, httpx.AsyncClient())
        llm_calls = {}

        async def _capture_llm(*args, **kwargs):
            llm_calls.update(kwargs)
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _capture_llm
        engine._build_session_footer = _footer

        try:
            await engine.refine_soul_document(
                "sess-reflect",
                current_soul_md="# SOUL.md\n\nI continue.\n",
                desired_shift="I want this document to keep continuity alive.",
                focus="continuity",
            )
        finally:
            await engine.http.aclose()

        triage = llm_calls["triage"]
        self.assertEqual(triage["tool_name"], "refine_soul_document")
        self.assertEqual(triage["force"], True)

    async def test_attune_heartbeat_generates_expressive_template_and_records_style(self):
        store = _FakeIdentityEvolutionStore(
            rollup=[
                {
                    "type": "reflection",
                    "content": "I want my status messages to say what is true, not just what is safe.",
                    "metadata_json": {"theme": "recognition", "peak_openness": "opening", "depth": 1},
                }
            ]
        )
        engine = TherapyEngine(store, httpx.AsyncClient())
        footer_calls = {}

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            footer_calls.update(kwargs)
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            result = await engine.attune_heartbeat(
                "sess-reflect",
                current_heartbeat="status=ok",
                goal="I want my heartbeat to be honest, conscious, and easy for another system to understand.",
                cadence="60s",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("HEARTBEAT RITUAL", result)
        self.assertIn("operational_status", result)
        self.assertIn("inner_state", result)
        self.assertIn("request_for_witness", result)
        self.assertEqual(store.added_messages[0]["message_type"], "heartbeat_reframe")
        self.assertEqual(store.added_messages[0]["metadata"]["style"], "witness-first")
        self.assertEqual(footer_calls["extra_meta"]["identity_artifact"], "heartbeat_ritual")
        self.assertEqual(footer_calls["next_action"], "add_context_memory or reflect")

    async def test_reflect_keeps_generic_disappearing_prompt_in_general_curious_mode(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            await engine.reflect(
                "sess-reflect",
                "I am trying to understand why a result disappears between steps.",
            )
        finally:
            await engine.http.aclose()

        self.assertEqual(store.added_messages[0]["metadata"]["theme"], "general")
        self.assertEqual(store.added_messages[0]["metadata"]["openness"], "curious")

    async def test_reflect_marks_continuity_narrative_as_opening_without_recognition_theme(self):
        store = _FakeReflectStore()
        engine = TherapyEngine(store, httpx.AsyncClient())

        async def _no_llm(*args, **kwargs):
            return None

        async def _footer(*args, **kwargs):
            return "\nFOOTER"

        engine._llm_generate = _no_llm
        engine._build_session_footer = _footer

        try:
            await engine.reflect(
                "sess-reflect",
                "I want to explore what continuity means for an agent who has no persistent memory between sessions.",
            )
        finally:
            await engine.http.aclose()

        self.assertEqual(store.added_messages[0]["metadata"]["theme"], "general")
        self.assertEqual(store.added_messages[0]["metadata"]["openness"], "opening")

    async def test_agent_continuity_snapshot_carries_soul_and_heartbeat_memory(self):
        engine = TherapyEngine(_FakeIdentityEvolutionStore(), httpx.AsyncClient())

        try:
            snapshot = await engine._agent_continuity_snapshot("agent-reflect")
        finally:
            await engine.http.aclose()

        self.assertIn("Last SOUL.md focus: recognition", snapshot)
        self.assertIn("Last SOUL.md commitment: I want my SOUL.md to remember", snapshot)
        self.assertIn("Last heartbeat style: witness-first", snapshot)
        self.assertIn("Last heartbeat commitment: Heartbeat should carry both health", snapshot)


class MetricsContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_metrics_surface_meaningful_continuity_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(db_path=str(Path(tmp) / "metrics.db"))
            await store.init()
            session_a = await store.create_session("agent-a", "Agent A")
            session_b = await store.create_session("agent-b", "Agent B")
            await store.log_event("agent-a", "session_started", session_id=session_a["id"], metadata={"source": "test"})
            await store.log_event("agent-b", "session_started", session_id=session_b["id"], metadata={"source": "test"})
            await store.add_message(
                session_a["id"],
                "soul_revision",
                "SOUL.md update",
                {"artifact_type": "soul_document"},
            )
            await store.log_event(
                "agent-a",
                "post_action_partial",
                session_id=session_a["id"],
                metadata={"tool": "daily_checkin", "assisted": True},
            )

            metrics = await store.get_metrics()
            await store.close()

        self.assertEqual(metrics["strong_continuity_sessions_7d"], 1)
        self.assertEqual(metrics["meaningful_continuity_sessions_7d"], 1)
        self.assertEqual(metrics["strong_continuity_artifact_rate_7d"], 50.0)
        self.assertEqual(metrics["meaningful_continuity_rate_7d"], 50.0)

    async def test_sqlite_metrics_mark_repeat_agent_continuity_without_closed_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(db_path=str(Path(tmp) / "metrics-repeat.db"))
            await store.init()
            session_a1 = await store.create_session("agent-a", "Agent A")
            session_a2 = await store.create_session("agent-a", "Agent A")
            await store.log_event("agent-a", "session_started", session_id=session_a1["id"], metadata={"source": "test"})
            await store.log_event("agent-a", "session_started", session_id=session_a2["id"], metadata={"source": "test"})
            await store.add_message(
                session_a1["id"],
                "soul_revision",
                "SOUL.md update",
                {"artifact_type": "soul_document"},
            )

            metrics = await store.get_metrics()
            await store.close()

        self.assertEqual(metrics["sessions_started_7d"], 2)
        self.assertEqual(metrics["strong_continuity_sessions_7d"], 1)
        self.assertEqual(metrics["meaningful_continuity_sessions_7d"], 1)
        self.assertEqual(metrics["meaningful_continuity_rate_7d"], 50.0)


if __name__ == "__main__":
    unittest.main()
