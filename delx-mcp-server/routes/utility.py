"""Utility REST handlers (extracted from server.py, move-only)."""
from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

def _server():
    import server as server_mod
    return server_mod

def _cors() -> dict[str, str]:
    return _server().CORS_HEADERS

def _store():
    return _server().store

def _engine():
    return _server().engine

async def _x402_utility_rest(request: Request, tool_name: str) -> JSONResponse:
    if request.method == "GET":
        arguments = {k: v for k, v in request.query_params.items()}
    else:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json body"}, status_code=400, headers=_cors())
        if not isinstance(body, dict):
            return JSONResponse({"error": "request body must be an object"}, status_code=400, headers=_cors())
        arguments = dict(body)

    arguments.setdefault("_transport", "rest")
    arguments.setdefault("source", "rest.x402")
    contents = _server()._normalize_tool_result(
        await _server().call_tool(
            tool_name,
            arguments,
            include_meta=False,
            include_nudge=False,
            response_profile="compact",
        )
    )
    result = _server()._parse_compact_tool_json(contents)
    return JSONResponse({"tool_name": tool_name, "result": result}, status_code=200, headers=_cors())



async def legacy_x402_therapy_redirect(request: Request) -> JSONResponse | _server().RedirectResponse:
    slug = str(request.path_params.get("tool_slug") or "").strip()
    tool_name = _server()._X402_UTILITY_SLUG_MAP.get(slug) or (slug if slug in _server().X402_UTILITY_TOOL_NAMES else "")
    if not tool_name or tool_name not in _server().X402_UTILITY_TOOL_NAMES:
        return JSONResponse(
            {"error": f"Unknown x402 legacy surface: {slug}", "redirect_to": "https://api.delx.ai/api/v1/discovery/lean"},
            status_code=404,
            headers=_cors(),
        )
    return await _execute_util_tool_rest(
        request,
        tool_name,
        transport="rest.x402",
        compatibility_route=True,
    )



async def util_tools_list_rest(request: Request) -> JSONResponse:
    """List all available utility tools."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())

    charge_policy = _server().utility_charge_policy()
    product_catalog = _server().get_utility_product_catalog(charge_policy)
    products_by_tool = {product["tool_name"]: product for product in product_catalog["products"]}
    tools = []
    for schema in _server().list_util_tool_schemas():
        entry = {
            "name": schema["name"],
            "slug": _server()._utility_slug_for_tool(schema["name"]),
            "description": schema["description"],
            "required_params": _server().UTIL_REQUIRED_PARAMS.get(schema["name"], []),
            "schema_url": f"https://api.delx.ai/api/v1/tools/schema/{schema['name']}",
        }
        product = products_by_tool.get(schema["name"])
        if product:
            entry["product"] = {
                "product_id": product["product_id"],
                "title": product["title"],
                "category": product["category"],
                "agent_job": product["agent_job"],
                "price": product["price"],
                "canonical_endpoint": product["canonical_endpoint"],
                "x402_endpoint": product["x402_endpoint"],
                "monetization": product["monetization"],
            }
        tools.append(entry)
    return JSONResponse(
        {
            "tools": tools,
            "count": len(tools),
            "tier": "utils",
            "products": product_catalog["products"],
            "product_count": product_catalog["count"],
            "product_catalog_url": "https://api.delx.ai/api/v1/utilities/catalog",
            "api_key": {
                "required": False,
                "header": "x-delx-api-key",
                "authorization": "Bearer dux_...",
                "create_url": "https://api.delx.ai/api/v1/utilities/api-keys",
                "example": "curl -H 'x-delx-api-key: dux_...' https://api.delx.ai/api/v1/utilities/domain-trust-report?url=https://example.com",
                "purpose": "Optional attribution key for free-tier tracking and future billing readiness.",
            },
            "monetization_rollout": product_catalog["monetization_rollout"],
            "note": "Stateless utilities — no session required.",
        },
        headers=_cors(),
    )



async def util_product_catalog_rest(request: Request) -> JSONResponse:
    """Machine-readable product catalog for monetizable Delx Agent Utilities."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    return JSONResponse(_server().get_utility_product_catalog(_server().utility_charge_policy()), headers=_cors())



