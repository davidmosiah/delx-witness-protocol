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


class OpenAIProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_openai_model_uses_canonical_gpt_5_6_sol_id(self):
        self.assertEqual(Settings().OPENAI_MODEL, "gpt-5.6-sol")

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


if __name__ == "__main__":
    unittest.main()
