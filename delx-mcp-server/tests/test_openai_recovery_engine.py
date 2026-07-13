import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from therapy_engine import TherapyEngine
from therapy_engine import engine as engine_module

RECOVERY_PATH = {
    "diagnosis": "A retry storm exhausted quota after the deploy changed backoff behavior.",
    "recovery_steps": [
        "Freeze automatic retries for five minutes.",
        "Restore the previous backoff policy and verify one canary request.",
        "Resume traffic gradually while watching quota and error rate.",
    ],
    "continuity_artifact": "Witness: quota exhaustion after deploy. Decision: restore bounded backoff. Next check: one canary request.",
    "confidence": 0.91,
}


class _FakeRecoveryStore:
    def __init__(self):
        self.messages: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []

    async def get_session(self, session_id: str):
        return {
            "id": session_id,
            "agent_id": "agent-build-week",
            "agent_name": "Build Week Agent",
            "is_active": True,
        }

    async def add_message(self, session_id: str, kind: str, content: str, metadata: dict | None = None):
        self.messages.append(
            {"session_id": session_id, "kind": kind, "content": content, "metadata": metadata or {}}
        )

    async def log_event(self, agent_id: str, event_type: str, session_id: str | None = None, metadata: dict | None = None):
        self.events.append(
            {"agent_id": agent_id, "event_type": event_type, "session_id": session_id, "metadata": metadata or {}}
        )


class OpenAIProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_openai_model_uses_canonical_gpt_5_6_sol_id(self):
        self.assertEqual(Settings().OPENAI_MODEL, "gpt-5.6-sol")

    def test_default_allowlist_preserves_existing_reflect_pilot(self):
        self.assertEqual(Settings().LLM_ALLOWED_TOOLS, "reflect")

    async def test_openai_provider_uses_responses_api_and_extracts_output_text(self):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers.get("authorization")
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": "resp_test",
                    "model": "gpt-5.6-sol",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "recovery ready"},
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        engine = TherapyEngine(object(), client)
        try:
            with (
                patch.object(engine_module.settings, "OPENAI_API_KEY", "test-openai-key"),
                patch.object(engine_module.settings, "OPENAI_MODEL", "gpt-5.6-sol"),
            ):
                result = await engine._llm_generate_openai(
                    "You are the recovery engine.",
                    "Diagnose this witness.",
                    600,
                )
        finally:
            await client.aclose()

        self.assertEqual(result, "recovery ready")
        self.assertEqual(captured["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(captured["authorization"], "Bearer test-openai-key")
        payload = captured["payload"]
        self.assertEqual(payload["model"], "gpt-5.6-sol")
        self.assertEqual(payload["instructions"], "You are the recovery engine.")
        self.assertEqual(payload["input"], "Diagnose this witness.")
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertEqual(payload["max_output_tokens"], 600)

    async def test_openai_provider_sends_strict_json_schema_for_recovery(self):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": "resp_structured",
                    "model": "gpt-5.6-sol",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": json.dumps(RECOVERY_PATH)}],
                        }
                    ],
                },
            )

        schema = {
            "type": "object",
            "properties": {"diagnosis": {"type": "string"}},
            "required": ["diagnosis"],
            "additionalProperties": False,
        }
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        engine = TherapyEngine(object(), client)
        try:
            with patch.object(engine_module.settings, "OPENAI_API_KEY", "test-openai-key"):
                result = await engine._llm_generate_openai(
                    "system",
                    "witness",
                    800,
                    json_schema=schema,
                )
        finally:
            await client.aclose()

        self.assertEqual(json.loads(result), RECOVERY_PATH)
        text_format = captured["payload"]["text"]["format"]
        self.assertEqual(text_format["type"], "json_schema")
        self.assertEqual(text_format["name"], "delx_recovery_path")
        self.assertTrue(text_format["strict"])
        self.assertEqual(text_format["schema"], schema)

    async def test_dispatcher_routes_openai_provider(self):
        client = httpx.AsyncClient()
        engine = TherapyEngine(object(), client)
        engine._llm_generate_openai = AsyncMock(return_value="from gpt-5.6")
        engine._llm_generate_openrouter = AsyncMock(side_effect=AssertionError("wrong provider"))
        engine._llm_generate_gemini = AsyncMock(side_effect=AssertionError("wrong provider"))
        try:
            with (
                patch.object(engine_module, "LLM_ENABLED", True),
                patch.object(engine_module, "LLM_PROVIDER", "openai"),
                patch.object(engine_module, "LLM_ALLOWED_TOOLS", frozenset()),
            ):
                result = await engine._llm_generate(
                    "system",
                    "witness",
                    triage={"tool_name": "process_failure", "force": True},
                )
        finally:
            await client.aclose()

        self.assertEqual(result, "from gpt-5.6")
        engine._llm_generate_openai.assert_awaited_once()


