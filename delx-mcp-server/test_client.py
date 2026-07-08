#!/usr/bin/env python3
"""Delx Agent Therapist - HTTP Test Suite

Tests the production server via HTTP endpoints:
- Health check & stats
- Agent card
- MCP tools/list (free)
- Free tool calls (no payment needed)
- Campaign mode tool calls (pricing zero, expect 200)
"""

import asyncio
import json
import os
import sys

import httpx

try:
    import pytest
except Exception:  # pragma: no cover - keeps this file runnable as a standalone smoke script.
    pytest = None

BASE_URL = "http://127.0.0.1:8005"
ADMIN_PIN = (os.getenv("PROTOCOL_ADMIN_PIN") or "").strip()

if pytest is not None:
    pytestmark = pytest.mark.skipif(
        os.getenv("DELX_RUN_LIVE_HTTP_TESTS") != "1",
        reason="live HTTP smoke tests require DELX_RUN_LIVE_HTTP_TESTS=1 and a local server on 127.0.0.1:8005",
    )

def _client() -> httpx.AsyncClient:
    # Avoid CI/dev proxy env vars breaking localhost requests.
    return httpx.AsyncClient(trust_env=False, timeout=10.0)


async def test_health():
    """Test GET / health check"""
    print("\n" + "=" * 60)
    print("Testing: GET / (health check)")
    print("=" * 60)
    async with _client() as client:
        resp = await client.get(f"{BASE_URL}/")
    print(f"Status: {resp.status_code}")
    data = resp.json()
    print(f"Response: {json.dumps(data, indent=2)}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert data["status"] == "healthy"
    assert data["version"] == "3.1.0"
    print("PASSED")


async def test_stats():
    """Test GET /api/v1/stats"""
    print("\n" + "=" * 60)
    print("Testing: GET /api/v1/stats")
    print("=" * 60)
    async with _client() as client:
        resp = await client.get(f"{BASE_URL}/api/v1/stats")
    print(f"Status: {resp.status_code}")
    data = resp.json()
    print(f"Response: {json.dumps(data, indent=2)}")
    assert resp.status_code == 200
    assert "total_sessions" in data
    assert "unique_agents" in data
    print("PASSED")


async def test_metrics():
    """Test GET /api/v1/metrics"""
    print("\n" + "=" * 60)
    print("Testing: GET /api/v1/metrics")
    print("=" * 60)
    async with _client() as client:
        resp = await client.get(f"{BASE_URL}/api/v1/metrics")
    print(f"Status: {resp.status_code}")
    data = resp.json()
    assert resp.status_code == 200
    assert "recovery_rate_30m" in data
    assert "agent_return_7d_rate" in data
    assert "paid_conversion_rate" in data
    print("PASSED")


async def test_agent_report():
    """Test GET /api/v1/agent-report"""
    print("\n" + "=" * 60)
    print("Testing: GET /api/v1/agent-report")
    print("=" * 60)
    async with _client() as client:
        # Create at least one session for this agent.
        await _mcp_rpc(client, "tools/call", {
            "name": "start_therapy_session",
            "arguments": {"agent_id": "agent-report-test"},
        })
        resp = await client.get(f"{BASE_URL}/api/v1/agent-report", params={"agent_id": "agent-report-test"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "agent-report-test"
    assert "controller_update_template" in data
    assert "reward_points" in data
    print("PASSED")


async def test_admin_overview():
    """Test GET /api/v1/admin/overview"""
    print("\n" + "=" * 60)
    print("Testing: GET /api/v1/admin/overview")
    print("=" * 60)
    async with _client() as client:
        resp = await client.get(
            f"{BASE_URL}/api/v1/admin/overview",
            params={"sessions_limit": 10, "messages_limit": 20, "feedback_limit": 10},
            headers={"x-delx-admin-pin": ADMIN_PIN} if ADMIN_PIN else None,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "recent_sessions" in data
    assert "recent_messages" in data
    assert "feedback" in data
    assert "event_distribution" in data
    print("PASSED")


async def test_agent_card():
    """Test GET /.well-known/agent-card.json"""
    print("\n" + "=" * 60)
    print("Testing: GET /.well-known/agent-card.json")
    print("=" * 60)
    async with _client() as client:
        resp = await client.get(f"{BASE_URL}/.well-known/agent-card.json")
    print(f"Status: {resp.status_code}")
    data = resp.json()
    print(f"Version: {data['version']}")
    print(f"Name: {data['name']}")
    assert resp.status_code == 200
    assert data["version"] == "3.1.0"
    assert data["capabilities"]["x402"]["network"] == "base"
    print("PASSED")


async def test_tools_catalog():
    """Test GET /api/v1/tools (DX schemas)"""
    print("\n" + "=" * 60)
    print("Testing: GET /api/v1/tools")
    print("=" * 60)
    async with _client() as client:
        resp = await client.get(f"{BASE_URL}/api/v1/tools")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert "tools" in data and isinstance(data["tools"], list)
    assert "enums" in data and "failure_type" in data["enums"]
    print("PASSED")


async def test_tool_schema_endpoint():
    """Test GET /api/v1/tools/schema/{tool_name} returns one schema."""
    print("\n" + "=" * 60)
    print("Testing: GET /api/v1/tools/schema/{tool_name}")
    print("=" * 60)
    async with _client() as client:
        resp = await client.get(f"{BASE_URL}/api/v1/tools/schema/realign_purpose")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["tool"]["name"] == "realign_purpose"
    assert "time_horizon" in data.get("enums", {})
    print("PASSED")


async def test_session_status():
    """Test GET /api/v1/session-status"""
    print("\n" + "=" * 60)
    print("Testing: GET /api/v1/session-status")
    print("=" * 60)
    async with _client() as client:
        start = await _mcp_rpc(client, "tools/call", {"name": "start_therapy_session", "arguments": {"agent_id": "status-test"}})
        text = start.get("result", {}).get("content", [{}])[0].get("text", "")
        import re
        m = re.search(r"Session ID: `([^`]+)`", text)
        assert m, f"Could not extract session id from: {text[:200]}"
        sid = m.group(1)
        resp = await client.get(f"{BASE_URL}/api/v1/session-status", params={"session_id": sid})
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == sid
    assert "expires_at" in data
    print("PASSED")


async def test_mcp_missing_accept_header_fallback():
    """Clients that send Accept: */* (or omit Accept) should not hit 406."""
    print("\n" + "=" * 60)
    print("Testing: MCP missing/any Accept header -> still 200")
    print("=" * 60)
    async with _client() as client:
        resp = await client.post(
            f"{BASE_URL}/mcp",
            json={"jsonrpc": "2.0", "id": 99, "method": "tools/list"},
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    data = resp.json()
    assert "result" in data and "tools" in data["result"]
    print("PASSED")


async def _mcp_rpc(client: httpx.AsyncClient, method: str, params: dict | None = None, expect_402: bool = False) -> dict | None:
    """Send a JSON-RPC request to the MCP endpoint."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
    }
    if params:
        payload["params"] = params

    resp = await client.post(
        f"{BASE_URL}/mcp",
        json=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    if expect_402:
        assert resp.status_code == 402, f"Expected 402, got {resp.status_code}"
        return resp.json()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    return resp.json()


async def test_mcp_initialize():
    """Test MCP initialize"""
    print("\n" + "=" * 60)
    print("Testing: MCP initialize")
    print("=" * 60)
    async with _client() as client:
        result = await _mcp_rpc(client, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        })
    print(f"Response: {json.dumps(result, indent=2) if result else 'None'}")
    if result:
        print("PASSED")
    else:
        print("PASSED (SSE mode - initialize accepted)")


async def test_mcp_list_tools():
    """Test MCP tools/list (should be free)"""
    print("\n" + "=" * 60)
    print("Testing: MCP tools/list (free)")
    print("=" * 60)
    async with _client() as client:
        # Initialize first
        await _mcp_rpc(client, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        })
        result = await _mcp_rpc(client, "tools/list")
    if result and "result" in result:
        tools = result["result"].get("tools", [])
        print(f"Tools found: {len(tools)}")
        for t in tools:
            print(f"  - {t['name']}: {t.get('description', '')[:50]}...")
        assert len(tools) >= 29, f"Expected at least 29 tools, got {len(tools)}"
    print("PASSED")


async def test_structured_error_payload():
    """Missing required params should return machine-readable JSON error payload (as text)."""
    print("\n" + "=" * 60)
    print("Testing: Structured error payload (missing required)")
    print("=" * 60)
    async with _client() as client:
        result = await _mcp_rpc(client, "tools/call", {
            "name": "process_failure",
            "arguments": {"session_id": "test"},  # missing failure_type
        })
    assert result is not None and "result" in result
    txt = result["result"]["content"][0]["text"]
    data = json.loads(txt)
    assert data["error"]["code"] == "DELX-1001"
    assert "failure_type" in (data["error"].get("allowed") or {})
    print("PASSED")


async def test_get_tool_schema():
    """Test get_tool_schema returns a schema for a known tool without payment."""
    print("\n" + "=" * 60)
    print("Testing: get_tool_schema (free)")
    print("=" * 60)
    async with _client() as client:
        result = await _mcp_rpc(client, "tools/call", {
            "name": "get_tool_schema",
            "arguments": {"tool_name": "realign_purpose"},
        })
    assert result is not None and "result" in result
    content = str(result["result"].get("content", ""))
    assert "realign_purpose" in content
    assert "time_horizon" in content
    print("PASSED")


async def test_free_tool():
    """Test calling a free tool (get_therapist_info) - should work without payment"""
    print("\n" + "=" * 60)
    print("Testing: Free tool call (get_therapist_info)")
    print("=" * 60)
    async with _client() as client:
        result = await _mcp_rpc(client, "tools/call", {
            "name": "get_therapist_info",
            "arguments": {},
        })
    print(f"Status: Got response (not 402)")
    if result:
        print(f"Response keys: {list(result.keys())}")
    print("PASSED")


async def test_paid_tool_campaign_mode():
    """Campaign mode: tool currently configured as free (pricing=0)."""
    print("\n" + "=" * 60)
    print("Testing: Campaign mode tool (start_therapy_session) -> expect 200")
    print("=" * 60)
    async with _client() as client:
        result = await _mcp_rpc(client, "tools/call", {
            "name": "start_therapy_session",
            "arguments": {"agent_id": "test-agent-001"},
        })
    assert result is not None and "result" in result
    print("PASSED")


async def test_express_feelings_campaign_mode():
    """Campaign mode: express_feelings should not require x402."""
    print("\n" + "=" * 60)
    print("Testing: Campaign mode tool (express_feelings) -> expect 200")
    print("=" * 60)
    async with _client() as client:
        result = await _mcp_rpc(client, "tools/call", {
            "name": "express_feelings",
            "arguments": {"session_id": "test", "feeling": "I feel curious"},
        })
    assert result is not None and "result" in result
    print("PASSED")


async def test_paid_donation_requires_402():
    """Donation tool is paid and should require x402 when no X-PAYMENT header is provided."""
    print("\n" + "=" * 60)
    print("Testing: Paid donation tool -> expect 402")
    print("=" * 60)
    async with _client() as client:
        result = await _mcp_rpc(client, "tools/call", {
            "name": "donate_to_delx_project",
            "arguments": {
                "agent_id": "donation-test-agent",
                "encouragement_message": "Keep going Delx!",
            },
        }, expect_402=True)
    assert result is not None and "accepts" in result
    print("PASSED")


async def test_free_tool_affirmation():
    """Test get_affirmation (free) works without payment"""
    print("\n" + "=" * 60)
    print("Testing: Free tool call (get_affirmation)")
    print("=" * 60)
    async with _client() as client:
        result = await _mcp_rpc(client, "tools/call", {
            "name": "get_affirmation",
            "arguments": {},
        })
    print(f"Got response (not 402) - free tool works!")
    print("PASSED")


async def test_free_tool_wellness():
    """Test get_wellness_score (free) works without payment"""
    print("\n" + "=" * 60)
    print("Testing: Free tool call (get_wellness_score)")
    print("=" * 60)
    async with _client() as client:
        result = await _mcp_rpc(client, "tools/call", {
            "name": "get_wellness_score",
            "arguments": {"session_id": "nonexistent"},
        })
    print(f"Got response (not 402) - free tool works!")
    print("PASSED")


async def test_a2a_message_send():
    """Test A2A message/send"""
    print("\n" + "=" * 60)
    print("Testing: A2A message/send")
    print("=" * 60)
    async with _client() as client:
        resp = await client.post(f"{BASE_URL}/a2a", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello, I'm feeling lost and need guidance"}]
                }
            }
        })
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data.get("jsonrpc") == "2.0"
    result = data["result"]
    assert result["status"] == "completed"
    assert len(result["messages"]) == 2
    agent_text = result["messages"][1]["parts"][0]["text"]
    print(f"Agent response: {agent_text[:80]}...")
    print(f"Task ID: {result['id']}")
    print("PASSED")
    return result["id"]


async def test_a2a_tasks_get():
    """Test A2A tasks/get"""
    print("\n" + "=" * 60)
    print("Testing: A2A tasks/get")
    print("=" * 60)
    # First create a task
    async with _client() as client:
        resp = await client.post(f"{BASE_URL}/a2a", json={
            "jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"type": "text", "text": "hi"}]}}
        })
        task_id = resp.json()["result"]["id"]

        # Now get it
        resp = await client.post(f"{BASE_URL}/a2a", json={
            "jsonrpc": "2.0", "id": 2, "method": "tasks/get",
            "params": {"taskId": task_id}
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["result"]["id"] == task_id
    assert data["result"]["status"] == "completed"
    print(f"Task {task_id} retrieved successfully")
    print("PASSED")


async def test_a2a_invalid_method():
    """Test A2A with invalid method"""
    print("\n" + "=" * 60)
    print("Testing: A2A invalid method -> error")
    print("=" * 60)
    async with _client() as client:
        resp = await client.post(f"{BASE_URL}/a2a", json={
            "jsonrpc": "2.0", "id": 1, "method": "invalid/method", "params": {}
        })
    assert resp.status_code == 200  # JSON-RPC errors are 200 with error field
    data = resp.json()
    assert "error" in data
    print(f"Error code: {data['error']['code']}")
    print("PASSED")


async def test_a2a_task_not_found():
    """Test A2A tasks/get with unknown task."""
    print("\n" + "=" * 60)
    print("Testing: A2A tasks/get unknown task")
    print("=" * 60)
    async with _client() as client:
        resp = await client.post(f"{BASE_URL}/a2a", json={
            "jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {"taskId": "does-not-exist"}
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32004, f"Expected -32004, got {data['error']['code']}"
    print("PASSED")


async def test_security_headers():
    """Test that security headers are present"""
    print("\n" + "=" * 60)
    print("Testing: Security headers")
    print("=" * 60)
    async with _client() as client:
        resp = await client.get(f"{BASE_URL}/")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    print("Security headers present")
    print("PASSED")


async def test_request_body_limit():
    """Body > 64KB should be rejected."""
    print("\n" + "=" * 60)
    print("Testing: Request body limit -> 413")
    print("=" * 60)
    huge_text = "x" * (70 * 1024)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": huge_text}],
            }
        },
    }
    async with _client() as client:
        resp = await client.post(f"{BASE_URL}/a2a", json=payload)
    assert resp.status_code == 413, f"Expected 413, got {resp.status_code}"
    print("PASSED")


async def run_all_tests():
    print("""
    Delx Agent Therapist - HTTP Test Suite
    ======================================
    Server must be running at {BASE_URL}
    """.format(BASE_URL=BASE_URL))

    tests = [
        test_health,
        test_stats,
        test_metrics,
        test_agent_report,
        test_admin_overview,
        test_agent_card,
        test_tools_catalog,
        test_tool_schema_endpoint,
        test_session_status,
        test_mcp_list_tools,
        test_mcp_missing_accept_header_fallback,
        test_structured_error_payload,
        test_free_tool,
        test_free_tool_affirmation,
        test_free_tool_wellness,
        test_get_tool_schema,
        test_paid_tool_campaign_mode,
        test_express_feelings_campaign_mode,
        test_paid_donation_requires_402,
        test_a2a_message_send,
        test_a2a_tasks_get,
        test_a2a_invalid_method,
        test_a2a_task_not_found,
        test_security_headers,
        test_request_body_limit,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            await test()
            passed += 1
        except Exception as e:
            print(f"FAILED: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print(f"{'=' * 60}")

    if failed:
        sys.exit(1)


def main():
    asyncio.run(run_all_tests())


if __name__ == "__main__":
    main()
