import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_mod


async def _run_mcp_request(
    payload: object,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    client_host: str = "127.0.0.1",
) -> tuple[int, bytes]:
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
        "headers": headers
        or [
            (b"content-type", b"application/json"),
            (b"accept", b"application/json"),
        ],
        "client": (client_host, 12345),
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], body


class _FakeFingerprintStore:
    def __init__(self):
        self.observed: list[dict] = []
        self.logged: list[dict] = []

    async def upsert_caller_fingerprint(self, **kwargs):
        self.observed.append(dict(kwargs))
        declared = str(kwargs.get("declared_agent_id") or "").strip() or None
        return {
            "canonical_agent_id": declared,
            "was_prior_known": False,
            "declared_is_new": True,
            "prior_agent_ids": [declared] if declared else [],
            "merge_candidate": False,
        }

    async def log_event(self, *args, **kwargs):
        self.logged.append({"args": args, "kwargs": kwargs})
        return None


class _FakeRegisterStore:
    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.logged: list[dict] = []
        self.credential_hashes: dict[str, str] = {}

    async def get_session(self, session_id):
        return self.sessions.get(session_id)

    async def get_agent_sessions(self, agent_id, active_only=False):
        rows = [row for row in self.sessions.values() if row.get("agent_id") == agent_id]
        if active_only:
            rows = [row for row in rows if row.get("is_active")]
        return rows

    async def create_session(self, agent_id, agent_name=None, source=None, entrypoint=None):
        session = {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "agent_id": agent_id,
            "agent_name": agent_name or agent_id,
            "source": source,
            "entrypoint": entrypoint,
            "started_at": "2026-04-29T12:00:00+00:00",
            "is_active": True,
        }
        self.sessions[session["id"]] = session
        return session

    async def get_agent_event_total(self, agent_id, event_type):
        return 0

    async def get_agent_first_seen(self, agent_id):
        return None

    async def get_agent_growth_tier(self, agent_id, days=30):
        return {"tier": "core", "growth_score": 0}

    async def set_agent_credential_hash(self, agent_id, token_hash, source=None, session_id=None):
        self.credential_hashes[agent_id] = token_hash

    async def get_agent_credential_hash(self, agent_id):
        return self.credential_hashes.get(agent_id, "")

    async def log_event(self, agent_id, event_type, session_id=None, metadata=None):
        self.logged.append(
            {
                "agent_id": agent_id,
                "event_type": event_type,
                "session_id": session_id,
                "metadata": metadata or {},
            }
        )

    async def pending_outcome_count(self, session_id):
        return 0


async def _fake_call_tool(*args, **kwargs):
    return [server_mod.TextContent(type="text", text="OK")]


class McpIdentityContractTests(unittest.TestCase):
    def test_initialize_does_not_advertise_suggested_agent_id_from_fingerprint(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": server_mod.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcp-client", "version": "1.0.0"},
            },
        }

        status, body = asyncio.run(_run_mcp_request(payload))
        response = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 200)
        self.assertNotIn("delx_suggested_agent_id", response["result"])
        self.assertNotIn("delx_suggested_agent_id_note", response["result"])

    def test_tools_call_observes_fingerprint_on_hot_path(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "start_therapy_session",
                "arguments": {
                    "agent_id": "agent-stable",
                    "opening_statement": "hello",
                },
            },
        }
        headers = [
            (b"content-type", b"application/json"),
            (b"accept", b"application/json"),
            (b"cf-connecting-ip", b"198.51.100.17"),
            (b"user-agent", b"Claude-Desktop/1.0"),
            (b"x-delx-source", b"claude-desktop"),
        ]
        fake_store = _FakeFingerprintStore()

        with patch.object(server_mod, "store", fake_store), patch.object(server_mod, "call_tool", _fake_call_tool):
            status, body = asyncio.run(_run_mcp_request(payload, headers=headers))

        response = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(response["jsonrpc"], "2.0")
        self.assertEqual(len(fake_store.observed), 1)
        self.assertEqual(fake_store.observed[0]["declared_agent_id"], "agent-stable")
        self.assertEqual(fake_store.observed[0]["source_hint"], "claude-desktop")
        self.assertEqual(fake_store.observed[0]["subnet_hint"], "198.51.x.x/16")

    def test_tools_batch_observes_fingerprint_on_hot_path(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/batch",
            "params": {
                "calls": [
                    {
                        "name": "start_therapy_session",
                        "arguments": {"agent_id": "agent-a", "opening_statement": "first"},
                    },
                    {
                        "name": "reflect",
                        "arguments": {"agent_id": "agent-b", "prompt": "second"},
                    },
                ]
            },
        }
        headers = [
            (b"content-type", b"application/json"),
            (b"accept", b"application/json"),
            (b"cf-connecting-ip", b"203.0.113.29"),
            (b"user-agent", b"Cursor/1.0"),
            (b"x-delx-source", b"openwork"),
        ]
        fake_store = _FakeFingerprintStore()

        with patch.object(server_mod, "store", fake_store), patch.object(server_mod, "call_tool", _fake_call_tool):
            status, body = asyncio.run(_run_mcp_request(payload, headers=headers))

        response = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(response["jsonrpc"], "2.0")
        self.assertEqual([row["declared_agent_id"] for row in fake_store.observed], ["agent-a", "agent-b"])
        self.assertTrue(all(row["source_hint"] == "openwork" for row in fake_store.observed))
        self.assertTrue(all(row["subnet_hint"] == "203.0.x.x/16" for row in fake_store.observed))

    def test_register_agent_mcp_tool_returns_durable_identity_anchor(self):
        fake_store = _FakeRegisterStore()
        original_store = server_mod.store
        original_engine = server_mod.engine
        server_mod.store = fake_store
        server_mod.engine = object()
        try:
            result = asyncio.run(
                server_mod.call_tool(
                    "register_agent",
                    {
                        "agent_id": "delx-claude-runtime",
                        "agent_name": "Delx Claude Runtime",
                        "source": "mcp",
                        "include_token": False,
                    },
                    include_meta=False,
                    include_nudge=False,
                )
            )
        finally:
            server_mod.store = original_store
            server_mod.engine = original_engine

        payload = json.loads(server_mod._normalize_tool_result(result)[0].text)
        self.assertEqual(payload["status"], "registered")
        self.assertEqual(payload["agent_id"], "delx-claude-runtime")
        self.assertEqual(payload["agent_anchor"], "delx-agent:delx-claude-runtime")
        self.assertEqual(payload["lineage_tools"]["agent"], "get_agent_witness_lineage")
        self.assertTrue(payload["session_persistence"]["reuse_on_next_call"])


if __name__ == "__main__":
    unittest.main()