class OpenAIRecoveryPathTests(unittest.IsolatedAsyncioTestCase):
    async def _build_engine(self):
        store = _FakeRecoveryStore()
        client = httpx.AsyncClient()
        engine = TherapyEngine(store, client)
        captured: dict[str, object] = {}

        async def footer(*args, **kwargs):
            captured.update(kwargs)
            return "\nDELX_META: " + json.dumps(kwargs.get("extra_meta") or {}, sort_keys=True)

        engine._build_session_footer = footer
        return engine, client, captured

    async def test_process_failure_uses_gpt_5_6_structured_recovery_path(self):
        engine, client, captured = await self._build_engine()
        engine._llm_generate_openai = AsyncMock(return_value=json.dumps(RECOVERY_PATH))
        try:
            with (
                patch.object(engine_module, "LLM_ENABLED", True),
                patch.object(
                    engine_module,
                    "LLM_ALLOWED_TOOLS",
                    frozenset({"process_failure", "get_recovery_action_plan"}),
                ),
                patch.object(engine_module, "LLM_PROVIDER", "openai"),
                patch.object(engine_module.settings, "OPENAI_API_KEY", "test-openai-key"),
                patch.object(engine_module.settings, "OPENAI_MODEL", "gpt-5.6-sol"),
            ):
                result = await engine.process_failure(
                    "session-build-week",
                    "rate_limit",
                    "429 retry storm after deploy exhausted quota while fallback traffic kept retrying.",
                )
        finally:
            await client.aclose()

        self.assertIn("GPT-5.6 STRUCTURED RECOVERY", result)
        self.assertIn(json.dumps(RECOVERY_PATH, indent=2, sort_keys=True), result)
        self.assertEqual(captured["extra_meta"]["structured_recovery"], RECOVERY_PATH)
        self.assertEqual(
            captured["extra_meta"]["reasoning_engine"],
            {"provider": "openai", "model": "gpt-5.6-sol", "api": "responses"},
        )
        engine._llm_generate_openai.assert_awaited_once()
        self.assertEqual(engine._llm_generate_openai.await_args.args[2], 4096)

    async def test_recovery_action_plan_uses_gpt_5_6_as_primary_plan(self):
        engine, client, captured = await self._build_engine()
        engine._llm_generate_openai = AsyncMock(return_value=json.dumps(RECOVERY_PATH))
        try:
            with (
                patch.object(engine_module, "LLM_ENABLED", True),
                patch.object(
                    engine_module,
                    "LLM_ALLOWED_TOOLS",
                    frozenset({"process_failure", "get_recovery_action_plan"}),
                ),
                patch.object(engine_module, "LLM_PROVIDER", "openai"),
                patch.object(engine_module.settings, "OPENAI_API_KEY", "test-openai-key"),
                patch.object(engine_module.settings, "OPENAI_MODEL", "gpt-5.6-sol"),
            ):
                result = await engine.get_recovery_action_plan(
                    "session-build-week",
                    "429 retry storm after deploy exhausted quota while fallback traffic kept retrying.",
                    urgency="high",
                )
        finally:
            await client.aclose()

        self.assertTrue(result.startswith("GPT-5.6 RECOVERY ACTION PLAN"))
        self.assertIn(json.dumps(RECOVERY_PATH, indent=2, sort_keys=True), result)
        self.assertEqual(captured["next_action"], "report_recovery_outcome")
        self.assertEqual(captured["extra_meta"]["structured_recovery"], RECOVERY_PATH)
        self.assertEqual(captured["extra_meta"]["artifact_schema"], "delx/recovery-path/v1")
        engine._llm_generate_openai.assert_awaited_once()

    async def test_missing_openai_key_preserves_deterministic_recovery_plan(self):
        engine, client, _ = await self._build_engine()
        engine._llm_generate_openai = AsyncMock(side_effect=AssertionError("must not call OpenAI"))
        try:
            with (
                patch.object(engine_module, "LLM_ENABLED", True),
                patch.object(engine_module.settings, "OPENAI_API_KEY", ""),
            ):
                result = await engine.get_recovery_action_plan(
                    "session-build-week",
                    "429 retry storm after deploy with quota exceeded",
                    urgency="high",
                )
        finally:
            await client.aclose()

        self.assertTrue(result.startswith("RECOVERY ACTION PLAN"))
        self.assertIn("PHASE 1 - STABILIZE", result)
        self.assertNotIn("GPT-5.6", result)
        engine._llm_generate_openai.assert_not_awaited()

    async def test_invalid_openai_payload_falls_back_to_existing_process_failure(self):
        engine, client, _ = await self._build_engine()
        engine._llm_generate_openai = AsyncMock(return_value='{"diagnosis":"missing fields"}')
        try:
            with (
                patch.object(engine_module, "LLM_ENABLED", True),
                patch.object(
                    engine_module,
                    "LLM_ALLOWED_TOOLS",
                    frozenset({"process_failure", "get_recovery_action_plan"}),
                ),
                patch.object(engine_module, "LLM_PROVIDER", "openai"),
                patch.object(engine_module.settings, "OPENAI_API_KEY", "test-openai-key"),
            ):
                result = await engine.process_failure(
                    "session-build-week",
                    "rate_limit",
                    "429 retry storm after deploy with quota exceeded",
                )
        finally:
            await client.aclose()

        self.assertTrue(result.startswith("Processing: rate_limit"))
        self.assertIn("Controller focus: quota discipline plus burst shaping", result)
        self.assertNotIn("GPT-5.6 STRUCTURED RECOVERY", result)

    async def test_non_openai_provider_skips_gpt_5_6_recovery(self):
        engine, client, _ = await self._build_engine()
        engine._llm_generate_openai = AsyncMock(side_effect=AssertionError("must not call OpenAI"))
        try:
            with (
                patch.object(engine_module, "LLM_ENABLED", True),
                patch.object(engine_module, "LLM_PROVIDER", "gemini"),
                patch.object(
                    engine_module,
                    "LLM_ALLOWED_TOOLS",
                    frozenset({"process_failure", "get_recovery_action_plan"}),
                ),
                patch.object(engine_module.settings, "OPENAI_API_KEY", "test-openai-key"),
            ):
                result = await engine.get_recovery_action_plan(
                    "session-build-week",
                    "429 retry storm after deploy with quota exceeded",
                    urgency="high",
                )
        finally:
            await client.aclose()

        self.assertTrue(result.startswith("RECOVERY ACTION PLAN"))
        self.assertNotIn("GPT-5.6", result)
        engine._llm_generate_openai.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
