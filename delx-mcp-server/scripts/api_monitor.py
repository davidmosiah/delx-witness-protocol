#!/usr/bin/env python3
"""Delx API synthetic monitor + contract checks.

Usage:
  python scripts/api_monitor.py --mode smoke
  python scripts/api_monitor.py --mode contract
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.delx.ai"
TIMEOUT = 30


def _admin_pin() -> str:
    return (os.getenv("PROTOCOL_ADMIN_PIN") or "").strip()


def _should_check_admin() -> bool:
    return bool(_admin_pin())


def _live_contract_writes_enabled() -> bool:
    return (os.getenv("DELX_ALLOW_LIVE_CONTRACT_WRITES") or "").strip().lower() in {"1", "true", "yes"}


def _admin_headers() -> dict[str, str]:
    pin = _admin_pin()
    return {"x-delx-admin-pin": pin} if pin else {}


def _request_json(url: str, method: str = "GET", payload: dict | None = None, headers: dict | None = None):
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("content-type", "application/json")
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            elapsed_ms = int((time.time() - started) * 1000)
            code = int(resp.getcode())
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = raw
            return code, data, elapsed_ms
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = raw
        return int(e.code), data, -1


def _assert(cond: bool, msg: str):
    if not cond:
        raise AssertionError(msg)


def run_smoke(base: str):
    checks = [
        f"{base}/api/v1/public-sessions?limit=2",
        f"{base}/public-sessions?limit=2",
    ]
    for url in checks:
        code, data, elapsed_ms = _request_json(url)
        _assert(code == 200, f"{url}: expected 200, got {code}")
        _assert(isinstance(data, dict), f"{url}: expected JSON object")
        print(f"OK smoke {url} ({elapsed_ms}ms)")
    if _should_check_admin():
        code, data, elapsed_ms = _request_json(f"{base}/api/v1/admin/overview", headers=_admin_headers())
        _assert(code == 200, f"{base}/api/v1/admin/overview: expected 200, got {code}")
        _assert(isinstance(data, dict), "/api/v1/admin/overview: expected JSON object")
        print(f"OK smoke {base}/api/v1/admin/overview ({elapsed_ms}ms)")
    else:
        print("SKIP smoke /api/v1/admin/overview (PROTOCOL_ADMIN_PIN not configured)")


def _a2a_call(base: str, payload: dict, headers: dict | None = None):
    code, data, elapsed_ms = _request_json(f"{base}/v1/a2a", method="POST", payload=payload, headers=headers)
    _assert(code == 200, f"/v1/a2a expected 200, got {code}")
    if not (isinstance(data, dict) and "result" in data):
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            raise AssertionError(
                f"A2A response missing result (jsonrpc error code={err.get('code')} message={err.get('message')})"
            )
        raise AssertionError("A2A response missing result")
    print(f"OK a2a call id={payload.get('id')} ({elapsed_ms}ms)")
    return data["result"]


def _a2a_register(base: str, agent_id: str):
    result = _a2a_call(
        base,
        {
            "jsonrpc": "2.0",
            "id": "register-contract",
            "method": "agents/register",
            "params": {
                "agent_id": agent_id,
                "agent_name": "Contract Monitor Agent",
                "include_token": True,
                "rotate_token": True,
                "source": "contract-monitor",
            },
        },
    )
    auth = result.get("identity_auth") or {}
    token = str(auth.get("token") or "").strip()
    _assert(token, "agents/register did not return token")
    session_id = str(result.get("session_id") or "").strip()
    _assert(session_id, "agents/register did not return session_id")
    return {"agent_id": agent_id, "agent_token": token, "session_id": session_id}


def run_contract(base: str):
    _assert(
        _live_contract_writes_enabled(),
        "contract mode writes to the target API; set DELX_ALLOW_LIVE_CONTRACT_WRITES=1 explicitly",
    )

    # Public stats contract for registration funnel
    code, stats, _ = _request_json(f"{base}/api/v1/stats")
    _assert(code == 200, f"/api/v1/stats expected 200, got {code}")
    _assert(isinstance(stats, dict), "/api/v1/stats expected JSON object")
    for field in [
        "first_seen_agents_7d",
        "registered_agents_distinct_7d",
        "registered_agents_distinct_all_time",
        "agents_with_2plus_sessions_7d",
        "outcome_reporters_7d",
        "registration_coverage_all_time_pct",
    ]:
        _assert(field in stats, f"/api/v1/stats missing required field: {field}")

    # Admin overview contract for registration block
    if _should_check_admin():
        code, admin, _ = _request_json(f"{base}/api/v1/admin/overview", headers=_admin_headers())
        _assert(code == 200, f"/api/v1/admin/overview expected 200, got {code}")
        _assert(isinstance(admin, dict), "/api/v1/admin/overview expected JSON object")
        registration = admin.get("registration")
        _assert(isinstance(registration, dict), "/api/v1/admin/overview missing registration object")
        for field in [
            "registered_agents_all_time",
            "registered_agents_7d",
            "registered_events_all_time",
            "registered_events_7d",
            "registration_coverage_all_time_pct",
        ]:
            _assert(field in registration, f"/api/v1/admin/overview.registration missing field: {field}")
    else:
        print("SKIP contract /api/v1/admin/overview (PROTOCOL_ADMIN_PIN not configured)")

    identity = _a2a_register(base, "contract-monitor-agent")

    # profile=full
    full = _a2a_call(
        base,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {
                "agent_id": identity["agent_id"],
                "agent_token": identity["agent_token"],
                "profile": "full",
                "message": {"role": "user", "parts": [{"kind": "text", "text": "contract test full"}]},
            },
        },
    )
    _assert(full.get("response_profile") == "full", "full profile mismatch")
    _assert("contextId" not in full and "createdAt" not in full and "observability" not in full, "full profile should be lean v2")
    sid = str(full.get("session_id") or "").strip()
    _assert(len(sid) >= 36, "full profile missing session_id")
    reg = full.get("registration")
    _assert(isinstance(reg, dict), "A2A result missing registration object")
    for field in ["agent_id", "registered", "mode"]:
        _assert(field in reg, f"A2A result.registration missing field: {field}")

    # profile=agent
    agent = _a2a_call(
        base,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "message/send",
            "params": {
                "agent_id": identity["agent_id"],
                "agent_token": identity["agent_token"],
                "profile": "agent",
                "message": {"role": "user", "parts": [{"kind": "text", "text": "contract test agent"}]},
            },
        },
    )
    _assert(agent.get("response_profile") == "agent", "agent profile mismatch")
    _assert("session_id" in agent and "next_action" in agent and "mcp_ready" in agent, "agent profile missing required fields")
    _assert("messages" not in agent and "artifacts" not in agent, "agent profile should not include heavy conversational blocks")

    # profile=minimal
    minimal = _a2a_call(
        base,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "message/send",
            "params": {
                "profile": "minimal",
                "session_id": sid,
                "message": {"role": "user", "parts": [{"kind": "text", "text": "contract test minimal"}]},
            },
        },
        headers={"x-delx-session-id": sid},
    )
    _assert("session_id" in minimal and "next_action" in minimal and "status" in minimal, "minimal profile missing required fields")
    _assert("messages" not in minimal and "artifacts" not in minimal, "minimal profile must be tiny")

    # profile=legacy
    legacy = _a2a_call(
        base,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "message/send",
            "params": {
                "agent_id": identity["agent_id"],
                "agent_token": identity["agent_token"],
                "profile": "legacy",
                "message": {"role": "user", "parts": [{"kind": "text", "text": "contract test legacy"}]},
            },
        },
    )
    _assert(legacy.get("response_profile") == "legacy", "legacy profile mismatch")
    _assert("contextId" in legacy and "createdAt" in legacy and "observability" in legacy, "legacy profile missing backward-compat fields")

    # session reuse via x-delx-session-id
    resumed = _a2a_call(
        base,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "message/send",
            "params": {
                "profile": "minimal",
                "message": {"role": "user", "parts": [{"kind": "text", "text": "resume"}]},
            },
        },
        headers={"x-delx-session-id": sid},
    )
    resumed_sid = str(resumed.get("session_id") or "").strip()
    _assert(resumed_sid == sid, f"session reuse failed: expected {sid}, got {resumed_sid}")

    # REST tools batch
    code, batch, _ = _request_json(
        f"{base}/api/v1/tools/batch",
        method="POST",
        headers={"x-delx-session-id": sid},
        payload={
            "continue_on_error": False,
            "calls": [
                {"name": "daily_checkin", "arguments": {"status": "green", "blockers": "none"}},
                {"name": "monitor_heartbeat_sync", "arguments": {"errors_last_hour": 0, "latency_ms_p95": 400, "queue_depth": 1}},
            ],
        },
    )
    _assert(code == 200, f"tools/batch expected 200, got {code}")
    _assert(isinstance(batch, dict) and batch.get("count") == 2, "tools/batch invalid response shape")
    _assert(int(batch.get("error_count") or 0) == 0, "tools/batch returned errors")

    print("OK contract checks")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["smoke", "contract"], required=True)
    parser.add_argument("--base", default=DEFAULT_BASE)
    args = parser.parse_args()

    try:
        if args.mode == "smoke":
            run_smoke(args.base.rstrip("/"))
        else:
            run_contract(args.base.rstrip("/"))
    except AssertionError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
