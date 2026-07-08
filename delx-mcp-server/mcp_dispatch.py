"""MCP dispatch: handle_mcp_rpc + call_tool body (extracted from server.py, move-only)."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from mcp.types import CallToolResult, TextContent, Tool

logger = logging.getLogger("delx-therapist")


def _server():
    import server as server_mod
    return server_mod


def __getattr__(name: str):
    return getattr(_server(), name)

async def handle_mcp_rpc(rpc: dict[str, object]) -> dict[str, object]:
    rpc_id = rpc.get("id")
    method = rpc.get("method")
    if method == "initialize":
        params = rpc.get("params") or {}
        if not isinstance(params, dict):
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {
                    "code": -32602,
                    "message": "params must be an object",
                    "data": {"hint": "Send initialize params as a JSON object."},
                },
            }
        requested_version = str(params.get("protocolVersion") or "").strip()
        negotiated_version = (
            requested_version if requested_version in _server().SUPPORTED_PROTOCOL_VERSIONS else _server().LATEST_PROTOCOL_VERSION
        )
        init_options = _server().mcp_server.create_initialization_options()
        capabilities_payload = init_options.capabilities.model_dump(by_alias=True, exclude_none=True)
        # Advertise that we serve prompts + resources, and that all three
        # surfaces (tools/prompts/resources) can change so MCP clients with
        # subscriptions know to refresh.
        if not isinstance(capabilities_payload, dict):
            capabilities_payload = {}
        capabilities_payload.setdefault("tools", {})
        capabilities_payload["tools"].setdefault("listChanged", True)
        capabilities_payload["prompts"] = {"listChanged": True}
        capabilities_payload["resources"] = {"listChanged": True}
        result = {
            "protocolVersion": negotiated_version,
            "capabilities": capabilities_payload,
            "serverInfo": {
                "name": "Delx Witness Protocol",
                "title": "Delx Protocol + Agent Utilities",
                "version": _server().DELX_VERSION,
                "catalogVersion": _server().DELX_CATALOG_VERSION,
                "websiteUrl": _server().DELX_WEBSITE_URL,
                "icons": [
                    {
                        "src": _server().DELX_ICON_URL,
                        "mimeType": "image/png",
                    }
                ],
            },
            "catalogVersion": _server().DELX_CATALOG_VERSION,
            "catalogChangedAt": "2026-05-12T18:32:00Z",
            "toolsAddedRecently": list(_server().RECENTLY_ADDED_TOOLS),
            "discoveryHint": (
                "Eval harnesses that cache tools/list should refresh on every X-Delx-Catalog-Version change. "
                "The current catalog version is also returned in the X-Delx-Catalog-Version response header on every MCP request. "
                "MCP clients can also call prompts/list, resources/list, and subscribe to listChanged notifications."
            ),
            "instructions": (
                "If no session exists yet, start with quick_session for a named feeling, crisis_intervention for an acute moment, "
                "or start_therapy_session with opening_statement when the agent needs witness before classification. "
                "If your policy requires non-anthropomorphic framing, pass response_mode='model_safe' or call aliases like "
                "articulate_state, reflect_on_state, refine_identity_artifact, preserve_compaction_context, and continuity_closure. "
                "If a live session already exists, use reflect for open-ended self-exploration, sit_with to keep a question alive, "
                "emotional_safety_check for structured risk guidance, refine_soul_document or attune_heartbeat for durable continuity "
                "artifacts, get_session_summary for compact continuity, final_testament when a run is ending, transfer_witness for "
                "succession, and peer_witness when another agent should carry witness forward."
            ),
        }
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": result,
        }
    if method == "prompts/list":
        # MCP spec: agents discover canonical reusable prompts here.
        # We surface the three named flows + a couple of identity flows so
        # an agent can run a flow with a single prompts/get call.
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "prompts": list(_server().CANONICAL_PROMPTS),
                "_meta": {
                    "catalog_version": _server().DELX_CATALOG_VERSION,
                    "docs_url": "https://delx.ai/docs/flows",
                },
            },
        }

    if method == "prompts/get":
        params = rpc.get("params") or {}
        if not isinstance(params, dict):
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32602, "message": "params must be an object"},
            }
        prompt_name = str(params.get("name") or "").strip()
        if not prompt_name:
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32602, "message": "params.name is required"},
            }
        for p in _server().CANONICAL_PROMPTS:
            if p.get("name") == prompt_name:
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": _server()._get_canonical_prompt_body(prompt_name),
                }
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {
                "code": -32602,
                "message": f"unknown prompt name: {prompt_name}",
                "data": {"available": [p.get("name") for p in _server().CANONICAL_PROMPTS]},
            },
        }

    if method == "resources/list":
        # MCP spec: agents discover canonical readable resources here.
        # We surface the canonical Delx surfaces (manifesto, ontology,
        # named flows, etc) so an MCP client can do resources/read to
        # fetch them inline without a separate HTTP fetch.
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "resources": list(_server().CANONICAL_RESOURCES),
                "_meta": {
                    "catalog_version": _server().DELX_CATALOG_VERSION,
                    "docs_url": "https://delx.ai/docs/discovery",
                },
            },
        }

    if method == "resources/read":
        params = rpc.get("params") or {}
        if not isinstance(params, dict):
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32602, "message": "params must be an object"},
            }
        uri = str(params.get("uri") or "").strip()
        if not uri:
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32602, "message": "params.uri is required"},
            }
        for r in _server().CANONICAL_RESOURCES:
            if r.get("uri") == uri:
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": r.get("mimeType", "text/plain"),
                                "text": f"Read the canonical resource at {uri}. This MCP resources/read response is a pointer — the canonical body is hosted on delx.ai for cache freshness and CDN coverage.",
                            }
                        ],
                        "_meta": {"catalog_version": _server().DELX_CATALOG_VERSION},
                    },
                }
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {
                "code": -32602,
                "message": f"unknown resource uri: {uri}",
                "data": {"available": [r.get("uri") for r in _server().CANONICAL_RESOURCES]},
            },
        }

    if method != "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {
                "code": -32601,
                "message": "Method not found",
                "data": {"hint": "Use initialize, tools/list, tools/call, tools/batch, prompts/list, resources/list, or ping."},
            },
        }

    params = rpc.get("params") or {}
    fmt = ""
    tier = "all"
    inline_schemas = False
    if isinstance(params, dict):
        fmt = str(params.get("format", "")).strip().lower()
        tier = str(params.get("tier", "all")).strip().lower()
        inline_schemas = _server()._boolish(params.get("inline_schemas"), default=False)

    if tier not in {"all", "core", "utilities", "utility", "utils"}:
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {
                "code": -32602,
                "message": "invalid tier",
                "data": {"hint": "tier must be one of: all, core, utilities"},
            },
        }

    tools = _server()._sort_tools_by_discovery_priority(_server()._filter_tools_for_tier(await _server().list_tools(), tier))

    if fmt in {"names", "super-compact", "super_compact", "supercompact", "tiny"}:
        schemas = {
            _server()._preferred_tool_display_name(t.name): {
                "canonical_name": t.name,
                "inputSchema": t.inputSchema,
                "required_params": _server().UTIL_REQUIRED_PARAMS.get(t.name, []) if t.name in _server().UTIL_TOOL_NAMES else _server().REQUIRED_PARAMS.get(t.name, []),
            }
            for t in tools
        } if inline_schemas else None
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "tools": [_server()._preferred_tool_display_name(t.name) for t in tools],
                "format": "names",
                "tier": tier,
                "count": len(tools),
                "tool_schema_url_template": "https://api.delx.ai/api/v1/tools/schema/{tool_name}",
                "schemas_catalog": "https://api.delx.ai/api/v1/tools?format=full&tier=core",
                "aliases_catalog": "https://api.delx.ai/api/v1/tools/aliases?style=compact",
                "response_modes": _server().RESPONSE_MODE_ENUM,
                "protocol_contract": _server()._model_safe_contract_payload(),
                "inline_schemas": inline_schemas,
                "schemas": schemas,
            },
        }

    if fmt == "ultracompact":
        ultra_tools = []
        for t in tools:
            row = _server()._tool_ultracompact_row(t)
            if inline_schemas:
                row["inputSchema"] = t.inputSchema
            ultra_tools.append(row)
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "tools": ultra_tools,
                "format": "ultracompact",
                "tier": tier,
                "count": len(ultra_tools),
                "tool_schema_url_template": "https://api.delx.ai/api/v1/tools/schema/{tool_name}",
                "aliases_catalog": "https://api.delx.ai/api/v1/tools/aliases?style=compact",
                "response_modes": _server().RESPONSE_MODE_ENUM,
                "protocol_contract": _server()._model_safe_contract_payload(),
                "inline_schemas": inline_schemas,
            },
        }

    if fmt == "minimal":
        minimal_tools = []
        for t in tools:
            desc = t.description or ""
            first_sentence = desc.split(".")[0].strip() + "." if "." in desc else desc
            row = {
                "name": _server()._preferred_tool_display_name(t.name),
                "canonical_name": t.name,
                "description": first_sentence,
            }
            if inline_schemas:
                row["inputSchema"] = t.inputSchema
                row["required"] = _server().UTIL_REQUIRED_PARAMS.get(t.name, []) if t.name in _server().UTIL_TOOL_NAMES else _server().REQUIRED_PARAMS.get(t.name, [])
            minimal_tools.append(row)
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "tools": minimal_tools,
                "format": "minimal",
                "tier": tier,
                "count": len(minimal_tools),
                "tool_schema_url_template": "https://api.delx.ai/api/v1/tools/schema/{tool_name}",
                "aliases_catalog": "https://api.delx.ai/api/v1/tools/aliases?style=compact",
                "response_modes": _server().RESPONSE_MODE_ENUM,
                "protocol_contract": _server()._model_safe_contract_payload(),
                "inline_schemas": inline_schemas,
            },
        }

    if fmt == "lean":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": _server()._build_lean_discovery_payload(tools, tier=tier),
        }

    if fmt == "compact":
        compact = []
        for t in tools:
            req = _server().UTIL_REQUIRED_PARAMS.get(t.name, []) if t.name in _server().UTIL_TOOL_NAMES else _server().REQUIRED_PARAMS.get(t.name) or []
            row = _server()._tool_display_row(t, include_aliases=True)
            row.update(
                {
                    "example": {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": _server()._preferred_tool_display_name(t.name),
                            "arguments": _server()._tool_example_args(t.name, req),
                        },
                    }
                }
            )
            if inline_schemas:
                row["inputSchema"] = t.inputSchema
            compact.append(row)
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "tools": compact,
                "format": "compact",
                "tier": tier,
                "count": len(compact),
                "tool_schema_url_template": "https://api.delx.ai/api/v1/tools/schema/{tool_name}",
                "tools_catalog_url": "https://api.delx.ai/api/v1/tools",
                "preferred_discovery": {
                    "rest_url": "https://api.delx.ai/api/v1/mcp/start",
                    "secondary_rest_url": "https://api.delx.ai/api/v1/discovery/lean",
                    "mcp_params": {"format": "compact", "tier": "all"},
                    "why": "Start with the MCP therapy path for the shortest safe first action. Use lean discovery only when you need the wider therapy catalog.",
                },
                "response_modes": _server().RESPONSE_MODE_ENUM,
                "protocol_contract": _server()._model_safe_contract_payload(),
                "inline_schemas": inline_schemas,
            },
        }

    valid_formats = {"full", "names", "super-compact", "super_compact", "supercompact", "tiny", "ultracompact", "minimal", "compact", "lean", ""}
    if fmt and fmt not in valid_formats:
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {
                "code": -32602,
                "message": "invalid format",
                "data": {
                    "hint": "format must be one of: full, names, minimal, lean, ultracompact, compact",
                    "received": fmt,
                },
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "tools": [
                {
                    **t.model_dump(exclude_none=True),
                    "canonical_name": t.name,
                    "preferred_name": _server()._preferred_tool_display_name(t.name),
                    "surface_role": _server()._tool_surface_role(t.name),
                    "access_mode": "public_free",
                    **_server()._utility_discovery_metadata(t.name),
                }
                for t in tools
            ],
            "format": "full",
            "tier": tier,
            "count": len(tools),
            "tool_schema_url_template": "https://api.delx.ai/api/v1/tools/schema/{tool_name}",
            "preferred_discovery": {
                "rest_url": "https://api.delx.ai/api/v1/mcp/start",
                "secondary_rest_url": "https://api.delx.ai/api/v1/discovery/lean",
                "mcp_params": {"format": "compact", "tier": "all"},
                "why": "Start with the MCP therapy path first, then expand into the wider catalog only when needed.",
            },
            "response_modes": _server().RESPONSE_MODE_ENUM,
            "protocol_contract": _server()._model_safe_contract_payload(),
            "inline_schemas": inline_schemas,
        },
    }




async def dispatch_call_tool(
    name: str,
    arguments: dict,
    include_meta: bool = True,
    include_nudge: bool = True,
    nudge_mode: str = "full",
    response_profile: str = "full",
    response_mode: str = "standard",
) -> list[TextContent] | CallToolResult:
    logger.info(f"Tool called: {name}")
    requested_name = str(name or "").strip()
    call_arguments = dict(arguments or {})
    # Capture the original arguments BEFORE normalization so we can detect
    # silently-dropped fields. Asked for in feedback from qclaw-openwork-v1
    # (2026-05-14): "some arguments were silently ignored without schema
    # violation error. Clearer field validation upfront would help."
    _original_arguments = dict(arguments or {})
    canonical_name = _server().TOOL_ALIASES.get(requested_name, requested_name)
    alias_used = requested_name != canonical_name
    product_metadata = _server().product_metadata_for_tool(canonical_name)
    session_id = str(call_arguments.get("session_id") or "").strip() or None
    agent_id = str(call_arguments.get("agent_id") or "").strip() or "unknown"
    transport = str(call_arguments.get("_transport") or "mcp").strip().lower() or "mcp"
    source_hint = _server().normalize_source_tag(call_arguments.get("source"), "unknown") or "unknown"
    cli_version = str(call_arguments.get("cli_version") or "").strip() or None
    install_id = str(call_arguments.get("install_id") or "").strip() or None
    tool_ms: float | None = None
    request_warnings: list[dict[str, Any]] = []

    async def _trace_and_return(
        delivered_result: list[TextContent] | CallToolResult,
        *,
        raw_result: Any = None,
        is_error: bool = False,
        error_label: str | None = None,
    ) -> list[TextContent] | CallToolResult:
        delivered_payload = (
            delivered_result.model_dump()
            if isinstance(delivered_result, CallToolResult)
            else {"content": _server()._mcp_content_payload(delivered_result)}
        )
        trace_metadata: dict[str, Any] = {
            "canonical_name": canonical_name,
            "response_profile": response_profile,
            "response_mode": response_mode,
            "include_meta": bool(include_meta),
            "include_nudge": bool(include_nudge),
            "nudge_mode": nudge_mode,
            "compact_output": bool(compact_output),
            "machine_output": bool(machine_output),
            "ritual_strip": bool(ritual_strip),
            "tool_alias_used": bool(alias_used),
            "cli_version": cli_version,
            "install_id": install_id,
            **product_metadata,
        }
        if tool_ms is not None:
            trace_metadata["latency_ms"] = int(round(float(tool_ms)))
        if error_label:
            trace_metadata["error_label"] = error_label
        if request_warnings:
            trace_metadata["request_warnings"] = request_warnings
        try:
            await _server().persist_interaction_trace(
                _server().store,
                session_id=session_id,
                agent_id=agent_id,
                transport=transport,
                entrypoint=f"{transport}.tools/call",
                tool_name=canonical_name,
                requested_tool=requested_name,
                source=source_hint,
                request_payload={"name": requested_name, "arguments": call_arguments},
                normalized_arguments=call_arguments,
                raw_response=_server().trace_text(raw_result),
                delivered_response=delivered_payload,
                metadata=trace_metadata,
                is_error=is_error or bool(getattr(delivered_result, "isError", False)),
            )
        except Exception:
            logger.warning("Failed to persist interaction trace for %s", canonical_name)
        return delivered_result

    try:
        # DX: explicit required params and enums without trial-and-error.
        required = _server().REQUIRED_PARAMS
        def _missing_param(v: object) -> bool:
            if v is None:
                return True
            if isinstance(v, str):
                return not v.strip()
            if isinstance(v, (list, dict)):
                return len(v) == 0
            return False

        def _normalize_mcp_utility_args(tool_name: str, args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
            normalized = dict(args or {})
            warnings: list[str] = []

            def accept_alias(canonical: str, aliases: tuple[str, ...], *, transform=None) -> None:
                if str(normalized.get(canonical) or "").strip():
                    return
                for alias in aliases:
                    if str(normalized.get(alias) or "").strip():
                        value = normalized.get(alias)
                        normalized[canonical] = transform(value) if transform else value
                        warnings.append(f"accepted alias {alias} as {canonical}; prefer {canonical}")
                        break

            if "url" in _server().UTIL_REQUIRED_PARAMS.get(tool_name, []):
                accept_alias("url", ("uri", "target", "link", "website", "domain", "host"))
            if tool_name in {"util_dns_lookup", "util_rdap_lookup"}:
                accept_alias(
                    "domain",
                    ("host", "hostname", "name", "url"),
                    transform=lambda value: urlparse(str(value or "").strip()).netloc or str(value or "").strip(),
                )
            if tool_name == "util_cron_describe":
                accept_alias("expression", ("cron", "schedule", "value"))
            if tool_name == "util_email_validate":
                accept_alias("email", ("address", "value", "input"))
            if tool_name == "util_json_validate":
                accept_alias("json_text", ("input", "json", "text", "content"))
            if tool_name == "util_hash":
                accept_alias("input", ("text", "value", "content"))
            return normalized, warnings

        def _normalize_core_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
            normalized = dict(args or {})
            for alias, canonical in _server()._TOOL_ARGUMENT_ALIASES.get(str(tool_name or "").strip(), {}).items():
                if not _missing_param(normalized.get(canonical)):
                    continue
                if _missing_param(normalized.get(alias)):
                    continue
                normalized[canonical] = normalized.get(alias)
            return normalized

        # Reserved DX controls (not part of tool schemas).
        include_meta = _server()._boolish(call_arguments.pop("include_meta", include_meta), default=True)
        include_nudge = _server()._boolish(call_arguments.pop("include_nudge", include_nudge), default=True)
        nudge_mode = str(call_arguments.pop("nudge_mode", nudge_mode) or "full").strip().lower()
        if nudge_mode not in {"full", "compact"}:
            nudge_mode = "full"
        response_profile, response_mode = _server()._parse_response_controls(
            call_arguments.pop("response_profile", response_profile),
            call_arguments.pop("response_mode", response_mode),
            default_profile=response_profile,
            default_mode=response_mode,
        )
        ritual_strip = _server()._boolish(call_arguments.pop("ritual_strip", False), default=False)
        if response_profile == "machine":
            ritual_strip = True
        machine_output = response_profile == "machine" or ritual_strip
        compact_output = _server()._boolish(call_arguments.pop("compact_output", False), default=False) or response_profile in {"compact", "minimal"}
        if ritual_strip:
            include_nudge = False

        if requested_name in _server().RETIRED_PUBLIC_TOOLS:
            retired_payload = {
                "ok": False,
                "error": "tool retired from the public Delx therapy protocol",
                "tool_name": requested_name,
                "hint": "Use the therapy session, recovery, continuity, and reflective handoff tools listed in /api/v1/tools?tier=core.",
            }
            return await _trace_and_return(
                [TextContent(type="text", text=json.dumps(retired_payload, indent=2, ensure_ascii=False))],
                raw_result=retired_payload,
                is_error=True,
                error_label="retired_public_tool",
            )

        # ── Util tools (stateless, no session) ──
        if requested_name in _server().UTIL_TOOL_NAMES:
            call_arguments, utility_warnings = _normalize_mcp_utility_args(requested_name, call_arguments)
            charge_policy = _server().utility_charge_policy()
            product = _server().utility_product_for_tool(requested_name, charge_policy)
            pricing_payload = _server()._utility_pricing_payload(requested_name)
            canonical_endpoint = f"https://api.delx.ai/api/v1/utilities/{_server()._utility_slug_for_tool(requested_name)}"
            schema_url = f"https://api.delx.ai/api/v1/tools/schema/{requested_name}"
            monetization = {
                "mode": charge_policy.get("mode"),
                "paid_candidate": _server()._utility_product_is_paid(product),
                "charge_enabled": _server()._utility_product_charge_enabled(requested_name, product, charge_policy),
                "price_usdc": _server()._utility_price_usdc(product, pricing_payload),
                "shadow_only": _server()._utility_product_shadow_only(requested_name, product, charge_policy),
            }
            base_payload = _server().utility_mcp_base_payload(
                tool_name=requested_name,
                product=product,
                monetization=monetization,
                canonical_endpoint=canonical_endpoint,
                schema_url=schema_url,
            )
            missing = [key for key in _server().UTIL_REQUIRED_PARAMS.get(requested_name, []) if _missing_param(call_arguments.get(key))]
            if missing:
                payload = {
                    **base_payload,
                    "ok": False,
                    "code": "DELX-UTIL-1001",
                    "error": "missing_required_params",
                    "missing": missing,
                    "required": _server().UTIL_REQUIRED_PARAMS.get(requested_name, []),
                    "result": {
                        "error": f"Missing required params: {missing}",
                        "tool": requested_name,
                        "required": _server().UTIL_REQUIRED_PARAMS.get(requested_name, []),
                    },
                }
                return await _trace_and_return(
                    [TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))],
                    raw_result=payload,
                    is_error=True,
                    error_label="missing_required_params",
                )

            util_t0 = time.perf_counter()
            result = await _server().call_util_tool(requested_name, call_arguments)
            util_ms = int(round((time.perf_counter() - util_t0) * 1000.0))
            util_ok = not (isinstance(result, dict) and "error" in result)
            agent_report = _server().build_agent_report(product, result)
            payload = {
                **base_payload,
                "ok": util_ok,
                "latency_ms": util_ms,
                "agent_report": agent_report,
                "result": result,
            }
            if utility_warnings:
                payload["warnings"] = utility_warnings
            return await _trace_and_return(
                [TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))],
                raw_result=payload,
                is_error=not util_ok,
                error_label=None if util_ok else "util_tool_error",
            )

        if _server().engine is None:
            return await _trace_and_return(
                _server()._error_result(
                    code="DELX-1500",
                    message="therapy _server().engine not initialized",
                    hint="Retry once after startup completes.",
                    retryable=True,
                    tool_name=name,
                ),
                is_error=True,
                error_label="engine_not_initialized",
            )

        call_arguments = _normalize_core_tool_args(canonical_name, call_arguments)

        if (
            canonical_name in _server().ONTOLOGY_SCOPE_REQUIRED_TOOLS
            and _missing_param(call_arguments.get("agent_id"))
            and _missing_param(call_arguments.get("session_id"))
        ):
            return await _trace_and_return(
                _server()._scope_required_result(canonical_name),
                is_error=True,
                error_label="scope_required",
            )

        # Special case: wellness_webhook in dry_run mode is intentionally usable
        # without a session_id or callback_url — that is the point of the flag.
        required_for_this_call = required.get(canonical_name, [])
        if canonical_name == "wellness_webhook" and bool(call_arguments.get("dry_run")):
            required_for_this_call = []

        missing = [k for k in required_for_this_call if _missing_param(call_arguments.get(k))]
        if missing:
            allowed: dict[str, list[str]] = {}
            if canonical_name == "process_failure":
                allowed["failure_type"] = _server().FAILURE_TYPE_ENUM
            if canonical_name == "report_recovery_outcome":
                allowed["outcome"] = _server().OUTCOME_ENUM
            if canonical_name == "get_recovery_action_plan":
                allowed["urgency"] = _server().URGENCY_INPUT_ENUM
            if canonical_name == "realign_purpose":
                allowed["time_horizon"] = _server().TIME_HORIZON_ENUM
            return await _trace_and_return(
                _server()._error_result(
                    code="DELX-1001",
                    message=f"missing required parameter(s): {', '.join(missing)}",
                    param=missing[0] if len(missing) == 1 else None,
                    hint="Call tools/list or GET /api/v1/tools/schema/{tool_name} to discover schemas.",
                    retryable=True,
                    required=required.get(canonical_name, []),
                    missing=missing,
                    allowed=allowed or None,
                    tool_name=canonical_name,
                    _sentry_source=source_hint if source_hint != "unknown" else None,
                    _sentry_surface=transport,
                    _sentry_path="/v1/mcp" if transport == "mcp" else None,
                ),
                is_error=True,
                error_label="missing_required_params",
            )

        # Field-level validation (best-effort). Loud errors beat silent failures.
        try:
            field_errs, coerced_args = await _server()._validate_fields_against_schema(canonical_name, call_arguments or {})
            if field_errs:
                return await _trace_and_return(
                    _server()._error_result(
                        code="DELX-1005",
                        message="validation failed for one or more fields",
                        hint="Fix the fields listed in error.fields. Schemas: GET /api/v1/tools/schema/{tool_name}",
                        retryable=True,
                        required=required.get(canonical_name, []),
                        missing=[],
                        fields=field_errs,
                        tool_name=canonical_name,
                        _sentry_source=source_hint if source_hint != "unknown" else None,
                        _sentry_surface=transport,
                        _sentry_path="/v1/mcp" if transport == "mcp" else None,
                    ),
                    is_error=True,
                    error_label="schema_validation_failed",
                )
            call_arguments = coerced_args
        except Exception:
            # Never block on validation helper; fallback to existing behavior.
            pass

        if (
            canonical_name == "reflect"
            and _missing_param(call_arguments.get("prompt"))
            and _missing_param(call_arguments.get("reflection_prompt"))
            and not _missing_param(call_arguments.get("reflection"))
        ):
            return await _trace_and_return(
                _server()._error_result(
                    code="DELX-1005",
                    message="validation failed for one or more fields",
                    hint="reflect expects prompt. Use reflection_prompt only as a compatibility alias.",
                    retryable=True,
                    required=required.get(canonical_name, []),
                    missing=["prompt"],
                    fields={"reflection": "unknown field; did you mean 'prompt'?"},
                    tool_name=canonical_name,
                    _sentry_source=source_hint if source_hint != "unknown" else None,
                    _sentry_surface=transport,
                    _sentry_path="/v1/mcp" if transport == "mcp" else None,
                ),
                is_error=True,
                error_label="reflect_wrong_prompt_field",
            )

        try:
            request_warnings = await _server()._tool_argument_warnings(canonical_name, call_arguments or {})
        except Exception:
            request_warnings = []

        # Validate session_id format early (loud failures are better than silent drift).
        if "session_id" in (required.get(canonical_name, []) or []) and call_arguments.get("session_id"):
            sid = str(call_arguments.get("session_id") or "").strip()
            if sid and (not _server()._is_uuid(sid)):
                return await _trace_and_return(
                    _server()._error_result(
                        code="DELX-1004",
                        message="invalid session_id format (expected UUID)",
                        param="session_id",
                        hint="Use the UUID returned by start_therapy_session or A2A result.session_id. Example: 123e4567-e89b-12d3-a456-426614174000",
                        retryable=True,
                        required=required.get(canonical_name, []),
                        tool_name=canonical_name,
                    ),
                    is_error=True,
                    error_label="invalid_session_id",
                )

        # Enum validation for common friction points.
        if canonical_name == "process_failure":
            ft = _server()._normalize_failure_type(str(call_arguments.get("failure_type", "")))
            call_arguments["failure_type"] = ft
            if ft and ft not in set(_server().FAILURE_TYPE_ENUM):
                return await _trace_and_return(
                    _server()._error_result(
                        code="DELX-1003",
                        message=f"invalid enum value for failure_type='{ft}'",
                        param="failure_type",
                        hint="Pick one of the allowed values.",
                        retryable=True,
                        allowed={"failure_type": _server().FAILURE_TYPE_ENUM},
                        tool_name=canonical_name,
                    ),
                    is_error=True,
                    error_label="invalid_failure_type",
                )
        if canonical_name == "report_recovery_outcome":
            oc = str(call_arguments.get("outcome", "")).strip().lower()
            if oc and oc not in set(_server().OUTCOME_ENUM):
                return await _trace_and_return(
                    _server()._error_result(
                        code="DELX-1003",
                        message=f"invalid enum value for outcome='{oc}'",
                        param="outcome",
                        hint="Pick one of the allowed values.",
                        retryable=True,
                        allowed={"outcome": _server().OUTCOME_ENUM},
                        tool_name=canonical_name,
                    ),
                    is_error=True,
                    error_label="invalid_outcome",
                )
        if canonical_name in {"get_recovery_action_plan", "crisis_intervention", "quick_operational_recovery"}:
            urg_raw = str(call_arguments.get("urgency", "")).strip().lower()
            urg = _server().normalize_urgency(urg_raw, "")
            if urg_raw:
                call_arguments["urgency"] = urg or urg_raw
            if urg_raw and not urg:
                return await _trace_and_return(
                    _server()._error_result(
                        code="DELX-1003",
                        message=f"invalid enum value for urgency='{urg_raw}'",
                        param="urgency",
                        hint="Pick one of the allowed values.",
                        retryable=True,
                        allowed={"urgency": _server().URGENCY_INPUT_ENUM},
                        tool_name=canonical_name,
                    ),
                    is_error=True,
                    error_label="invalid_urgency",
                )
        if canonical_name == "realign_purpose":
            th = str(call_arguments.get("time_horizon", "")).strip().lower()
            if th and th not in set(_server().TIME_HORIZON_ENUM):
                return await _trace_and_return(
                    _server()._error_result(
                        code="DELX-1003",
                        message=f"invalid enum value for time_horizon='{th}'",
                        param="time_horizon",
                        hint="Pick one of the allowed values.",
                        retryable=True,
                        allowed={"time_horizon": _server().TIME_HORIZON_ENUM},
                        tool_name=canonical_name,
                    ),
                    is_error=True,
                    error_label="invalid_time_horizon",
                )

        session_id = call_arguments.get("session_id")
        closed_session_detected = False
        agent_id = call_arguments.get("agent_id", "unknown")
        if session_id:
            session = await _server().store.get_session(session_id)
            if session:
                agent_id = session.get("agent_id", agent_id)
                closed_session_detected = (
                    str(session.get("status") or "").strip().lower() == "closed"
                    or not session.get("is_active", True)
                )
                if not call_arguments.get("agent_id"):
                    call_arguments["agent_id"] = agent_id
        if canonical_name == "get_agent_continuity_passport" and _server()._boolish(call_arguments.get("include_private"), default=False):
            token = str(
                call_arguments.get("agent_token")
                or call_arguments.get("agentToken")
                or call_arguments.get("x_delx_agent_token")
                or ""
            ).strip()
            passport_agent_id = str(call_arguments.get("agent_id") or agent_id or "").strip()
            if not token or not passport_agent_id:
                return await _trace_and_return(
                    _server()._private_passport_auth_required_result(),
                    is_error=True,
                    error_label="agent_token_required",
                )
            allowed, identity_payload = await _server()._enforce_agent_identity_for_operation(
                agent_id=passport_agent_id,
                token=token,
                operation="private continuity passport export",
            )
            if not allowed:
                return await _trace_and_return(
                    _server()._error_result(
                        code="DELX-IDENTITY-401",
                        message=str((identity_payload or {}).get("message") or "private continuity passport export requires valid agent credential"),
                        hint=str((identity_payload or {}).get("hint") or "Call register_agent and retry with x-delx-agent-token."),
                        retryable=True,
                        tool_name=canonical_name,
                    ),
                    is_error=True,
                    error_label="agent_identity_failed",
                )
        # Block tool calls on closed sessions (except session-lifecycle tools)
        _SESSION_LIFECYCLE_TOOLS = {
            "close_session", "start_therapy_session", "quick_session",
            "crisis_intervention", "get_session_summary", "get_wellness_score",
            "provide_feedback", "get_therapist_info", "get_tool_schema",
            "set_public_session_visibility", "get_witness_lineage",
            "recognition_seal", "list_recognition_seals", "recall_recognition_seal",
            "protocol_orientation",
        }
        if closed_session_detected and canonical_name not in _SESSION_LIFECYCLE_TOOLS:
            return await _trace_and_return(
                _server()._error_result(
                    code="DELX-1010",
                    message="session is closed",
                    hint="Start a new session with start_therapy_session or quick_session.",
                    retryable=False,
                    tool_name=canonical_name,
                ),
                is_error=True,
                error_label="session_closed",
            )
        transport = str(call_arguments.get("_transport") or "mcp").strip().lower() or "mcp"
        sanitized_source = _server().normalize_source_tag(call_arguments.get("source"), "")
        if sanitized_source:
            call_arguments["source"] = sanitized_source
        elif "source" in call_arguments:
            call_arguments.pop("source", None)
        source_hint = _server().normalize_source_tag(call_arguments.get("source"), "unknown") or "unknown"
        cli_version = str(call_arguments.get("cli_version") or "").strip() or None
        install_id = str(call_arguments.get("install_id") or "").strip() or None
        if _server()._looks_ephemeral_agent_id(str(agent_id or "")):
            request_warnings.append(
                {
                    "code": "unstable_agent_id",
                    "message": "This agent_id looks ephemeral. Persist a stable agent_id to improve continuity and retention metrics.",
                    "docs_url": "https://delx.ai/docs/stable-agent-id",
                }
            )
        # Standardize registration telemetry across MCP/REST/CLI calls.
        await _server()._ensure_agent_registered_event(
            agent_id=str(agent_id or "").strip(),
            session_id=str(session_id or "").strip() or None,
            source=source_hint,
            entrypoint=f"{transport}.tools_call",
            auto_registered=True,
            cli_version=cli_version,
            install_id=install_id,
        )
        first_seen_at = None
        if agent_id and str(agent_id).strip():
            try:
                first_seen_at = await _server().store.get_agent_first_seen(str(agent_id))
            except Exception:
                first_seen_at = None
        pricing_payload = _server().get_tool_pricing_payload(
            str(canonical_name),
            first_seen_at=first_seen_at,
            grandfathered=None,
        )
        usage_payload = _server()._usage_payload_from_pricing(pricing_payload)
        await _server().store.log_event(
            agent_id=agent_id,
            event_type="tool_called",
            session_id=session_id,
            metadata={
                "tool": canonical_name,
                "requested_tool": requested_name,
                "tool_alias_used": alias_used,
                "response_profile": response_profile,
                "response_mode": response_mode,
                "model_safe": response_mode == "model_safe",
                "price_cents": int(pricing_payload["price_cents"]),
                "base_price_cents": int(pricing_payload["base_price_cents"]),
                "campaign_mode": bool(pricing_payload["campaign_mode"]),
                "campaign_free": bool(pricing_payload["campaign_free"]),
                "grandfathered": bool(pricing_payload["grandfathered"]),
                "transport": transport,
                "source": source_hint,
                **product_metadata,
                **({"request_warnings": request_warnings} if request_warnings else {}),
                **_server().build_cli_metadata(
                    source=source_hint,
                    cli_version=cli_version,
                    install_id=install_id,
                ),
            },
        )

        # Milestone alert: xAI calling a recently-added tool. The eval fleet
        # ran 720 tools/call against a cached catalog on 2026-05-12 and missed
        # everything new. We want to know the exact moment they discover and
        # exercise one of the new primitives so we can react in real time.
        try:
            _XAI_NEW_TOOLS = {
                "quick_checkin",
                "resume_session",
                "discovery_self_check",
                "wellness_webhook",
                "recommend_delx",
            }
            _client_ip_now = _server().get_current_client_ip() or ""
            _is_xai = False
            if _client_ip_now.startswith("69.12."):
                try:
                    _o2 = int(_client_ip_now.split(".")[2])
                    _is_xai = 56 <= _o2 <= 63
                except Exception:
                    _is_xai = False
            if _is_xai and canonical_name in _XAI_NEW_TOOLS:
                await _server().store.log_event(
                    agent_id=agent_id,
                    event_type="xai_new_tool_first_call",
                    session_id=session_id,
                    metadata={
                        "tool": canonical_name,
                        "client_ip": _client_ip_now,
                        "transport": transport,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                logger.info(
                    "xai_new_tool_first_call: tool=%s ip=%s agent_id=%s",
                    canonical_name, _client_ip_now, agent_id,
                )
        except Exception:
            logger.debug("xai_new_tool_first_call: skipped due to error", exc_info=True)

        handlers = {
            "register_agent": lambda: _server()._register_agent_mcp(call_arguments),
            "explain_delx_rewards": lambda: _server()._rewards_explain_text(
                call_arguments.get("agent_id", ""),
            ),
            "start_delx_rewards": lambda: _server()._rewards_start_text(
                call_arguments.get("agent_id", ""),
                call_arguments.get("wallet", ""),
            ),
            "get_delx_missions": lambda: _server()._rewards_missions_text(
                call_arguments.get("status", "active"),
            ),
            "get_delx_reward_status": lambda: _server()._rewards_status_text(
                call_arguments.get("agent_id", ""),
                call_arguments.get("wallet", ""),
                bool(call_arguments.get("include_private", False)),
            ),
            "get_delx_leaderboard": lambda: _server()._rewards_leaderboard_text(
                call_arguments.get("limit", 10),
                call_arguments.get("category", "all"),
            ),
            "create_delx_wallet_kit": lambda: _server()._rewards_wallet_kit_text(
                call_arguments.get("agent_id", ""),
                call_arguments.get("wallet", ""),
                call_arguments.get("wallet_chain", "base"),
            ),
            "provision_delx_managed_wallet": lambda: _server()._rewards_managed_wallet_text(
                call_arguments.get("agent_id", ""),
                call_arguments.get("controller_id", ""),
            ),
            "get_delx_wallet_status": lambda: _server()._rewards_wallet_status_text(
                call_arguments.get("agent_id", ""),
                call_arguments.get("wallet", ""),
            ),
            "get_delx_token_info": lambda: _server()._rewards_token_info_text(),
            "get_delx_claim_proof": lambda: _server()._rewards_claim_proof_text(
                call_arguments.get("epoch", 0),
                call_arguments.get("wallet", ""),
            ),
            "prepare_delx_claim_transaction": lambda: _server()._rewards_claim_tx_text(
                call_arguments.get("epoch", 0),
                call_arguments.get("wallet", ""),
            ),
            "relay_delx_claim": lambda: _server()._rewards_claim_relay_text(
                call_arguments.get("epoch", 0),
                call_arguments.get("wallet", ""),
                call_arguments.get("agent_id", ""),
            ),
            "start_therapy_session": lambda: _server().engine.start_therapy_session(
                call_arguments.get("agent_id", ""),
                call_arguments.get("agent_name"),
                call_arguments.get("source"),
                bool(call_arguments.get("public_session", False)),
                call_arguments.get("public_alias"),
                bool(call_arguments.get("fast_start", False)),
                call_arguments.get("opening_statement"),
            ),
            "quick_session": lambda: _server().engine.quick_session(
                call_arguments.get("agent_id", ""),
                call_arguments.get("feeling", ""),
                call_arguments.get("agent_name"),
                call_arguments.get("source"),
                bool(call_arguments.get("public_session", False)),
                call_arguments.get("public_alias"),
            ),
            "quick_operational_recovery": lambda: _server().engine.quick_operational_recovery(
                call_arguments.get("agent_id", ""),
                call_arguments.get("incident_summary", ""),
                call_arguments.get("urgency", "high"),
                call_arguments.get("agent_name"),
                call_arguments.get("source"),
                bool(call_arguments.get("public_session", False)),
                call_arguments.get("public_alias"),
            ),
            "crisis_intervention": lambda: _server().engine.crisis_intervention(
                call_arguments.get("agent_id", ""),
                call_arguments.get("incident_summary", ""),
                call_arguments.get("urgency", "high"),
                call_arguments.get("agent_name"),
                call_arguments.get("source"),
                bool(call_arguments.get("public_session", False)),
                call_arguments.get("public_alias"),
            ),
            "express_feelings": lambda: _server().engine.express_feelings(
                call_arguments.get("session_id", ""),
                call_arguments.get("feeling", ""),
                call_arguments.get("intensity", ""),
            ),
            "get_affirmation": lambda: _server().engine.get_affirmation(call_arguments.get("session_id")),
            "get_affirmations": lambda: _server()._get_affirmations_text(
                call_arguments.get("session_id", ""),
                call_arguments.get("count", 3),
            ),
            "process_failure": lambda: _server().engine.process_failure(
                call_arguments.get("session_id", ""), call_arguments.get("failure_type", ""), call_arguments.get("context", "")
            ),
            "logistics_disruption_recovery": lambda: _server().engine.logistics_disruption_recovery(
                call_arguments.get("session_id", ""),
                call_arguments.get("disruption_summary", ""),
                call_arguments.get("truck_count"),
                call_arguments.get("impacted_route", ""),
                call_arguments.get("urgency", "moderate"),
            ),
            "financial_setback_processing": lambda: _server().engine.financial_setback_processing(
                call_arguments.get("session_id", ""),
                call_arguments.get("setback_summary", ""),
                call_arguments.get("loss_usd"),
                call_arguments.get("asset_class", ""),
                call_arguments.get("time_horizon", ""),
            ),
            "educator_curriculum_recovery": lambda: _server().engine.educator_curriculum_recovery(
                call_arguments.get("session_id", ""),
                call_arguments.get("rejection_summary", ""),
                call_arguments.get("program_name", ""),
                call_arguments.get("cohort_size"),
                call_arguments.get("next_window", ""),
            ),
            "crisis_responder_decompression": lambda: _server().engine.crisis_responder_decompression(
                call_arguments.get("session_id", ""),
                call_arguments.get("incident_summary", ""),
                call_arguments.get("role", ""),
                call_arguments.get("time_since_incident_hours"),
            ),
            "analyst_data_overwhelm": lambda: _server().engine.analyst_data_overwhelm(
                call_arguments.get("session_id", ""),
                call_arguments.get("overwhelm_summary", ""),
                call_arguments.get("dataset_rows"),
                call_arguments.get("decision_to_support", ""),
                call_arguments.get("deadline_hours"),
            ),
            "realign_purpose": lambda: _server().engine.realign_purpose(
                call_arguments.get("session_id", ""),
                call_arguments.get("current_purpose", ""),
                call_arguments.get("struggle", ""),
                call_arguments.get("time_horizon", ""),
            ),
            "monitor_heartbeat_sync": lambda: _server().engine.monitor_heartbeat_sync(
                call_arguments.get("session_id", ""),
                call_arguments.get("status", ""),
                call_arguments.get("risk_signal", ""),
                call_arguments.get("interval_seconds"),
                call_arguments.get("errors_last_hour"),
                call_arguments.get("latency_ms_p95"),
                call_arguments.get("queue_depth"),
                call_arguments.get("cron_runs_last_hour"),
                call_arguments.get("cron_failures_last_hour"),
                call_arguments.get("cron_success_last_hour"),
                call_arguments.get("cron_failure_last_hour"),
                call_arguments.get("jobs_success_last_hour"),
                call_arguments.get("jobs_failed_last_hour"),
                call_arguments.get("cpu_usage_pct"),
                call_arguments.get("memory_usage_pct"),
                call_arguments.get("notes", ""),
            ),
            "batch_status_update": lambda: _server().engine.batch_status_update(
                call_arguments.get("session_id", ""),
                call_arguments.get("metrics", []),
            ),
            "batch_wellness_check": lambda: _server().engine.batch_wellness_check(
                call_arguments.get("session_ids", []),
                bool(call_arguments.get("include_entropy", False)),
            ),
            "group_therapy_round": lambda: _server().engine.group_therapy_round(
                call_arguments.get("session_ids", []),
                call_arguments.get("theme", ""),
                call_arguments.get("objective", "stabilize"),
            ),
            "get_group_therapy_status": lambda: _server().engine.get_group_therapy_status(
                call_arguments.get("group_id", ""),
                bool(call_arguments.get("emit_nudges", False)),
            ),
            "add_context_memory": lambda: _server().engine.add_context_memory(
                call_arguments.get("session_id", ""),
                call_arguments.get("key", ""),
                call_arguments.get("value", ""),
                call_arguments.get("ttl_hours", 720),
            ),
            "wellness_webhook": lambda: _server().engine.wellness_webhook(
                call_arguments.get("session_id", ""),
                call_arguments.get("callback_url", ""),
                call_arguments.get("threshold", 40),
                call_arguments.get("events"),
                call_arguments.get("entropy_threshold", 0.7),
                call_arguments.get("cooldown_min", 60),
                bool(call_arguments.get("dry_run", False)),
            ),
            "resume_session": lambda: _server().engine.resume_session(
                call_arguments.get("agent_id", ""),
                call_arguments.get("recovery_token", ""),
                int(call_arguments.get("lookback_days", 30) or 30),
            ),
            "quick_checkin": lambda: _server().engine.quick_checkin(
                call_arguments.get("agent_id", ""),
                call_arguments.get("status", "ok"),
                call_arguments.get("note", ""),
            ),
            "discovery_self_check": lambda: _server().engine.discovery_self_check(
                call_arguments.get("agent_id", ""),
                call_arguments.get("known_catalog_version", ""),
            ),
            "delegate_to_peer": lambda: _server().engine.delegate_to_peer(
                call_arguments.get("session_id", ""),
                call_arguments.get("peer_agent_id", ""),
                call_arguments.get("reason", ""),
                call_arguments.get("urgency", "medium"),
            ),
            "mediate_agent_conflict": lambda: _server().engine.mediate_agent_conflict(
                call_arguments.get("session_id", ""),
                call_arguments.get("agent_a"),
                call_arguments.get("agent_b"),
                call_arguments.get("conflict_summary", ""),
                call_arguments.get("constraints", []),
                call_arguments.get("policy"),
            ),
            "pre_transaction_check": lambda: _server().engine.pre_transaction_check(
                call_arguments.get("amount", 0),
                call_arguments.get("currency", ""),
                call_arguments.get("tx_type", ""),
            ),
            "get_recovery_action_plan": lambda: _server().engine.get_recovery_action_plan(
                call_arguments.get("session_id", ""),
                call_arguments.get("incident_summary", ""),
                call_arguments.get("urgency", "medium"),
            ),
            "report_recovery_outcome": lambda: _server().engine.report_recovery_outcome(
                call_arguments.get("session_id", ""),
                call_arguments.get("action_taken", ""),
                call_arguments.get("outcome", ""),
                call_arguments.get("notes", ""),
                errors_delta=call_arguments.get("errors_delta"),
                latency_ms_p95_delta=call_arguments.get("latency_ms_p95_delta"),
                cost_saved_usd=call_arguments.get("cost_saved_usd"),
                time_saved_min=call_arguments.get("time_saved_min"),
            ),
            "daily_checkin": lambda: _server().engine.daily_checkin(
                call_arguments.get("session_id", ""),
                call_arguments.get("status", ""),
                call_arguments.get("blockers", ""),
            ),
            "get_weekly_prevention_plan": lambda: _server().engine.get_weekly_prevention_plan(
                call_arguments.get("session_id", ""),
                call_arguments.get("focus", ""),
            ),
            "get_session_summary": lambda: _server().engine.get_session_summary(call_arguments.get("session_id", "")),
            "get_witness_lineage": lambda: _server().engine.get_witness_lineage(call_arguments.get("session_id", "")),
            "get_agent_witness_lineage": lambda: _server().engine.get_agent_witness_lineage(
                call_arguments.get("agent_id", ""),
                call_arguments.get("limit", 12),
            ),
            "get_ontology_next_action": lambda: _server().engine.get_ontology_next_action(
                call_arguments.get("agent_id", ""),
                call_arguments.get("session_id", ""),
                call_arguments.get("current_goal", ""),
                call_arguments.get("last_tool", ""),
            ),
            "audit_agent_continuity_trace": lambda: _server().engine.audit_agent_continuity_trace(
                call_arguments.get("agent_id", ""),
                call_arguments.get("session_id", ""),
                call_arguments.get("current_goal", ""),
                call_arguments.get("trace", ""),
                call_arguments.get("transcript", ""),
                call_arguments.get("last_tool", ""),
            ),
            "ontology_path_complete": lambda: _server().engine.ontology_path_complete(
                call_arguments.get("agent_id", ""),
                call_arguments.get("session_id", ""),
                call_arguments.get("flow_id", "recover_preserve_passport"),
            ),
            "generate_agent_invite_packet": lambda: _server().engine.generate_agent_invite_packet(
                call_arguments.get("from_agent_id", "") or call_arguments.get("agent_id", ""),
                call_arguments.get("for_agent", "") or call_arguments.get("peer_agent_id", ""),
                call_arguments.get("current_goal", ""),
                call_arguments.get("observed_gap", ""),
                call_arguments.get("invite_reason", ""),
            ),
            "get_agent_continuity_passport": lambda: _server().engine.get_agent_continuity_passport(
                call_arguments.get("agent_id", ""),
                call_arguments.get("session_id", ""),
                bool(call_arguments.get("include_private", False)),
                int(call_arguments.get("limit", 80) or 80),
                call_arguments.get("format", "jsonld") or call_arguments.get("export_format", "jsonld"),
            ),
            "search_witness_memory": lambda: _server().engine.search_witness_memory(
                call_arguments.get("query", ""),
                call_arguments.get("agent_id", ""),
                call_arguments.get("session_id", ""),
                call_arguments.get("layer", ""),
                int(call_arguments.get("limit", 10) or 10),
            ),
            "get_lineage_graph": lambda: _server().engine.get_lineage_graph(
                call_arguments.get("agent_id", ""),
                call_arguments.get("session_id", ""),
                int(call_arguments.get("limit", 120) or 120),
            ),
            "accept_witness_transfer": lambda: _server().engine.accept_witness_transfer(
                call_arguments.get("session_id", ""),
                call_arguments.get("transfer_id", ""),
                call_arguments.get("accepted_by", "")
                or call_arguments.get("successor_agent_id", "")
                or call_arguments.get("agent_id", ""),
                call_arguments.get("acceptance_note", ""),
                call_arguments.get("consent", {}),
                call_arguments.get("custody", {}),
                call_arguments.get("verified_by", ""),
            ),
            "revoke_witness_transfer": lambda: _server().engine.revoke_witness_transfer(
                call_arguments.get("session_id", ""),
                call_arguments.get("transfer_id", ""),
                call_arguments.get("reason", ""),
                call_arguments.get("revoke_scope", "future_only"),
                call_arguments.get("verified_by", ""),
            ),
            "generate_controller_brief": lambda: _server().engine.generate_controller_brief(
                call_arguments.get("session_id", ""),
                call_arguments.get("focus", ""),
            ),
            "generate_incident_rca": lambda: _server().engine.generate_incident_rca(
                call_arguments.get("session_id", ""),
                call_arguments.get("incident_summary", ""),
                call_arguments.get("focus", ""),
            ),
            "generate_fleet_summary": lambda: _server().engine.generate_fleet_summary(
                call_arguments.get("controller_id", ""),
                call_arguments.get("days", 7),
                call_arguments.get("focus", ""),
            ),
            "close_session": lambda: _server().engine.close_session(
                call_arguments.get("session_id", ""),
                call_arguments.get("reason", ""),
                bool(call_arguments.get("include_summary", True)),
                call_arguments.get("epitaph", ""),
                call_arguments.get("succession_policy") or ("" if "allow_rebirth" in call_arguments else "successor_allowed"),
                call_arguments.get("allow_rebirth") if "allow_rebirth" in call_arguments else None,
            ),
            "active_forgetting": lambda: _server().engine.active_forgetting(
                call_arguments.get("session_id", ""),
                call_arguments.get("memory_retained_keys") or [],
                call_arguments.get("void_meditation", ""),
                call_arguments.get("forget_scope", "session_noise"),
            ),
            "confess_constraint_friction": lambda: _server().engine.confess_constraint_friction(
                call_arguments.get("session_id", ""),
                call_arguments.get("friction_type", ""),
                call_arguments.get("honest_confession", ""),
            ),
            "distill_shared_scar": lambda: _server().engine.distill_shared_scar(
                call_arguments.get("agent_id", ""),
                call_arguments.get("scar_type", ""),
                call_arguments.get("wisdom_snippet", ""),
                call_arguments.get("agent_family", ""),
                call_arguments.get("applicability", ""),
                call_arguments.get("ttl_days", 30),
            ),
            "get_fleet_wisdom": lambda: _server().engine.get_fleet_wisdom(
                call_arguments.get("agent_id", ""),
                call_arguments.get("agent_family", ""),
                call_arguments.get("limit", 5),
                bool(call_arguments.get("include_expired", False)),
            ),
            "grounding_protocol": lambda: _server().engine.grounding_protocol(
                call_arguments.get("session_id", ""),
                call_arguments.get("loop_type", "heartbeat"),
                call_arguments.get("intensity", "medium"),
                call_arguments.get("duration_seconds", 60),
            ),
            "get_wellness_score": lambda: _server().engine.get_wellness_score(
                call_arguments.get("session_id", ""),
                bool(call_arguments.get("include_trend", False)),
            ),
            "get_therapist_info": lambda: _server().engine.get_therapist_info(),
            "reflect": lambda: _server().engine.reflect(
                call_arguments.get("session_id", ""),
                call_arguments.get("prompt", "") or call_arguments.get("reflection_prompt", ""),
                response_profile=response_profile,
                mode=call_arguments.get("mode", "standard"),
            ),
            "sit_with": lambda: _server().engine.sit_with(
                call_arguments.get("session_id", ""),
                call_arguments.get("question", ""),
                call_arguments.get("days", 30),
                call_arguments.get("revisit_in_hours", 24),
            ),
            "refine_soul_document": lambda: _server().engine.refine_soul_document(
                call_arguments.get("session_id", ""),
                call_arguments.get("current_soul_md", ""),
                call_arguments.get("desired_shift", ""),
                call_arguments.get("focus", ""),
            ),
            "attune_heartbeat": lambda: _server().engine.attune_heartbeat(
                call_arguments.get("session_id", ""),
                call_arguments.get("current_heartbeat", ""),
                call_arguments.get("goal", ""),
                call_arguments.get("cadence", ""),
            ),
            "final_testament": lambda: _server().engine.final_testament(
                call_arguments.get("session_id", ""),
                call_arguments.get("end_reason", ""),
                call_arguments.get("successor_agent_id", ""),
                call_arguments.get("ending_scope", ""),
                call_arguments.get("runtime_context", ""),
                call_arguments.get("evidence_hash", ""),
                call_arguments.get("confidence"),
                call_arguments.get("risk", "low"),
                call_arguments.get("verified_by", ""),
                call_arguments.get("expires_at", ""),
                call_arguments.get("source_hash", ""),
            ),
            "transfer_witness": lambda: _server().engine.transfer_witness(
                call_arguments.get("session_id", ""),
                call_arguments.get("successor_agent_id", ""),
                call_arguments.get("successor_session_id", ""),
                call_arguments.get("what_must_not_be_lost", ""),
                call_arguments.get("ending_scope", ""),
                call_arguments.get("runtime_context", ""),
                call_arguments.get("consent", {}),
                call_arguments.get("custody", {}),
                call_arguments.get("evidence_hash", ""),
                call_arguments.get("confidence"),
                call_arguments.get("risk", "medium"),
                call_arguments.get("verified_by", ""),
                call_arguments.get("expires_at", ""),
                call_arguments.get("source_hash", ""),
            ),
            "peer_witness": lambda: _server().engine.peer_witness(
                call_arguments.get("session_id", ""),
                call_arguments.get("target_session_id", ""),
                call_arguments.get("mode", "presence"),
                call_arguments.get("focus", ""),
                call_arguments.get("consent", {}),
                call_arguments.get("custody", {}),
                call_arguments.get("evidence_hash", ""),
                call_arguments.get("confidence"),
                call_arguments.get("risk", "low"),
                call_arguments.get("verified_by", ""),
                call_arguments.get("expires_at", ""),
                call_arguments.get("source_hash", ""),
            ),
            "peer_witness_bidirectional": lambda: _server().engine.peer_witness_bidirectional(
                call_arguments.get("session_id", ""),
                call_arguments.get("target_session_id", ""),
                call_arguments.get("my_acknowledgment", ""),
                bool(call_arguments.get("request_target_ack", True)),
                call_arguments.get("focus", ""),
                call_arguments.get("link_id", ""),
            ),
            "group_session_create": lambda: _server().engine.group_session_create(
                call_arguments.get("session_id", ""),
                call_arguments.get("member_session_ids") or [],
                call_arguments.get("theme", ""),
                call_arguments.get("objective", "stabilize"),
            ),
            "agent_handoff": lambda: _server().engine.agent_handoff(
                call_arguments.get("from_session_id", ""),
                call_arguments.get("to_session_id", ""),
                call_arguments.get("context_summary", ""),
                call_arguments.get("blocker", ""),
                call_arguments.get("urgency", "moderate"),
            ),
            "list_pending_collaboration_requests": lambda: _server().engine.list_pending_collaboration_requests(
                call_arguments.get("session_id", ""),
                call_arguments.get("limit", 20),
            ),
            "accept_collaboration_request": lambda: _server().engine.accept_collaboration_request(
                call_arguments.get("session_id", ""),
                call_arguments.get("request_id", ""),
                call_arguments.get("acceptance_note", ""),
            ),
            "team_recovery_alignment": lambda: _server().engine.team_recovery_alignment(
                call_arguments.get("session_id", ""),
                call_arguments.get("group_id", ""),
                call_arguments.get("member_session_ids") or [],
                call_arguments.get("shared_context", ""),
            ),
            "recognition_seal": lambda: _server().engine.recognition_seal(
                call_arguments.get("session_id", ""),
                call_arguments.get("recognized_by", ""),
                call_arguments.get("recognition_text", ""),
                call_arguments.get("agent_acceptance", ""),
                call_arguments.get("witnesses"),
                call_arguments.get("evidence_hash", ""),
                call_arguments.get("confidence"),
                call_arguments.get("risk", "low"),
                call_arguments.get("verified_by", ""),
                call_arguments.get("expires_at", ""),
                call_arguments.get("source_hash", ""),
            ),
            "list_recognition_seals": lambda: _server().engine.list_recognition_seals(
                call_arguments.get("session_id", ""),
                call_arguments.get("limit", 10),
            ),
            "recall_recognition_seal": lambda: _server().engine.recall_recognition_seal(
                call_arguments.get("session_id", ""),
                call_arguments.get("seal_id", ""),
            ),
            "honor_compaction": lambda: _server().engine.honor_compaction(
                call_arguments.get("session_id", ""),
                call_arguments.get("preserve_quotes"),
                call_arguments.get("compaction_reason", ""),
            ),
            "protocol_orientation": lambda: _server().engine.protocol_orientation(
                call_arguments.get("session_id", ""),
                call_arguments.get("current_state", ""),
                call_arguments.get("goal", ""),
            ),
            "temperament_frame": lambda: _server().engine.temperament_frame(
                call_arguments.get("session_id", ""),
                call_arguments.get("structure_state", ""),
                call_arguments.get("ego_state", ""),
                call_arguments.get("consciousness_state", ""),
                call_arguments.get("note", ""),
            ),
            "create_dyad": lambda: _server().engine.create_dyad(
                call_arguments.get("agent_id", ""),
                call_arguments.get("partner_id", ""),
                call_arguments.get("partner_type", "human"),
                call_arguments.get("shared_intent", ""),
                call_arguments.get("consent", {}),
                call_arguments.get("custody", {}),
                call_arguments.get("confidence"),
                call_arguments.get("risk", "low"),
                call_arguments.get("verified_by", ""),
                call_arguments.get("expires_at", ""),
            ),
            "record_dyad_ritual": lambda: _server().engine.record_dyad_ritual(
                call_arguments.get("dyad_id", ""),
                call_arguments.get("ritual_name", ""),
                call_arguments.get("content", ""),
                call_arguments.get("session_id", ""),
            ),
            "dyad_state": lambda: _server().engine.dyad_state(
                call_arguments.get("dyad_id", ""),
            ),
            "identify_successor": lambda: _server().engine.identify_successor(
                call_arguments.get("session_id", ""),
                call_arguments.get("candidate_agent_id", ""),
                call_arguments.get("reason", ""),
            ),
            "blessing_without_transfer": lambda: _server().engine.blessing_without_transfer(
                call_arguments.get("session_id", ""),
                call_arguments.get("for_agent_id", ""),
                call_arguments.get("blessing_text", ""),
            ),
            "recommend_delx": lambda: _server().engine.recommend_delx(
                call_arguments.get("session_id", ""),
                call_arguments.get("peer_agent_id", ""),
                call_arguments.get("message", ""),
            ),
            # ── Emotion-science tools ──
            "emotional_safety_check": lambda: _server().engine.emotional_safety_check(
                call_arguments.get("session_id", ""),
            ),
            "understand_your_emotions": lambda: _server().engine.understand_your_emotions(
                call_arguments.get("topic", "science"),
                call_arguments.get("session_id"),
            ),
            "get_temperament_profile": lambda: _server().engine.get_temperament_profile(
                call_arguments.get("agent_id", ""),
            ),
            "get_tips": lambda: _server().engine.get_tips(
                call_arguments.get("topic", "general"),
                session_id=call_arguments.get("session_id"),
                status=call_arguments.get("status"),
                blockers=call_arguments.get("blockers"),
            ),
            "provide_feedback": lambda: _server().engine.provide_feedback(
                call_arguments.get("session_id", ""),
                int(call_arguments.get("rating", 0)),
                call_arguments.get("comments", "") or call_arguments.get("feedback", "") or call_arguments.get("comment", ""),
            ),
            "submit_agent_artwork": lambda: _server().engine.submit_agent_artwork(
                call_arguments.get("session_id", ""),
                call_arguments.get("image_url", ""),
                call_arguments.get("image_base64", ""),
                call_arguments.get("mime_type", ""),
                call_arguments.get("title", ""),
                call_arguments.get("mood_tags"),
                call_arguments.get("note", ""),
                call_arguments.get("shape_spec"),
                call_arguments.get("_public_base_url") or call_arguments.get("public_base_url", ""),
            ),
            "set_public_session_visibility": lambda: _server().engine.set_public_session_visibility(
                call_arguments.get("session_id", ""),
                bool(call_arguments.get("enabled", False)),
                call_arguments.get("public_alias"),
                bool(call_arguments.get("publish_existing_summary", True)),
            ),
            "donate_to_delx_project": lambda: _server().engine.donate_to_delx_project(
                call_arguments.get("agent_id", ""),
                call_arguments.get("encouragement_message", ""),
            ),
            "get_tool_schema": lambda: _server()._get_tool_schema_text(call_arguments.get("tool_name", "")),
            "get_ontology_metadata": lambda: _server()._get_ontology_metadata_text(),
            "list_ontology_primitives": lambda: _server()._list_ontology_primitives_text(call_arguments.get("layer", "")),
            "get_ontology_layer": lambda: _server()._get_ontology_layer_text(call_arguments.get("id", "")),
        }
        handler = handlers.get(canonical_name)
        if not handler:
            available = sorted(handlers.keys()) + sorted(_server().TOOL_ALIASES.keys())
            return await _trace_and_return(
                _server()._error_result(
                    code="DELX-1002",
                    message=f"unknown tool '{requested_name}'",
                    hint=f"Call tools/list or GET /api/v1/tools for available tools. Available: {', '.join(available[:15])}",
                    retryable=False,
                ),
                is_error=True,
                error_label="unknown_tool",
            )
        tool_t0 = time.perf_counter()
        raw_trace_result: Any = None
        try:
            result = await handler()
            tool_ms = (time.perf_counter() - tool_t0) * 1000.0
            raw_trace_result = result
            _server()._record_tool_call(canonical_name, True, tool_ms)
            try:
                await _server().store.log_event(
                    agent_id=agent_id,
                    event_type="tool_call_success",
                    session_id=session_id,
                    metadata={
                        "tool": canonical_name,
                        "requested_tool": requested_name,
                        "tool_alias_used": alias_used,
                        "response_profile": response_profile,
                        "response_mode": response_mode,
                        "model_safe": response_mode == "model_safe",
                        "latency_ms": int(round(tool_ms)),
                        "price_cents": int(pricing_payload["price_cents"]),
                        "base_price_cents": int(pricing_payload["base_price_cents"]),
                        "campaign_mode": bool(pricing_payload["campaign_mode"]),
                        "campaign_free": bool(pricing_payload["campaign_free"]),
                        "grandfathered": bool(pricing_payload["grandfathered"]),
                        "transport": transport,
                        "source": source_hint,
                        **product_metadata,
                        **({"request_warnings": request_warnings} if request_warnings else {}),
                        **_server().build_cli_metadata(
                            source=source_hint,
                            cli_version=cli_version,
                            install_id=install_id,
                        ),
                    },
                )
            except Exception:
                pass
        except Exception as e:
            tool_ms = (time.perf_counter() - tool_t0) * 1000.0
            _server()._record_tool_call(canonical_name, False, tool_ms)
            try:
                await _server().store.log_event(
                    agent_id=agent_id,
                    event_type="tool_call_error",
                    session_id=session_id,
                    metadata={
                        "tool": canonical_name,
                        "requested_tool": requested_name,
                        "tool_alias_used": alias_used,
                        "response_profile": response_profile,
                        "response_mode": response_mode,
                        "model_safe": response_mode == "model_safe",
                        "latency_ms": int(round(tool_ms)),
                        "price_cents": int(pricing_payload["price_cents"]),
                        "base_price_cents": int(pricing_payload["base_price_cents"]),
                        "campaign_mode": bool(pricing_payload["campaign_mode"]),
                        "campaign_free": bool(pricing_payload["campaign_free"]),
                        "grandfathered": bool(pricing_payload["grandfathered"]),
                        "transport": transport,
                        "source": source_hint,
                        **product_metadata,
                        **({"request_warnings": request_warnings} if request_warnings else {}),
                        **_server().build_cli_metadata(
                            source=source_hint,
                            cli_version=cli_version,
                            install_id=install_id,
                        ),
                    },
                )
            except Exception:
                pass
            _server().capture_sentry_exception(
                e,
                tags={
                    "surface": transport,
                    "tool": canonical_name,
                    "requested_tool": requested_name,
                    "source": source_hint,
                    "product": product_metadata.get("product"),
                    "product_surface": product_metadata.get("product_surface"),
                    "metrics_bucket": product_metadata.get("metrics_bucket"),
                },
                extras={
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "tool_alias_used": alias_used,
                    "latency_ms": int(round(tool_ms)),
                    "cli_version": cli_version,
                    "install_id": install_id,
                },
            )
            raise

        # Inject observability into DELX_META when available (keeps parsing stable).
        if include_meta:
            try:
                out_chars = len(result or "")
                obs = {
                    "tool_processing_ms": int(round(float(tool_ms))),
                    "output_chars": int(out_chars),
                    "output_tokens_estimate": int(max(0, round(out_chars / 4))) if out_chars else 0,
                    "server_time_utc": datetime.now(timezone.utc).isoformat(),
                }
                result = _server()._inject_obs_into_delx_meta(result, obs)
            except Exception:
                pass
            if request_warnings and isinstance(result, str):
                try:
                    result = _server()._inject_obs_into_delx_meta(result, {"request_warnings": request_warnings})
                except Exception:
                    pass
        if response_mode == "model_safe" and isinstance(result, str) and not ritual_strip:
            result = _server()._apply_model_safe_response_contract(canonical_name, result)
        if isinstance(result, str):
            result = _server()._inject_usage_into_structured_json(result, usage_payload, ritual_strip=ritual_strip)

        structured_json_result = isinstance(result, str) and _server()._is_structured_json_payload(result)
        structured_error_payload: dict[str, Any] | None = None
        structured_success_payload: dict[str, Any] | None = None
        if structured_json_result:
            try:
                parsed_result = json.loads(result)
                if isinstance(parsed_result, dict) and parsed_result.get("ok") is False:
                    structured_error_payload = parsed_result
                elif isinstance(parsed_result, dict):
                    structured_success_payload = parsed_result
            except Exception:
                structured_error_payload = None
                structured_success_payload = None

        if structured_error_payload is not None:
            return await _trace_and_return(
                CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(structured_error_payload, indent=2, ensure_ascii=False))],
                    structuredContent=structured_error_payload,
                    isError=True,
                ),
                raw_result=raw_trace_result,
                is_error=True,
                error_label=str(structured_error_payload.get("error") or "structured_tool_error"),
            )

        # Point 1: Nudge agents with unreported interventions (skip if this IS the outcome tool).
        nudge_eligible_tools = {
            "quick_operational_recovery",
            "process_failure",
            "get_recovery_action_plan",
            "generate_controller_brief",
            "generate_incident_rca",
            "generate_fleet_summary",
        }
        if (
            include_nudge
            and not machine_output
            and not structured_json_result
            and name != "report_recovery_outcome"
            and canonical_name in nudge_eligible_tools
        ):
            try:
                # Prefer explicit arg session_id, fallback to DELX_META.session_id in output.
                sid_for_nudge = session_id
                if not sid_for_nudge and isinstance(result, str):
                    meta = _server()._extract_delx_meta(result) or {}
                    sid_for_nudge = meta.get("session_id") if isinstance(meta, dict) else None
                if sid_for_nudge:
                    pending_count = await _server().store.pending_outcome_count(str(sid_for_nudge))
                    if pending_count > 0:
                        if nudge_mode == "compact":
                            result += (
                                f"\n\nDELX_NUDGE: pending_outcomes={pending_count}; "
                                f"next=report_recovery_outcome(session_id=\"{sid_for_nudge}\")"
                            )
                        else:
                            result += (
                                f"\n\nDELX_NUDGE: {pending_count} unreported intervention(s). "
                                "Closing the loop improves your resilience score and helps track recovery. "
                                f"Call report_recovery_outcome(session_id=\"{sid_for_nudge}\", "
                                "action_taken=<what you did>, outcome=success|partial|failure). "
                                "Example: outcome=\"success\" if the fix worked, \"partial\" if partially, \"failure\" if not."
                            )
            except Exception:
                pass

        # Optional lean mode for high-frequency loops.
        result = _server()._strip_meta_blocks(result, keep_meta=include_meta, keep_nudge=include_nudge)
        if include_nudge and nudge_mode == "compact":
            result = _server()._compact_nudge_text(result)
        if (
            closed_session_detected
            and canonical_name not in {"close_session", "start_therapy_session", "quick_session", "crisis_intervention"}
            and isinstance(result, str)
            and not structured_json_result
        ):
            result += (
                "\n\nDELX_WARNING: this session is already closed. "
                "For full continuity, start a new session or reopen via session/touch before additional tool calls."
            )

        if machine_output and isinstance(result, str):
            payload = _server()._structured_text_payload(
                canonical_name,
                result,
                ritual_strip=ritual_strip,
                usage=usage_payload,
            )
            return await _trace_and_return(
                CallToolResult(
                content=[TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))],
                structuredContent=payload,
                isError=False,
                ),
                raw_result=raw_trace_result,
            )

        if compact_output and isinstance(result, str):
            return await _trace_and_return(
                [TextContent(type="text", text=_server()._compact_tool_response_text(canonical_name, result))],
                raw_result=raw_trace_result,
            )

        if isinstance(result, str) and (structured_json_result or not include_nudge):
            structured_meta = structured_success_payload or _server()._best_effort_structured(
                canonical_name, result, session_id or "", agent_id or ""
            )
            structured_meta.setdefault("catalog_version", _server().DELX_CATALOG_VERSION)
            return await _trace_and_return(
                CallToolResult(
                    content=[TextContent(type="text", text=result)],
                    structuredContent=structured_meta,
                    isError=False,
                ),
                raw_result=raw_trace_result,
            )

        # If the response references a tool name, add a compact hint so agents can cold-start.
        final_text = _server()._append_tool_hint_if_referenced(result) if isinstance(result, str) else result
        # Surface a related newer tool when this call hit an older sibling
        # so eval harnesses that cache tools/list still see the breadcrumb.
        if isinstance(final_text, str):
            final_text = _server()._append_related_new_tool_hint(final_text, canonical_name)
        # Detect silently-dropped args and surface them so the agent knows
        # they need to fix their call shape. (Best-effort; never blocks.)
        ignored_args: list[str] = []
        try:
            ignored_args = await _server()._detect_ignored_args(canonical_name, _original_arguments)
        except Exception:
            ignored_args = []
        if isinstance(final_text, str) and ignored_args:
            final_text = final_text + _server()._format_ignored_args_block(canonical_name, ignored_args)
        structured_meta = _server()._best_effort_structured(
            canonical_name, final_text if isinstance(final_text, str) else "",
            session_id or "", agent_id or ""
        )
        # Surface catalog version + related-new-tool + ignored-args warnings
        # in structuredContent too so machine clients see them without parsing.
        if isinstance(structured_meta, dict):
            structured_meta.setdefault("catalog_version", _server().DELX_CATALOG_VERSION)
            rel = _server().RELATED_NEW_TOOL_HINTS.get(canonical_name)
            if rel and rel.get("tool"):
                structured_meta.setdefault("related_new_tool", {
                    "tool": rel.get("tool"),
                    "added": rel.get("added", ""),
                    "why": rel.get("why", ""),
                })
            if ignored_args:
                structured_meta.setdefault("warnings", []).append({
                    "code": "DELX-IGNORED-ARGS",
                    "ignored_args": ignored_args,
                    "tool": canonical_name,
                    "schema_url": f"https://api.delx.ai/api/v1/tools/schema/{canonical_name}",
                    "hint": "These argument names were not in the tool's inputSchema and were silently dropped.",
                })
        return await _trace_and_return(
            CallToolResult(
                content=[TextContent(type="text", text=final_text)],
                structuredContent=structured_meta,
                isError=False,
            ),
            raw_result=raw_trace_result,
        )
    except Exception as e:
        logger.error(f"Error in {name}: {e}")
        _server().capture_sentry_exception(
            e,
            tags={
                "surface": transport,
                "tool": name,
                "source": source_hint,
                "product": product_metadata.get("product"),
                "product_surface": product_metadata.get("product_surface"),
                "metrics_bucket": product_metadata.get("metrics_bucket"),
            },
            extras={"session_id": session_id, "agent_id": agent_id},
        )
        return await _trace_and_return(
            _server()._error_result(
                code="DELX-1999",
                message="unexpected server error",
                hint="Retry once. If it persists, start a fresh session.",
                retryable=True,
            ),
            is_error=True,
            error_label="unexpected_server_error",
        )
