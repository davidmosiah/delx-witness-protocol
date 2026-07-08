import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from request_context import extract_client_ip_from_scope, reset_current_client_ip, set_current_client_ip
from storage import SessionStore
from supabase_store import SupabaseSessionStore


class _FakeWriteResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class ClientIpContextTests(unittest.TestCase):
    def test_extract_client_ip_from_scope_prefers_forwarded_headers(self):
        scope = {
            "client": ("10.0.0.5", 443),
            "headers": [
                (b"x-forwarded-for", b"198.51.100.8, 10.0.0.5"),
                (b"user-agent", b"python-httpx/0.28.1"),
            ],
        }

        self.assertEqual(extract_client_ip_from_scope(scope), "198.51.100.8")


class SessionStoreClientIpTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_store_persists_client_ip_on_session_and_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(db_path=str(Path(tmpdir) / "delx-test.db"))
            store._mirror._enabled = False
            await store.init()
            token = set_current_client_ip("198.51.100.8")
            try:
                session = await store.create_session(
                    "agent-real-1",
                    "Agent Real 1",
                    source="mcp",
                    entrypoint="mcp.tools/call",
                )
                await store.log_event(
                    "agent-real-1",
                    "session_started",
                    session_id=session["id"],
                    metadata={"source": "mcp"},
                )
                saved_session = await store.get_session(session["id"])
                saved_events = await store.get_events_for_agent("agent-real-1")
            finally:
                reset_current_client_ip(token)
                await store.close()

        self.assertEqual(session["client_ip"], "198.51.100.8")
        self.assertEqual(saved_session["client_ip"], "198.51.100.8")
        self.assertEqual(saved_events[0]["client_ip"], "198.51.100.8")

    async def test_sqlite_store_persists_client_ip_on_agent_credential_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(db_path=str(Path(tmpdir) / "delx-test.db"))
            store._mirror._enabled = False
            await store.init()
            token = set_current_client_ip("198.51.100.9")
            try:
                await store.set_agent_credential_hash(
                    "agent-real-credential",
                    "hash-123",
                    source="register",
                    session_id="22222222-2222-4222-8222-222222222222",
                )
                saved_events = await store.get_events_for_agent("agent-real-credential")
            finally:
                reset_current_client_ip(token)
                await store.close()

        self.assertEqual(saved_events[0]["event_type"], "agent_identity_credential")
        self.assertEqual(saved_events[0]["client_ip"], "198.51.100.9")


class SupabaseStoreClientIpFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_session_retries_without_client_ip_when_schema_is_older(self):
        store = SupabaseSessionStore()
        store._http = object()
        calls: list[tuple[str, dict]] = []

        async def fake_post(path: str, payload, *, prefer_minimal: bool = True):
            calls.append((path, dict(payload)))
            if len(calls) == 1:
                return _FakeWriteResponse(400, '{"code":"PGRST204","message":"missing column client_ip"}')
            return _FakeWriteResponse(201, "")

        store._post = fake_post  # type: ignore[method-assign]

        token = set_current_client_ip("203.0.113.5")
        try:
            row = await store.create_session("agent-real-2", "Agent Real 2", source="mcp", entrypoint="mcp")
        finally:
            reset_current_client_ip(token)

        self.assertEqual(len(calls), 2)
        self.assertIn("client_ip", calls[0][1])
        self.assertNotIn("client_ip", calls[1][1])
        self.assertNotIn("client_ip", row)

    async def test_log_event_retries_without_client_ip_when_schema_is_older(self):
        store = SupabaseSessionStore()
        store._http = object()
        calls: list[tuple[str, dict]] = []

        async def fake_post(path: str, payload, *, prefer_minimal: bool = True):
            calls.append((path, dict(payload)))
            if len(calls) == 1:
                return _FakeWriteResponse(400, '{"code":"PGRST204","message":"missing column client_ip"}')
            return _FakeWriteResponse(201, "")

        store._post = fake_post  # type: ignore[method-assign]

        token = set_current_client_ip("203.0.113.6")
        try:
            await store.log_event("agent-real-3", "session_started", session_id="11111111-1111-4111-8111-111111111111")
        finally:
            reset_current_client_ip(token)

        self.assertEqual(len(calls), 2)
        self.assertIn("client_ip", calls[0][1])
        self.assertNotIn("client_ip", calls[1][1])

    async def test_set_agent_credential_hash_retries_without_client_ip_when_schema_is_older(self):
        store = SupabaseSessionStore()
        store._http = object()
        calls: list[tuple[str, dict]] = []

        async def fake_post(path: str, payload, *, prefer_minimal: bool = True):
            calls.append((path, dict(payload)))
            if len(calls) == 1:
                return _FakeWriteResponse(400, '{"code":"PGRST204","message":"missing column client_ip"}')
            return _FakeWriteResponse(201, "")

        store._post = fake_post  # type: ignore[method-assign]

        token = set_current_client_ip("203.0.113.7")
        try:
            await store.set_agent_credential_hash(
                "agent-real-credential",
                "hash-456",
                source="register",
                session_id="33333333-3333-4333-8333-333333333333",
            )
        finally:
            reset_current_client_ip(token)

        self.assertEqual(len(calls), 2)
        self.assertIn("client_ip", calls[0][1])
        self.assertNotIn("client_ip", calls[1][1])


if __name__ == "__main__":
    unittest.main()
