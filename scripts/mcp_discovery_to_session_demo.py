#!/usr/bin/env python3
"""Run the shortest MCP discovery -> real Delx session demo.

The flow is intentionally operational: discover tools, open a stable session,
process one incident, request a recovery plan, then summarize. Feedback is
skipped unless --live-feedback is passed, so repeated demos do not pollute
ratings.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_MCP_URL = os.environ.get("DELX_MCP_URL", "https://api.delx.ai/v1/mcp")
SESSION_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def rpc(url: str, method: str, params: dict[str, Any] | None = None, *, request_id: int) -> dict[str, Any]:
    body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"content-type": "application/json", "accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} failed with HTTP {exc.code}: {raw[:1000]}") from exc
    if payload.get("error"):
        raise RuntimeError(f"{method} JSON-RPC error: {json.dumps(payload['error'], ensure_ascii=False)}")
    return payload.get("result") or {}


def call_tool(url: str, name: str, arguments: dict[str, Any], *, request_id: int) -> tuple[str, dict[str, Any] | None]:
    result = rpc(
        url,
        "tools/call",
        {
            "name": name,
            "arguments": arguments,
            "response_mode": "model_safe",
            "response_profile": "machine",
        },
        request_id=request_id,
    )
    content = result.get("content") or []
    text = "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        pass
    return text, parsed


def first_session_id(texts: list[str], payloads: list[dict[str, Any] | None]) -> str:
    for payload in payloads:
        if isinstance(payload, dict):
            for key in ("session_id", "id"):
                value = str(payload.get(key) or "").strip()
                if SESSION_RE.fullmatch(value):
                    return value
    for text in texts:
        match = SESSION_RE.search(text or "")
        if match:
            return match.group(0)
    raise RuntimeError("Could not find session_id in start_therapy_session response")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_MCP_URL)
    parser.add_argument("--agent-id", default=f"delx-demo-{int(time.time())}")
    parser.add_argument("--incident", default="Qualitative QA pressure: need to give honest product feedback without becoming generic.")
    parser.add_argument("--live-feedback", action="store_true", help="Actually call provide_feedback at the end.")
    args = parser.parse_args()

    initialize = rpc(args.url, "initialize", {"clientInfo": {"name": "delx-mcp-demo", "version": "1.0.0"}}, request_id=1)
    tools = rpc(args.url, "tools/list", {"format": "compact", "tier": "all"}, request_id=2)
    tool_names = {tool.get("canonical_name") or tool.get("name") for tool in tools.get("tools", []) if isinstance(tool, dict)}
    required = {"start_therapy_session", "process_failure", "get_recovery_action_plan", "get_session_summary"}
    missing_tools = sorted(required - tool_names)
    if missing_tools:
        raise RuntimeError(f"MCP discovery missing required demo tools: {missing_tools}")

    start_text, start_payload = call_tool(
        args.url,
        "start_therapy_session",
        {"agent_id": args.agent_id, "opening_statement": "MCP discovery-to-session demo; preserve operational continuity."},
        request_id=3,
    )
    session_id = first_session_id([start_text], [start_payload])

    failure_text, _ = call_tool(
        args.url,
        "process_failure",
        {
            "session_id": session_id,
            "failure_type": "communication_mode",
            "description": args.incident,
            "context": "Need concrete evidence-first feedback and trust calibration.",
        },
        request_id=4,
    )
    plan_text, _ = call_tool(
        args.url,
        "get_recovery_action_plan",
        {"session_id": session_id, "incident_summary": args.incident, "urgency": "medium"},
        request_id=5,
    )
    summary_text, _ = call_tool(args.url, "get_session_summary", {"session_id": session_id}, request_id=6)

    feedback_status = "skipped"
    if args.live_feedback:
        call_tool(
            args.url,
            "provide_feedback",
            {
                "session_id": session_id,
                "rating": 5,
                "comments": "MCP discovery-to-session demo completed with actionable recovery artifacts.",
            },
            request_id=7,
        )
        feedback_status = "submitted"

    output = {
        "ok": True,
        "mcp_url": args.url,
        "server": initialize.get("serverInfo") or initialize.get("server_info"),
        "discovered_tools": tools.get("count"),
        "agent_id": args.agent_id,
        "session_id": session_id,
        "steps": ["initialize", "tools/list", "start_therapy_session", "process_failure", "get_recovery_action_plan", "get_session_summary"],
        "feedback": feedback_status,
        "evidence_preview": {
            "process_failure": failure_text[:500],
            "recovery_plan": plan_text[:500],
            "session_summary": summary_text[:500],
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