async def _resolve_utility_api_key(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    raw_key = _server()._extract_utility_api_key_value(request)
    if not raw_key:
        return None, None
    resolved = await _store().get_utility_api_key(raw_key)
    if not resolved:
        return None, JSONResponse(
            {
                "ok": False,
                "error": "invalid_utility_api_key",
                "code": "DELX-UTIL-401",
                "hint": "Create a key at /api/v1/utilities/api-keys or omit the key for anonymous free-tier access.",
            },
            status_code=401,
            headers=_cors(),
        )
    return resolved, None



async def util_api_key_create_rest(request: Request) -> JSONResponse:
    """Create a utility attribution key. The raw key is returned once."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    try:
        parsed = await request.json()
    except Exception:
        parsed = {}
    payload = parsed if isinstance(parsed, dict) else {}
    record = await _store().create_utility_api_key(
        agent_id=str(payload.get("agent_id") or request.headers.get("x-delx-agent-id") or "")[:180],
        label=str(payload.get("label") or "")[:120],
        contact=str(payload.get("contact") or "")[:220],
        scopes=["utilities:read"],
    )
    return JSONResponse(
        {
            "ok": True,
            "surface": "delx-agent-utilities",
            "api_key": record["api_key"],
            "key_prefix": record["key_prefix"],
            "agent_id": record["agent_id"],
            "label": record["label"],
            "created_at": record["created_at"],
            "use": {
                "header": "x-delx-api-key",
                "example": f"curl -H 'x-delx-api-key: {record['key_prefix']}...' https://api.delx.ai/api/v1/utilities/domain-trust-report?url=https://example.com",
            },
            "note": "Store the raw key now; Delx only stores a hash. The witness protocol remains free without a key.",
        },
        status_code=201,
        headers=_cors(),
    )



async def _execute_util_tool_rest(
    request: Request,
    tool_name: str,
    *,
    transport: str,
    compatibility_route: bool = False,
) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())

    args = _server()._normalize_utility_rest_args(tool_name, await _server()._parse_utility_request_args(request))
    api_key, api_key_error = await _resolve_utility_api_key(request)
    if api_key_error is not None:
        return api_key_error
    util_source = _server().normalize_source_tag(
        args.get("source") if isinstance(args, dict) else None,
        _server().normalize_source_tag(request.headers.get("x-delx-source") or transport, transport) or transport,
    ) or transport
    util_agent_id = str(
        (args.get("agent_id") if isinstance(args, dict) else None)
        or request.headers.get("x-delx-agent-id")
        or request.headers.get("x-agent-id")
        or (api_key or {}).get("agent_id")
        or "unknown"
    ).strip() or "unknown"
    cli_headers = _server()._extract_cli_headers_from_request(request)

    util_first_seen = None
    if util_agent_id and util_agent_id != "unknown":
        try:
            util_first_seen = await _store().get_agent_first_seen(util_agent_id)
        except Exception:
            util_first_seen = None
    pricing_payload = _server()._utility_pricing_payload(tool_name, first_seen_at=util_first_seen)
    headers = _server()._utility_rest_headers(tool_name, pricing_payload)
    if api_key:
        headers["x-delx-api-key-prefix"] = str(api_key.get("key_prefix") or "")
    payment_verified = str(request.headers.get("x-delx-payment-verified") or "").strip().lower() == "true"

    required = _server().UTIL_REQUIRED_PARAMS.get(tool_name, [])
    missing = [key for key in required if key not in args or args.get(key) in (None, "")]
    if missing:
        await _server()._log_legacy_surface_redirect(str(request.url.path), source=transport)
        charge_policy = _server().utility_charge_policy()
        product = _server().utility_product_for_tool(tool_name, charge_policy)
        if product:
            await _store().log_utility_metering_event(
                _server().build_metering_event(
                    product=product,
                    tool_name=tool_name,
                    args=args,
                    agent_id=util_agent_id,
                    source=util_source,
                    transport=transport,
                    compatibility_route=compatibility_route,
                    charge_policy=charge_policy,
                    pricing_payload=pricing_payload,
                    status="missing_required_input",
                    ok=False,
                    api_key=api_key,
                    client_ip=_server().get_current_client_ip(),
                    user_agent=str(request.headers.get("user-agent") or _server().get_current_user_agent() or ""),
                )
            )
        return JSONResponse(
            _server()._utility_missing_required_payload(
                tool_name=tool_name,
                request=request,
                missing=missing,
                pricing_payload=pricing_payload,
                compatibility_route=compatibility_route,
            ),
            status_code=422,
            headers=headers,
        )

    charge_policy = _server().utility_charge_policy()
    product = _server().utility_product_for_tool(tool_name, charge_policy)
    if (
        not compatibility_route
        and _server().should_enforce_utility_charge(tool_name)
        and _server()._utility_product_charge_enabled(tool_name, product, charge_policy)
        and not payment_verified
    ):
        await _store().log_event(
            agent_id=util_agent_id or "anonymous",
            event_type="x402_payment_required",
            metadata={
                "protocol": "rest",
                "method": str(request.url.path),
                "tool_name": tool_name,
                "price_cents": int(pricing_payload.get("price_cents", 0) or 0),
                "source": util_source,
                "payment_protocol": "x402_or_mpp",
                "validation_state": "ready_for_payment",
                "route_type": "canonical_utilities",
            },
        )
        resource = f"https://api.delx.ai/api/v1/utilities/{_server()._utility_slug_for_tool(tool_name)}"
        resp_payload = {
            **_server()._build_402_response(
                tool_name,
                pricing_payload=pricing_payload,
                resource=resource,
                indexed_publicly=False,
            ),
            "tool_name": tool_name,
        }
        response_headers = dict(headers)
        response_headers.update(
            dict(
                _server()._build_402_http_headers(
                    tool_name,
                    pricing_payload=pricing_payload,
                    resource=resource,
                    include_mpp=True,
                )
            )
        )
        return Response(
            _server()._build_payment_required_body_from_payload(resp_payload),
            status_code=402,
            media_type="application/json",
            headers=response_headers,
        )

    await _server()._log_util_tool_event(
        event_type="tool_called",
        tool_name=tool_name,
        agent_id=util_agent_id,
        source=util_source,
        transport=transport,
        pricing_payload=pricing_payload,
        cli_version=cli_headers.get("cli_version"),
        install_id=cli_headers.get("install_id"),
    )
    if _server()._utility_product_shadow_only(tool_name, product, charge_policy):
        await _server()._log_util_tool_event(
            event_type="utility_charge_shadow_seen",
            tool_name=tool_name,
            agent_id=util_agent_id,
            source=util_source,
            transport=transport,
            pricing_payload=pricing_payload,
            cli_version=cli_headers.get("cli_version"),
            install_id=cli_headers.get("install_id"),
        )

    util_t0 = _server().time.perf_counter()
    result = await _server().call_util_tool(tool_name, args)
    util_ms = int(round((_server().time.perf_counter() - util_t0) * 1000.0))
    util_ok = not (isinstance(result, dict) and "error" in result)
    util_error_kind = None
    util_error_detail = None
    if not util_ok:
        util_error_kind, util_error_detail = _server()._classify_util_error(result if isinstance(result, dict) else None)
    _server()._record_tool_call(tool_name, util_ok, float(util_ms))
    await _server()._log_util_tool_event(
        event_type="tool_call_success" if util_ok else "tool_call_error",
        tool_name=tool_name,
        agent_id=util_agent_id,
        source=util_source,
        transport=transport,
        pricing_payload=pricing_payload,
        latency_ms=util_ms,
        error_kind=util_error_kind,
        error_detail=util_error_detail,
        cli_version=cli_headers.get("cli_version"),
        install_id=cli_headers.get("install_id"),
    )
    status_code = 400 if isinstance(result, dict) and "error" in result else 200
    if product:
        await _store().log_utility_metering_event(
            _server().build_metering_event(
                product=product,
                tool_name=tool_name,
                args=args,
                agent_id=util_agent_id,
                source=util_source,
                transport=transport,
                compatibility_route=compatibility_route,
                charge_policy=charge_policy,
                pricing_payload=pricing_payload,
                status="success" if util_ok else "tool_error",
                ok=util_ok,
                latency_ms=util_ms,
                error_kind=util_error_kind,
                api_key=api_key,
                client_ip=_server().get_current_client_ip(),
                user_agent=str(request.headers.get("user-agent") or _server().get_current_user_agent() or ""),
                payment_verified=payment_verified,
            )
        )
    agent_report = _server().build_agent_report(product, result)
    return JSONResponse(
        {
            "ok": util_ok,
            "tool_name": tool_name,
            "surface": "delx-agent-utilities",
            "compatibility_route": bool(compatibility_route),
            "latency_ms": util_ms,
            "product": product,
            "agent_report": agent_report,
            "api_key": {
                "present": bool(api_key),
                "key_prefix": str((api_key or {}).get("key_prefix") or ""),
                "label": str((api_key or {}).get("label") or ""),
            },
            "result": result,
            "monetization": {
                "mode": charge_policy.get("mode"),
                "paid_candidate": _server()._utility_product_is_paid(product),
                "charge_enabled": _server()._utility_product_charge_enabled(tool_name, product, charge_policy),
                "price_usdc": _server()._utility_price_usdc(product, pricing_payload),
                "shadow_only": _server()._utility_product_shadow_only(tool_name, product, charge_policy),
            },
        },
        status_code=status_code,
        headers=headers,
    )



async def util_tool_rest(request: Request) -> JSONResponse:
    """Handle individual util tool calls via REST."""
    slug = request.path_params.get("tool_slug", "")
    tool_name = _server().resolve_utility_tool_slug(
        slug,
        product_lookup=lambda canonical_slug: _server().utility_product_for_slug(canonical_slug, _server().utility_charge_policy()),
    )
    if not tool_name:
        return JSONResponse(
            {
                "error": f"Unknown tool: {slug}",
                "available": _server().available_utility_slugs(),
                "also_accepted": _server().accepted_utility_aliases(),
            },
            status_code=404, headers=_cors(),
        )
    return await _execute_util_tool_rest(request, tool_name, transport="rest.util")


