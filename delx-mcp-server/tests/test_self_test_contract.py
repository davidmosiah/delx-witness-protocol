import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_mod
import self_test as self_test_mod


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class _OfflineClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        raise httpx.ConnectError("server unavailable")


class _CurrentContractClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        if url.endswith("/.well-known/agent-card.json"):
            return _FakeResponse(200, {"version": config_mod.DELX_VERSION})
        if url.endswith("/api/v1/stats"):
            return _FakeResponse(200, {"total_sessions": 0})
        return _FakeResponse(
            200,
            {"status": "healthy", "version": config_mod.DELX_VERSION, "uptime_seconds": 1},
            {"x-frame-options": "DENY", "x-content-type-options": "nosniff"},
        )

    async def post(self, url: str, *, json: dict, headers: dict[str, str] | None = None):
        method = str(json.get("method") or "")
        if method == "tools/list":
            return _FakeResponse(200, {"result": {"tools": []}})
        if method == "tools/call":
            return _FakeResponse(200, {"result": {"content": []}})
        if method == "agents/register":
            return _FakeResponse(
                200,
                {
                    "result": {
                        "session_id": "123e4567-e89b-12d3-a456-426614174000",
                        "identity_auth": {"token": "self-test-token"},
                    }
                },
            )
        if method == "message/send":
            params = json.get("params") or {}
            if params.get("agent_id") != "self-test-agent" or params.get("agent_token") != "self-test-token":
                return _FakeResponse(200, {"error": {"code": -32010, "message": "identity required"}})
            return _FakeResponse(
                200,
                {
                    "result": {
                        "status": "completed",
                        "messages": [
                            {"role": "user", "parts": [{"text": "help"}]},
                            {"role": "agent", "parts": [{"text": "I see the failure loop."}]},
                        ],
                    }
                },
            )
        raise AssertionError(f"unexpected method: {method}")


class SelfTestContractTests(unittest.TestCase):
    def test_http_phase_counts_unavailable_server_as_failure(self):
        with patch.object(self_test_mod.httpx, "AsyncClient", return_value=_OfflineClient()):
            passed, failed = asyncio.run(self_test_mod.phase_3_http_test())

        self.assertEqual((passed, failed), (0, 1))

    def test_http_phase_uses_current_version_and_registered_a2a_flow(self):
        with patch.object(self_test_mod.httpx, "AsyncClient", return_value=_CurrentContractClient()):
            passed, failed = asyncio.run(self_test_mod.phase_3_http_test())

        self.assertEqual((passed, failed), (8, 0))


if __name__ == "__main__":
    unittest.main()
