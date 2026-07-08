import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_mod


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


class McpInitializeContractTests(unittest.TestCase):
    def test_initialize_returns_valid_mcp_result(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "smithery-probe", "version": "1.0.0"},
            },
        }

        status, body = asyncio.run(_run_mcp_request(payload))
        response = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 200)
        self.assertEqual(response["jsonrpc"], "2.0")
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertEqual(result["serverInfo"]["name"], "Delx Witness Protocol")
        self.assertEqual(result["serverInfo"]["version"], server_mod.DELX_VERSION)
        self.assertIn("tools", result["capabilities"])

    def test_initialize_negotiates_latest_version_for_unknown_protocol(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {
                "protocolVersion": "2099-01-01",
                "capabilities": {},
                "clientInfo": {"name": "smithery-probe", "version": "1.0.0"},
            },
        }

        status, body = asyncio.run(_run_mcp_request(payload))
        response = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["protocolVersion"], "2025-11-25")

    def test_initialized_notification_is_accepted(self):
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }

        status, body = asyncio.run(_run_mcp_request(payload))

        self.assertEqual(status, 202)
        self.assertEqual(body, b"")

    def test_ping_returns_empty_result(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "ping",
            "params": {},
        }

        status, body = asyncio.run(_run_mcp_request(payload))
        response = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 200)
        self.assertEqual(response["result"], {})


if __name__ == "__main__":
    unittest.main()
