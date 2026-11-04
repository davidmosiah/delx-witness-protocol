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
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://api.delx.ai"
TIMEOUT = 30
MAX_RESPONSE_BYTES = 1024 * 1024
TRUSTED_ADMIN_ORIGIN = ("https", "api.delx.ai", 443)


class MonitorFailure(Exception):
    """Only local, non-sensitive diagnostics may use this exception."""


class TargetValidationError(ValueError):
    """A static validation diagnostic, never raw URL-parser exception text."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even same-origin redirects may forward session headers or POST bodies.
        return None


def _validated_url(url: str):
    if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in url):
        raise TargetValidationError("Invalid target URL")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise TargetValidationError("Invalid target URL or port") from None
    if (parts.scheme not in {"https", "http"} or not parts.hostname or
            parts.username is not None or parts.password is not None or parts.fragment or
            parts.netloc.endswith(":")):
        raise TargetValidationError("Invalid target URL")
    if port is not None and port < 1:
        raise TargetValidationError("Invalid target URL")
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise TargetValidationError("Remote targets require HTTPS")
    return parts


def _require_admin_origin(url: str):
    parts = _validated_url(url)
    if (parts.scheme, parts.hostname, parts.port or 443) != TRUSTED_ADMIN_ORIGIN:
        raise TargetValidationError("Administrative checks require the configured HTTPS origin")


def _validated_base(base: str) -> str:
    parts = _validated_url(base)
    if parts.query:
        raise TargetValidationError("Target base must not contain a query")
    if _admin_pin():
        _require_admin_origin(base)
        if parts.path not in {"", "/"}:
            raise TargetValidationError("Administrative checks require the configured root path")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _admin_pin() -> str:
    return (os.getenv("PROTOCOL_ADMIN_PIN") or "").strip()


def _should_check_admin() -> bool:
    return bool(_admin_pin())


def _live_contract_writes_enabled() -> bool:
    return (os.getenv("DELX_ALLOW_LIVE_CONTRACT_WRITES") or "").strip().lower() in {"1", "true", "yes"}


def _admin_headers(base: str = DEFAULT_BASE) -> dict[str, str]:
    pin = _admin_pin()
    if pin:
        _validated_base(base)
    return {"x-delx-admin-pin": pin} if pin else {}


def _read_response(response) -> str:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise MonitorFailure("Monitor response exceeded the configured byte limit")
    return raw.decode("utf-8", errors="replace")


def _request_json(url: str, method: str = "GET", payload: dict | None = None, headers: dict | None = None):
    parts = _validated_url(url)
    body = None
    req_headers = dict(headers or {})
    if any(key.lower() == "x-delx-admin-pin" and value for key, value in req_headers.items()):
        _require_admin_origin(url)
        if parts.path != "/api/v1/admin/overview" or parts.query:
            raise TargetValidationError("Administrative request path not authorized")
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("content-type", "application/json")
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    started = time.time()
    try:
        with urllib.request.build_opener(_NoRedirects()).open(req, timeout=TIMEOUT) as resp:
            raw = _read_response(resp)
            elapsed_ms = int((time.time() - started) * 1000)
            code = int(resp.getcode())
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = raw
            return code, data, elapsed_ms
    except urllib.error.HTTPError as e:
        with e:
            raw = _read_response(e)
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = raw
        return int(e.code), data, -1


def _assert(cond: bool, msg: str):
    if not cond:
        raise AssertionError(msg)


def _assert_status(actual, context: str):
    if type(actual) is not int or not 100 <= actual <= 599:
        raise MonitorFailure("Monitor received an invalid HTTP status")
    if actual != 200:
        suffix = "; redirects are not followed" if 300 <= actual < 400 else ""
        raise MonitorFailure(f"{context}: expected HTTP 200, received HTTP {actual}{suffix}")


def run_smoke(base: str):
    base = _validated_base(base)
    checks = [
        f"{base}/api/v1/public-sessions?limit=2",
        f"{base}/public-sessions?limit=2",
    ]
    for url in checks:
        code, data, elapsed_ms = _request_json(url)
        _assert_status(code, "Public smoke endpoint")
        _assert(isinstance(data, dict), f"{url}: expected JSON object")
        print(f"OK smoke {url} ({elapsed_ms}ms)")
    if _should_check_admin():
        code, data, elapsed_ms = _request_json(f"{base}/api/v1/admin/overview", headers=_admin_headers(base))
        _assert_status(code, "Administrative overview")
        _assert(isinstance(data, dict), "/api/v1/admin/overview: expected JSON object")
        print(f"OK smoke {base}/api/v1/admin/overview ({elapsed_ms}ms)")
    else:
        print("SKIP smoke /api/v1/admin/overview (PROTOCOL_ADMIN_PIN not configured)")


def _a2a_call(base: str, payload: dict, headers: dict | None = None):
    code, data, elapsed_ms = _request_json(f"{base}/v1/a2a", method="POST", payload=payload, headers=headers)
    _assert_status(code, "A2A endpoint")
    if not (isinstance(data, dict) and "result" in data):
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
    if not _live_contract_writes_enabled():
        raise MonitorFailure("Contract mode writes synthetic identities and sessions; set DELX_ALLOW_LIVE_CONTRACT_WRITES=1 explicitly")

    base = _validated_base(base)

    # Public stats contract for registration funnel
    code, stats, _ = _request_json(f"{base}/api/v1/stats")
    _assert_status(code, "Public stats endpoint")
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
        code, admin, _ = _request_json(f"{base}/api/v1/admin/overview", headers=_admin_headers(base))
        _assert_status(code, "Administrative overview")
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
    _assert(resumed_sid == sid, "session reuse failed")

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
    _assert_status(code, "Tools batch endpoint")
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
            run_smoke(args.base)
        else:
            run_contract(args.base)
    except (MonitorFailure, TargetValidationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    except AssertionError:
        print("FAIL: target response did not match the expected contract", file=sys.stderr)
        return 1
    except Exception:
        print("ERROR: monitor request or target validation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
