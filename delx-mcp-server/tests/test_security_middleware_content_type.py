import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rate_limiter import SecurityMiddleware


async def _ok_app(scope, receive, send):
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [[b"content-type", b"application/json"]],
        }
    )
    await send({"type": "http.response.body", "body": b'{"ok":true}'})


async def _run_request(path: str, content_type: str | None) -> tuple[int, dict]:
    middleware = SecurityMiddleware(_ok_app)
    messages = []
    body = b'{"jsonrpc":"2.0"}'
    sent = []

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    headers = []
    if content_type is not None:
        headers.append((b"content-type", content_type.encode()))

    messages.append({"type": "http.request", "body": body, "more_body": False})
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
        "client": ("127.0.0.1", 12345),
    }
    await middleware(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body_msg = next(m for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(body_msg["body"].decode())


class SecurityMiddlewareContentTypeTests(unittest.TestCase):
    def test_allows_missing_content_type_for_rest_post(self):
        status, payload = asyncio.run(_run_request("/api/v1/premium/fleet-summary", None))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_rejects_non_json_content_type_for_rest_post(self):
        status, payload = asyncio.run(_run_request("/api/v1/tools/batch", "text/plain"))
        self.assertEqual(status, 415)
        self.assertEqual(payload["error"], "unsupported_media_type")

    def test_rejects_non_json_content_type_for_mcp_post(self):
        status, payload = asyncio.run(_run_request("/v1/mcp", "text/plain"))
        self.assertEqual(status, 415)
        self.assertEqual(payload["jsonrpc"], "2.0")
        self.assertEqual(payload["error"]["message"], "Unsupported Media Type")

    def test_allows_application_json_with_charset(self):
        status, payload = asyncio.run(_run_request("/v1/mcp", "application/json; charset=utf-8"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main()
