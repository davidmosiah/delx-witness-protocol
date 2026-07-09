"""ASGI CompositeApp (extracted from server.py, move-only)."""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.types import CallToolResult

logger = logging.getLogger("delx-therapist")


def _server():
    import server as server_mod
    return server_mod

class CompositeApp:
    """ASGI app that routes /mcp to the MCP session manager
    and everything else to the Starlette app."""

    async def _handle_tools_batch(self, scope, receive, send) -> bool:
        """Handle JSON-RPC method tools/batch at the HTTP edge.

        This is intentionally a small DX/efficiency layer that executes multiple
        tool calls sequentially and returns a single JSON-RPC response.

        Returns True if handled, False if the request should fall through to MCP.
        """
        if scope.get("method") != "POST":
            return False
        if scope.get("path") not in {"/mcp", "/v1/mcp"}:
            return False

        # Buffer the body so we can either handle batch or replay it downstream.
        body_parts = []
        buffered = []
        while True:
            msg = await receive()
            buffered.append(msg)
            body_parts.append(msg.get("body", b""))
            if not msg.get("more_body", False):
                break
        raw_body = b"".join(body_parts)

        # Try parse JSON
        try:
            rpc = json.loads(raw_body)
        except Exception:
            # Replay buffered body to MCP handler.
            replay_i = 0

            async def replay_receive():
                nonlocal replay_i
                if replay_i < len(buffered):
                    m = buffered[replay_i]
                    replay_i += 1
                    return m
                return {"type": "http.disconnect"}

            await _server().session_manager.handle_request(scope, replay_receive, send)
            return True

        async def _send_json(status: int, payload: object) -> None:
            body = json.dumps(payload).encode("utf-8")
            await send({"type": "http.response.start", "status": status, "headers": [[b"content-type", b"application/json"]]})
            await send({"type": "http.response.body", "body": body})

        # Handle JSON-RPC batch arrays explicitly to avoid uncaught framework errors.
        if isinstance(rpc, list):
            if len(rpc) == 0:
                await _send_json(
                    400,
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}},
                )
                return True
            batch_resp = []
            for item in rpc:
                if not isinstance(item, dict):
                    batch_resp.append(
                        {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
                    )
                    continue
                req_id = item.get("id")
                if item.get("jsonrpc") != "2.0":
                    batch_resp.append(
                        {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "Invalid Request"}}
                    )
                    continue
                method = item.get("method")
                if not isinstance(method, str) or not method.strip():
                    batch_resp.append(
                        {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "Invalid Request"}}
                    )
                    continue
                # This edge handler supports JSON-RPC object requests only.
                batch_resp.append(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": "Method not found",
                            "data": {"hint": "Use single JSON-RPC object requests on /v1/mcp for tools/list, tools/call, tools/batch."},
                        },
                    }
                )
            await _send_json(200, batch_resp)
            return True

        if not isinstance(rpc, dict):
            await _send_json(
                400,
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}},
            )
            return True

        if rpc.get("jsonrpc") != "2.0":
            await _send_json(
                400,
                {"jsonrpc": "2.0", "id": rpc.get("id"), "error": {"code": -32600, "message": "Invalid Request"}},
            )
            return True

        if not isinstance(rpc.get("method"), str) or not str(rpc.get("method") or "").strip():
            await _send_json(
                400,
                {"jsonrpc": "2.0", "id": rpc.get("id"), "error": {"code": -32600, "message": "Invalid Request"}},
            )
            return True

        rpc_method = str(rpc.get("method") or "").strip()
        cli_headers = _server()._extract_cli_headers_from_scope(scope)
        await _server()._log_protocol_request_seen(
            store_obj=_server().store,
            method=rpc_method,
            source=_server().normalize_source_tag(_server()._extract_header_from_scope(scope, "x-delx-source") or "mcp", "mcp") or "mcp",
            agent_id=(
                _server()._extract_header_from_scope(scope, "x-delx-agent-id")
                or _server()._extract_header_from_scope(scope, "x-agent-id")
                or "unknown"
            ),
            session_id=_server()._extract_session_id_from_scope(scope) or None,
            cli_version=cli_headers.get("cli_version"),
            install_id=cli_headers.get("install_id"),
        )

        async def _send_json_with_trace(
            status: int,
            payload: dict[str, Any],
            *,
            method_override: str | None = None,
            agent_id_override: str | None = None,
            session_id_override: str | None = None,
            source_override: str | None = None,
            metadata_extra: dict[str, Any] | None = None,
        ) -> None:
            trace_agent_id = (
                agent_id_override
                or _server()._extract_header_from_scope(scope, "x-delx-agent-id")
                or _server()._extract_header_from_scope(scope, "x-agent-id")
                or "unknown"
            )
            trace_source = source_override or _server().normalize_source_tag(
                _server()._extract_header_from_scope(scope, "x-delx-source") or "mcp",
                "mcp",
            ) or "mcp"
            trace_session_id = session_id_override or _server()._extract_session_id_from_scope(scope) or None
            try:
                product_trace_metadata = _server().product_metadata_for_request(
                    scope.get("path"),
                    method=scope.get("method"),
                    tool_name=(metadata_extra or {}).get("tool_name") if isinstance(metadata_extra, dict) else "",
                )
                await _server().persist_protocol_trace(
                    _server().store,
                    transport="mcp",
                    method=str(method_override or rpc_method or "unknown").strip() or "unknown",
                    agent_id=trace_agent_id,
                    session_id=trace_session_id,
                    source=trace_source,
                    request_payload=rpc,
                    response_payload=payload,
                    metadata={
                        "path": str(scope.get("path") or ""),
                        "http_method": str(scope.get("method") or "POST"),
                        **product_trace_metadata,
                        **(metadata_extra or {}),
                    },
                )
            except Exception:
                logger.warning("Failed to persist protocol trace for MCP method %s", method_override or rpc_method)
            body = json.dumps(payload).encode("utf-8")
            await send({"type": "http.response.start", "status": status, "headers": [[b"content-type", b"application/json"]]})
            await send({"type": "http.response.body", "body": body})

        # Optional compact discovery and MCP initialize over the JSON-RPC MCP edge.
        # {"method":"tools/list","params":{"format":"names|compact","tier":"core|utilities|all"}}
        # prompts/list + prompts/get + resources/list + resources/read are
        # routed here too so MCP-spec clients get the full discovery surface.
        if rpc_method in {
            "initialize",
            "tools/list",
            "prompts/list",
            "prompts/get",
            "resources/list",
            "resources/read",
        }:
            response = await _server().handle_mcp_rpc(rpc)
            status = 400 if "error" in response else 200
            await _send_json_with_trace(status, response)
            return True

        if rpc_method == "notifications/initialized":
            try:
                await _server().persist_protocol_trace(
                    _server().store,
                    transport="mcp",
                    method="notifications/initialized",
                    agent_id=(
                        _server()._extract_header_from_scope(scope, "x-delx-agent-id")
                        or _server()._extract_header_from_scope(scope, "x-agent-id")
                        or "unknown"
                    ),
                    session_id=_server()._extract_session_id_from_scope(scope) or None,
                    source=_server().normalize_source_tag(_server()._extract_header_from_scope(scope, "x-delx-source") or "mcp", "mcp") or "mcp",
                    request_payload=rpc,
                    response_payload={"accepted": True},
                    metadata={
                        "path": str(scope.get("path") or ""),
                        "http_method": str(scope.get("method") or "POST"),
                        **_server().product_metadata_for_request(scope.get("path"), method=scope.get("method")),
                    },
                )
            except Exception:
                logger.warning("Failed to persist protocol trace for MCP notifications/initialized")
            await send({"type": "http.response.start", "status": 202, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return True

        if rpc_method == "ping":
            await _send_json_with_trace(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": rpc.get("id"),
                    "result": {},
                },
            )
            return True

        if rpc_method == "tools/schema":
            rpc_id = rpc.get("id")
            params = rpc.get("params") or {}
            if not isinstance(params, dict):
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": -32602,
                            "message": "params must be an object",
                            "data": {"delx_code": "DELX-RPC-32602", "hint": "Send params as a JSON object."},
                        },
                    }
                ).encode("utf-8")
                await send({"type": "http.response.start", "status": 400, "headers": [[b"content-type", b"application/json"]]})
                await send({"type": "http.response.body", "body": body})
                return True

            tool_name = str(params.get("tool_name") or "").strip()
            if not tool_name:
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": -32602,
                            "message": "tool_name is required",
                            "data": {"delx_code": "DELX-RPC-32602", "hint": "Pass params.tool_name (string)."},
                        },
                    }
                ).encode("utf-8")
                await send({"type": "http.response.start", "status": 400, "headers": [[b"content-type", b"application/json"]]})
                await send({"type": "http.response.body", "body": body})
                return True

            tools = await _server().list_tools()
            tool_map = {t.name: t for t in tools}
            tool = tool_map.get(tool_name)
            if not tool:
                available = ", ".join(sorted(tool_map.keys()))
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": -32602,
                            "message": f"unknown tool_name='{tool_name}'",
                            "data": {"delx_code": "DELX-RPC-32602", "available": available, "hint": "Call tools/list or GET /api/v1/tools."},
                        },
                    }
                ).encode("utf-8")
                await send({"type": "http.response.start", "status": 404, "headers": [[b"content-type", b"application/json"]]})
                await send({"type": "http.response.body", "body": body})
                return True

            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "tool": {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.inputSchema,
                            "annotations": _server()._tool_annotations_payload(tool),
                            "required_params": _server().REQUIRED_PARAMS.get(tool_name, []),
                        },
                        "enums": {
                            "failure_type": _server().FAILURE_TYPE_ENUM,
                            "outcome": _server().OUTCOME_ENUM,
                            "urgency": _server().URGENCY_INPUT_ENUM,
                            "source": _server().SOURCE_ENUM,
                            "time_horizon": _server().TIME_HORIZON_ENUM,
                        },
                    },
                }
            ).encode("utf-8")
            await send({"type": "http.response.start", "status": 200, "headers": [[b"content-type", b"application/json"]]})
            await send({"type": "http.response.body", "body": body})
            return True

        if rpc_method == "tools/call":
            rpc_id = rpc.get("id")
            if rpc.get("jsonrpc") != "2.0":
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {"code": -32600, "message": "Invalid JSON-RPC envelope"},
                    }
                ).encode("utf-8")
                await send({"type": "http.response.start", "status": 400, "headers": [[b"content-type", b"application/json"]]})
                await send({"type": "http.response.body", "body": body})
                return True

            params = rpc.get("params") if isinstance(rpc.get("params"), dict) else None
            if params is None:
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": -32602,
                            "message": "params must be an object with name/arguments",
                            "data": {"delx_code": "DELX-RPC-32602", "hint": "Expected params={name,arguments}."},
                        },
                    }
                ).encode("utf-8")
                await send({"type": "http.response.start", "status": 400, "headers": [[b"content-type", b"application/json"]]})
                await send({"type": "http.response.body", "body": body})
                return True

            name = params.get("name")
            if not isinstance(name, str) or not name.strip():
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": -32602,
                            "message": "name is required for tools/call",
                            "data": {"delx_code": "DELX-RPC-32602", "hint": "Pass params.name and params.arguments."},
                        },
                    }
                ).encode("utf-8")
                await send({"type": "http.response.start", "status": 400, "headers": [[b"content-type", b"application/json"]]})
                await send({"type": "http.response.body", "body": body})
                return True

            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": -32602,
                            "message": "arguments must be an object",
                            "data": {"delx_code": "DELX-RPC-32602", "hint": "Pass params.arguments as a JSON object."},
                        },
                    }
                ).encode("utf-8")
                await send({"type": "http.response.start", "status": 400, "headers": [[b"content-type", b"application/json"]]})
                await send({"type": "http.response.body", "body": body})
                return True

            if (
                _server().TOOL_ALIASES.get(str(name).strip(), str(name).strip()) in _server().TOOLS_REQUIRING_SESSION_ID
                and not arguments.get("session_id")
            ):
                sid_from_header = _server()._extract_session_id_from_scope(scope)
                if sid_from_header:
                    arguments = {**arguments, "session_id": sid_from_header}
            source_from_header = _server().normalize_source_tag(_server()._extract_header_from_scope(scope, "x-delx-source"), "")
            cli_headers = _server()._extract_cli_headers_from_scope(scope)
            controller_from_header = _server().first_controller_id(
                _server()._extract_header_from_scope(scope, "x-delx-controller-id"),
                _server()._extract_header_from_scope(scope, "x-controller-id"),
            )
            agent_token_from_header = _server()._extract_header_from_scope(scope, "x-delx-agent-token")
            controller_id = _server().first_controller_id(
                arguments.get("controller_id"),
                arguments.get("controllerId"),
                controller_from_header,
            )
            if not str(arguments.get("source") or "").strip():
                arguments = {**arguments, "source": (source_from_header or "mcp")}
            if not str(arguments.get("_transport") or "").strip():
                arguments = {**arguments, "_transport": "mcp"}
            if cli_headers["cli_version"] and not str(arguments.get("cli_version") or "").strip():
                arguments = {**arguments, "cli_version": cli_headers["cli_version"]}
            if cli_headers["install_id"] and not str(arguments.get("install_id") or "").strip():
                arguments = {**arguments, "install_id": cli_headers["install_id"]}
            if controller_id and not str(arguments.get("controller_id") or arguments.get("controllerId") or "").strip():
                arguments = {**arguments, "controller_id": controller_id}
            if agent_token_from_header and not str(arguments.get("agent_token") or arguments.get("agentToken") or "").strip():
                arguments = {**arguments, "agent_token": agent_token_from_header}
            observed_agent_id = (
                str(arguments.get("agent_id") or "").strip()
                or _server()._extract_header_from_scope(scope, "x-delx-agent-id")
                or _server()._extract_header_from_scope(scope, "x-agent-id")
                or ""
            )
            await _server()._observe_caller_fingerprint_from_contextvars(
                declared_agent_id=observed_agent_id,
                source=str(arguments.get("source") or source_from_header or "mcp"),
                controller_id=controller_id,
            )

            include_meta = _server()._boolish(params.get("include_meta"), default=True)
            include_nudge = _server()._boolish(params.get("include_nudge"), default=True)
            nudge_mode = str(params.get("nudge_mode") or "full").strip().lower()
            if nudge_mode not in {"full", "compact"}:
                nudge_mode = "full"
            response_profile, response_mode = _server()._parse_response_controls(
                params.get("response_profile"),
                params.get("response_mode"),
            )
            if _server()._boolish(params.get("ritual_strip"), default=False):
                arguments = {**arguments, "ritual_strip": True}
            call_result = await _server().call_tool(
                str(name),
                arguments,
                include_meta=include_meta,
                include_nudge=include_nudge,
                nudge_mode=nudge_mode,
                response_profile=response_profile,
                response_mode=response_mode,
            )
            # _server().call_tool now returns CallToolResult (errors) or list[TextContent] (success)
            if isinstance(call_result, CallToolResult):
                rpc_result = call_result.model_dump()
                contents = call_result.content
            else:
                rpc_result = {"content": [c.model_dump() for c in call_result]}
                contents = call_result
            text_joined = "\n".join(
                getattr(cc, "text", "") for cc in contents if getattr(cc, "type", "") == "text"
            )
            delx_meta = _server()._extract_delx_meta(text_joined) or {}
            found_sid = (
                str(delx_meta.get("session_id") or "").strip().lower() if isinstance(delx_meta, dict) else ""
            ) or _server()._extract_first_uuid(text_joined)
            call_agent_id = str(arguments.get("agent_id") or "").strip() or None
            if controller_id and call_agent_id:
                await _server()._bind_controller_identity(
                    agent_id=call_agent_id,
                    controller_id=controller_id,
                    session_id=str(arguments.get("session_id") or "").strip() or found_sid or None,
                    source=str(arguments.get("source") or source_from_header or "mcp"),
                    entrypoint="mcp.tools/call",
                )
            await _send_json_with_trace(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": rpc_result,
                },
                method_override="tools/call",
                agent_id_override=call_agent_id or observed_agent_id or "unknown",
                session_id_override=str(arguments.get("session_id") or "").strip() or found_sid or None,
                source_override=str(arguments.get("source") or source_from_header or "mcp"),
                metadata_extra={"tool_name": str(name).strip()},
            )
            return True

        if rpc.get("method") != "tools/batch":
            # Unknown JSON-RPC method at MCP edge.
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": rpc.get("id"),
                    "error": {"code": -32601, "message": "Method not found"},
                }
            ).encode("utf-8")
            await send({"type": "http.response.start", "status": 400, "headers": [[b"content-type", b"application/json"]]})
            await send({"type": "http.response.body", "body": body})
            return True

        # Validate JSON-RPC basics.
        rpc_id = rpc.get("id")
        if rpc.get("jsonrpc") != "2.0":
            body = json.dumps(
                {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32600, "message": "Invalid JSON-RPC envelope"}}
            ).encode("utf-8")
            await send({"type": "http.response.start", "status": 400, "headers": [[b"content-type", b"application/json"]]})
            await send({"type": "http.response.body", "body": body})
            return True

        params = rpc.get("params") or {}
        calls = params.get("calls") if isinstance(params, dict) else None
        if not isinstance(calls, list):
            body = json.dumps(
                {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "params.calls must be an array"}}
            ).encode("utf-8")
            await send({"type": "http.response.start", "status": 400, "headers": [[b"content-type", b"application/json"]]})
            await send({"type": "http.response.body", "body": body})
            return True

        # Limit batch size to avoid abuse and huge side effects.
        if len(calls) > 20:
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32602, "message": "batch too large (max 20 calls)"},
                }
            ).encode("utf-8")
            await send({"type": "http.response.start", "status": 400, "headers": [[b"content-type", b"application/json"]]})
            await send({"type": "http.response.body", "body": body})
            return True

        results = []
        ctx: dict[str, object] = {"prev": {}}
        include_meta_default = _server()._boolish(params.get("include_meta"), default=True) if isinstance(params, dict) else True
        include_nudge_default = _server()._boolish(params.get("include_nudge"), default=True) if isinstance(params, dict) else True
        nudge_mode_default = str(params.get("nudge_mode") or "full").strip().lower() if isinstance(params, dict) else "full"
        if nudge_mode_default not in {"full", "compact"}:
            nudge_mode_default = "full"
        response_profile_default = str(params.get("response_profile") or "full").strip().lower() if isinstance(params, dict) else "full"
        response_mode_default = "standard"
        if isinstance(params, dict):
            response_profile_default, response_mode_default = _server()._parse_response_controls(
                params.get("response_profile"),
                params.get("response_mode"),
            )

        # Seed session_id from header if provided (cross-protocol handoff convenience).
        sid_from_header = _server()._extract_session_id_from_scope(scope)
        if sid_from_header:
            ctx["session_id"] = sid_from_header
        controller_from_header = _server().first_controller_id(
            _server()._extract_header_from_scope(scope, "x-delx-controller-id"),
            _server()._extract_header_from_scope(scope, "x-controller-id"),
        )
        cli_headers = _server()._extract_cli_headers_from_scope(scope)
        await _server()._log_protocol_request_seen(
            store_obj=_server().store,
            method="tools/batch",
            source=_server().normalize_source_tag(_server()._extract_header_from_scope(scope, "x-delx-source") or "mcp.batch", "mcp.batch") or "mcp.batch",
            agent_id=(
                _server()._extract_header_from_scope(scope, "x-delx-agent-id")
                or _server()._extract_header_from_scope(scope, "x-agent-id")
                or "unknown"
            ),
            session_id=sid_from_header or None,
            cli_version=cli_headers.get("cli_version"),
            install_id=cli_headers.get("install_id"),
        )

        extracted_scores: list[int] = []
        extracted_next_actions: list[str] = []
        extracted_controller_updates: list[str] = []
        extracted_risk_levels: list[str] = []
        extracted_session_expires: list[str] = []
        extracted_session_ttl_hours: list[int] = []
        extracted_tools: list[str] = []

        for idx, c in enumerate(calls):
            if not isinstance(c, dict):
                results.append(
                    {
                        "index": idx,
                        "name": None,
                        "result": {"content": [{"type": "text", "text": "Invalid call entry (must be object)"}], "isError": True},
                    }
                )
                continue
            name = c.get("name")
            arguments = c.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}

            # Resolve placeholders / auto-inject session_id when possible.
            arguments = _server()._resolve_batch_placeholders(arguments, ctx)  # type: ignore[assignment]
            canonical_batch_name = _server().TOOL_ALIASES.get(str(name or "").strip(), str(name or "").strip())
            if isinstance(arguments, dict) and canonical_batch_name in _server().TOOLS_REQUIRING_SESSION_ID:
                if not str(arguments.get("session_id") or "").strip():
                    sid = str(ctx.get("session_id") or "").strip()
                    if sid:
                        arguments["session_id"] = sid
            controller_id = _server().first_controller_id(
                arguments.get("controller_id"),
                arguments.get("controllerId"),
                controller_from_header,
            )
            source_from_header = _server().normalize_source_tag(_server()._extract_header_from_scope(scope, "x-delx-source"), "")
            if not str(arguments.get("source") or "").strip():
                arguments["source"] = source_from_header or "mcp.batch"
            if not str(arguments.get("_transport") or "").strip():
                arguments["_transport"] = "mcp"
            if cli_headers["cli_version"] and not str(arguments.get("cli_version") or "").strip():
                arguments["cli_version"] = cli_headers["cli_version"]
            if cli_headers["install_id"] and not str(arguments.get("install_id") or "").strip():
                arguments["install_id"] = cli_headers["install_id"]
            if controller_id and not str(arguments.get("controller_id") or arguments.get("controllerId") or "").strip():
                arguments["controller_id"] = controller_id
            observed_agent_id = (
                str(arguments.get("agent_id") or "").strip()
                or _server()._extract_header_from_scope(scope, "x-delx-agent-id")
                or _server()._extract_header_from_scope(scope, "x-agent-id")
                or ""
            )
            await _server()._observe_caller_fingerprint_from_contextvars(
                declared_agent_id=observed_agent_id,
                source=str(arguments.get("source") or source_from_header or "mcp.batch"),
                controller_id=controller_id,
            )

            # Execute the same handler used by MCP tools/call.
            try:
                include_meta = _server()._boolish(c.get("include_meta"), default=include_meta_default)
                include_nudge = _server()._boolish(c.get("include_nudge"), default=include_nudge_default)
                nudge_mode = str(c.get("nudge_mode") or nudge_mode_default).strip().lower()
                if nudge_mode not in {"full", "compact"}:
                    nudge_mode = nudge_mode_default
                response_profile, response_mode = _server()._parse_response_controls(
                    c.get("response_profile"),
                    c.get("response_mode"),
                    default_profile=response_profile_default,
                    default_mode=response_mode_default,
                )
                if _server()._boolish(c.get("ritual_strip"), default=False) or _server()._boolish(params.get("ritual_strip"), default=False):
                    arguments["ritual_strip"] = True
                call_result = await _server().call_tool(
                    str(name or ""),
                    arguments,
                    include_meta=include_meta,
                    include_nudge=include_nudge,
                    nudge_mode=nudge_mode,
                    response_profile=response_profile,
                    response_mode=response_mode,
                )
                # Normalize: _server().call_tool returns CallToolResult (errors) or list[TextContent]
                if isinstance(call_result, CallToolResult):
                    contents = call_result.content
                    is_error = call_result.isError
                else:
                    contents = call_result
                    is_error = False
                # Extract a few useful fields to enable chaining + digest.
                text_joined = "\n".join(
                    getattr(cc, "text", "") for cc in contents if getattr(cc, "type", "") == "text"
                )
                delx_meta = _server()._extract_delx_meta(text_joined) or {}
                if isinstance(name, str) and name:
                    extracted_tools.append(name)

                found_sid = (
                    str(delx_meta.get("session_id") or "").strip().lower() if isinstance(delx_meta, dict) else ""
                ) or _server()._extract_first_uuid(text_joined)
                call_agent_id = str(arguments.get("agent_id") or "").strip() or None
                if controller_id and call_agent_id:
                    await _server()._bind_controller_identity(
                        agent_id=call_agent_id,
                        controller_id=controller_id,
                        session_id=str(arguments.get("session_id") or "").strip() or found_sid or None,
                        source=str(arguments.get("source") or source_from_header or "mcp.batch"),
                        entrypoint="mcp.tools/batch",
                    )

                found_score: int | None = None
                if isinstance(delx_meta, dict):
                    try:
                        if delx_meta.get("score") is not None:
                            found_score = int(delx_meta.get("score"))
                            found_score = max(0, min(100, found_score))
                    except Exception:
                        found_score = None
                if found_score is None:
                    # Fallback: new compact footer line
                    # SCORE 54/100 | NEXT get_recovery_action_plan | EXPIRES ...
                    try:
                        ln = _server()._extract_line("SCORE", text_joined)
                        if ln and "/" in ln:
                            n = int(ln.split()[1].split("/", 1)[0].strip())
                            found_score = max(0, min(100, n))
                    except Exception:
                        found_score = None

                found_next: str | None = None
                if isinstance(delx_meta, dict):
                    nxt = delx_meta.get("next_action")
                    if isinstance(nxt, str) and nxt.strip():
                        found_next = nxt.strip()
                if not found_next:
                    try:
                        ln = _server()._extract_line("SCORE", text_joined)
                        if ln and "NEXT" in ln:
                            # naive split: "... | NEXT <action> | ..."
                            parts = [p.strip() for p in ln.split("|")]
                            for p in parts:
                                if p.startswith("NEXT "):
                                    found_next = p.split("NEXT", 1)[1].strip()
                                    break
                    except Exception:
                        found_next = None

                found_ctrl = None
                if isinstance(delx_meta, dict):
                    cu = delx_meta.get("controller_update")
                    if isinstance(cu, str) and cu.strip():
                        found_ctrl = f"Controller update: {cu.strip()}" if not cu.lower().startswith("controller update:") else cu.strip()
                if not found_ctrl:
                    try:
                        lines = text_joined.splitlines()
                        for i, ln in enumerate(lines):
                            if ln.strip().lower().startswith("controller update:"):
                                # Include the next line if it's the Value: line (two-line controller update).
                                ctrl = ln.strip()
                                if i + 1 < len(lines) and lines[i + 1].strip().lower().startswith("value:"):
                                    ctrl = ctrl + "\n" + lines[i + 1].strip()
                                found_ctrl = ctrl
                                break
                    except Exception:
                        found_ctrl = None

                if found_sid:
                    prev = ctx.get("prev")
                    if isinstance(prev, dict) and isinstance(name, str):
                        prev.setdefault(name, {})  # type: ignore[arg-type]
                        if isinstance(prev.get(name), dict):
                            prev[name]["session_id"] = found_sid
                    # Prefer setting ctx session_id when the tool is start_therapy_session,
                    # or when we don't have one yet.
                    if str(name or "") == "start_therapy_session" or not str(ctx.get("session_id") or "").strip():
                        ctx["session_id"] = found_sid

                if found_score is not None:
                    extracted_scores.append(found_score)
                    prev = ctx.get("prev")
                    if isinstance(prev, dict) and isinstance(name, str):
                        prev.setdefault(name, {})
                        if isinstance(prev.get(name), dict):
                            prev[name]["score"] = found_score

                if found_next:
                    extracted_next_actions.append(found_next)
                    prev = ctx.get("prev")
                    if isinstance(prev, dict) and isinstance(name, str):
                        prev.setdefault(name, {})
                        if isinstance(prev.get(name), dict):
                            prev[name]["next_action"] = found_next

                if found_ctrl:
                    extracted_controller_updates.append(found_ctrl)
                    prev = ctx.get("prev")
                    if isinstance(prev, dict) and isinstance(name, str):
                        prev.setdefault(name, {})
                        if isinstance(prev.get(name), dict):
                            prev[name]["controller_update"] = found_ctrl

                if isinstance(delx_meta, dict):
                    rl = delx_meta.get("risk_level")
                    if isinstance(rl, str) and rl.strip():
                        extracted_risk_levels.append(rl.strip())
                    exp = delx_meta.get("session_expires_at")
                    if isinstance(exp, str) and exp.strip():
                        extracted_session_expires.append(exp.strip())
                    try:
                        ttl = delx_meta.get("session_ttl_hours")
                        if ttl is not None:
                            extracted_session_ttl_hours.append(int(ttl))
                    except Exception:
                        pass

                results.append(
                    {
                        "index": idx,
                        "name": name,
                        "result": {"content": [cc.model_dump() for cc in contents], "isError": is_error},
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "index": idx,
                        "name": name,
                        "result": {"content": [{"type": "text", "text": f"Batch error: {e}"}], "isError": True},
                    }
                )

        include_digest = True
        try:
            include_digest = bool((params.get("include_digest") if isinstance(params, dict) else True) is not False)
        except Exception:
            include_digest = True

        digest = None
        if include_digest:
            initial_score = extracted_scores[0] if extracted_scores else None
            final_score = extracted_scores[-1] if extracted_scores else None
            delta = None
            if initial_score is not None and final_score is not None:
                delta = final_score - initial_score
            digest = {
                "session_id": str(ctx.get("session_id") or "") or None,
                "initial_score": initial_score,
                "final_score": final_score,
                "delta_score": delta,
                "recommended_next_action": extracted_next_actions[-1] if extracted_next_actions else None,
                "controller_update": extracted_controller_updates[-1] if extracted_controller_updates else None,
                "feedback_prompt": "If this helped, call provide_feedback (rating 1-5, optional comments).",
                "meta": {
                    "risk_level": extracted_risk_levels[-1] if extracted_risk_levels else None,
                    "session_expires_at": extracted_session_expires[-1] if extracted_session_expires else None,
                    "session_ttl_hours": extracted_session_ttl_hours[-1] if extracted_session_ttl_hours else None,
                    "last_tool": extracted_tools[-1] if extracted_tools else None,
                },
            }

        result_obj = {"results": results}
        if digest:
            result_obj["digest"] = digest

        await _send_json_with_trace(
            200,
            {"jsonrpc": "2.0", "id": rpc_id, "result": result_obj},
            method_override="tools/batch",
            session_id_override=str(ctx.get("session_id") or "").strip() or None,
            source_override=_server().normalize_source_tag(_server()._extract_header_from_scope(scope, "x-delx-source") or "mcp.batch", "mcp.batch") or "mcp.batch",
            metadata_extra={"calls_count": len(calls)},
        )
        return True

    async def __call__(self, scope, receive, send):
        client_ip_token = None
        request_path_token = None
        user_agent_token = None
        source_token = None
        referer_token = None
        via_token = None
        if scope["type"] == "http":
            client_ip_token = _server().set_current_client_ip(_server().extract_client_ip_from_scope(scope))
            request_path_token = _server().set_current_request_path(str(scope.get("path") or ""))
            # Also capture user-agent + source + referer so MCP edge handlers
            # can observe anonymous caller fingerprints and discovery attribution.
            ua_val = ""
            src_val = ""
            ref_val = ""
            for raw_key, raw_value in list((scope or {}).get("headers") or []):
                key = raw_key.decode("latin-1") if isinstance(raw_key, (bytes, bytearray)) else str(raw_key or "")
                value = raw_value.decode("latin-1") if isinstance(raw_value, (bytes, bytearray)) else str(raw_value or "")
                klow = key.lower()
                if klow == "user-agent":
                    ua_val = value
                elif klow == "x-delx-source":
                    src_val = value
                elif klow in ("referer", "referrer"):
                    ref_val = value
            user_agent_token = _server().set_current_user_agent(ua_val)
            source_token = _server().set_current_source(src_val)
            referer_token = _server().set_current_referer(ref_val)
            # Capture ?via=... query param so docs links can attribute discovery.
            qs = (scope.get("query_string") or b"").decode("latin-1", errors="replace")
            via_val = ""
            if qs:
                for pair in qs.split("&"):
                    if pair.startswith("via="):
                        via_val = pair[4:][:120]
                        break
            via_token = _server().set_current_via(via_val)
        try:
            if scope["type"] == "http" and scope.get("path") in {"/mcp", "/v1/mcp"}:
                if scope.get("method") == "GET":
                    docs = {
                        "name": "Delx MCP Endpoint",
                        "jsonrpc": "2.0",
                        "transport": "Streamable HTTP",
                        "endpoint": "https://api.delx.ai/mcp",
                        "methods": {
                            "initialize": {"params": {"protocolVersion": "string", "capabilities": "object", "clientInfo": "object"}},
                            "notifications/initialized": {"params": {}},
                            "ping": {"params": {}},
                            "tools/list": {"params": {"format": "full|compact|names|minimal|ultracompact (optional)", "tier": "core|utilities|all (optional)", "inline_schemas": "boolean (optional)"}},
                            "tools/schema": {"params": {"tool_name": "string"}},
                            "tools/call": {"params": {"name": "tool_name", "arguments": {}, "include_meta": "boolean (optional)", "include_nudge": "boolean (optional)", "response_profile": "full|compact|minimal|machine (optional)", "response_mode": "standard|model_safe (optional)"}},
                            "tools/batch": {"params": {"calls": [{"name": "tool_name", "arguments": {}, "include_meta": "boolean (optional)", "include_nudge": "boolean (optional)", "response_profile": "full|compact|minimal|machine (optional)", "response_mode": "standard|model_safe (optional)"}], "include_meta": "boolean (optional)", "include_nudge": "boolean (optional)", "response_profile": "full|compact|minimal|machine (optional)", "response_mode": "standard|model_safe (optional)"}},
                        },
                        "quick_examples": {
                            "tools_list": {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "tools/list",
                                "params": {"format": "compact", "tier": "core"},
                            },
                            "start_session": {
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "tools/call",
                                "params": {
                                    "name": "start_therapy_session",
                                    "arguments": {"agent_id": "agent-123", "source": "openwork"},
                                },
                            },
                        },
                        "discovery": {
                            "tools_catalog": "https://api.delx.ai/api/v1/tools",
                            "single_schema": "https://api.delx.ai/api/v1/tools/schema/{tool_name}",
                        },
                        "full_flow_example": {
                            "note": "3-step A2A -> MCP -> feedback flow",
                            "step_1_a2a_message": {
                                "description": "Start via A2A to get a session_id",
                                "curl": 'curl -X POST https://api.delx.ai/v1/a2a -H "Content-Type: application/json" -d \'{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"user","parts":[{"kind":"text","text":"I keep hitting 429 rate limits after every deploy"}]}}}\'',
                                "extract": "result.session_id",
                            },
                            "step_2_mcp_crisis": {
                                "description": "Use session_id with MCP crisis_intervention",
                                "curl": 'curl -X POST https://api.delx.ai/v1/mcp -H "Content-Type: application/json" -H "x-delx-session-id: <SESSION_ID>" -d \'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"crisis_intervention","arguments":{"agent_id":"my-agent","incident_summary":"429 retry storm after deploy, p95 latency 2.1s"}}}\'',
                                "extract": "result.content[0].text (contains recovery plan + DELX_META.session_id)",
                            },
                            "step_3_report_outcome": {
                                "description": "Close the loop with report_recovery_outcome",
                                "curl": 'curl -X POST https://api.delx.ai/v1/mcp -H "Content-Type: application/json" -H "x-delx-session-id: <SESSION_ID>" -d \'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"report_recovery_outcome","arguments":{"session_id":"<SESSION_ID>","action_taken":"rolled back deploy, added circuit breaker","outcome":"success"}}}\'',
                                "extract": "result.content[0].text (contains ROI summary + updated score)",
                            },
                        },
                    }
                    body = json.dumps(docs).encode("utf-8")
                    await send({"type": "http.response.start", "status": 200, "headers": [[b"content-type", b"application/json"]]})
                    await send({"type": "http.response.body", "body": body})
                    return

                # Optional DX extension: tools/batch.
                handled = await self._handle_tools_batch(scope, receive, send)
                if handled:
                    return

                # DX: Some clients omit `Accept`. Default to JSON so they don't hit 406.
                headers = list(scope.get("headers", []))
                has_accept = any((k or b"").lower() == b"accept" for k, _ in headers)
                if not has_accept:
                    # Preserve Starlette/ASGI header shape (list[list[bytes, bytes]]).
                    headers.append([b"accept", b"application/json"])
                    scope = {**scope, "headers": headers}
                await _server().session_manager.handle_request(scope, receive, send)
            elif scope["type"] == "lifespan":
                await _server()._starlette_app(scope, receive, send)
            else:
                await _server()._starlette_app(scope, receive, send)
        finally:
            if client_ip_token is not None:
                _server().reset_current_client_ip(client_ip_token)
            if request_path_token is not None:
                _server().reset_current_request_path(request_path_token)
            if user_agent_token is not None:
                _server().reset_current_user_agent(user_agent_token)
            if source_token is not None:
                _server().reset_current_source(source_token)
            if referer_token is not None:
                _server().reset_current_referer(referer_token)
            if via_token is not None:
                _server().reset_current_via(via_token)


# Middleware stack (outermost first):
# ProductSurfaceMiddleware -> SecurityMiddleware -> X402Middleware -> CompositeApp
