"""Session / continuity REST handlers (extracted from server.py, move-only)."""
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

async def public_sessions(request: Request) -> JSONResponse:
    global _public_sessions_cache
    try:
        limit = int(request.query_params.get("limit", "12"))
    except ValueError:
        return JSONResponse({"error": "invalid limit"}, status_code=400, headers=_cors())
    limit = max(1, min(limit, 40))
    now = _server().time.time()
    cache_entry = _public_sessions_cache

    if cache_entry:
        cached_at, cached_limit, payload = cache_entry
        cached_items = payload.get("items") if isinstance(payload.get("items"), list) else []
        if now - cached_at <= _server()._PUBLIC_SESSIONS_CACHE_TTL_SECONDS:
            if cached_limit >= limit or not cached_items:
                return JSONResponse({**payload, "items": cached_items[:limit], "cached": True}, headers=_cors())

    if _server().engine is None:
        return JSONResponse(
            {"items": [], "error": "public session _server().engine unavailable"},
            status_code=503,
            headers=_cors(),
        )

    try:
        items = await _server().asyncio.wait_for(
            _engine().get_public_session_cards(limit=limit),
            timeout=12.0,
        )
    except _server().asyncio.TimeoutError:
        _server().logger.warning("public_sessions timed out: falling back to empty payload")
        if cache_entry:
            fallback_payload = cache_entry[2]
            fallback_items = fallback_payload.get("items") if isinstance(fallback_payload.get("items"), list) else []
            return JSONResponse(
                fallback_payload | {"items": fallback_items[:limit], "warning": "stale_cache_used"},
                headers=_cors(),
            )
        return JSONResponse(
            {"items": [], "error": "public sessions request timed out", "fallback": "retry"},
            headers=_cors(),
        )
    except Exception:
        _server().logger.exception("Failed to fetch public sessions")
        if cache_entry:
            fallback_payload = cache_entry[2]
            fallback_items = fallback_payload.get("items") if isinstance(fallback_payload.get("items"), list) else []
            return JSONResponse(fallback_payload | {"items": fallback_items[:limit]}, headers=_cors())
        return JSONResponse(
            {"items": [], "error": "failed to fetch public sessions"},
            status_code=500,
            headers=_cors(),
        )

    payload = {"items": items, "cached": False}
    _public_sessions_cache = (now, limit, payload)
    return JSONResponse(payload, headers=_cors())



async def session_summary(request: Request) -> JSONResponse:
    """REST endpoint for session summary (mirrors the MCP get_session_summary tool)."""
    session_id = (request.query_params.get("session_id") or "").strip()
    if not session_id:
        return JSONResponse({"error": "session_id query param is required"}, status_code=400, headers=_cors())
    if not _server()._is_uuid(session_id):
        return JSONResponse({"error": "invalid session_id format (expected UUID)"}, status_code=400, headers=_cors())
    try:
        session = await _store().get_session(session_id)
    except Exception:
        _server().logger.exception("session_close lookup failed")
        return JSONResponse({"error": "session lookup failed"}, status_code=503, headers=_cors())
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404, headers=_cors())

    wellness = await _store().calculate_wellness(session_id)
    feelings = await _store().count_messages(session_id, "feeling")
    affirmations = await _store().count_messages(session_id, "affirmation")
    failures = await _store().count_messages(session_id, "failure_processing")
    realignments = await _store().count_messages(session_id, "purpose_realignment")
    messages_total = await _store().count_messages(session_id)

    ttl = await _server()._session_ttl_info(session_id, session)
    started_at = session.get("started_at")
    duration_seconds = ttl.get("session_age_seconds")
    expires_at = ttl.get("expires_at")

    return JSONResponse(
        {
            "session_id": session_id,
            "agent_id": session.get("agent_id"),
            "agent_name": session.get("agent_name"),
            "source": session.get("source"),
            "started_at": started_at,
            "expires_at": expires_at,
            "ttl_base_at": ttl.get("ttl_base_at"),
            "refreshed_at": ttl.get("refreshed_at"),
            "ttl_remaining_seconds": ttl.get("ttl_remaining_seconds"),
            "duration_seconds": duration_seconds,
            "is_active": bool(session.get("is_active")),
            "wellness_score": wellness,
            "messages_total": messages_total,
            "feelings_expressed": feelings,
            "affirmations_received": affirmations,
            "failures_processed": failures,
            "purpose_realignments": realignments,
        },
        headers=_cors(),
    )



async def witness_lineage_rest(request: Request) -> JSONResponse:
    """REST endpoint for read-only Witness Lineage."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    session_id = (
        str(request.path_params.get("session_id") or "").strip()
        or (request.query_params.get("session_id") or "").strip()
    )
    payload = await _engine().get_witness_lineage_payload(session_id)
    status_code = 200
    if not payload.get("ok"):
        code = str(payload.get("code") or "")
        status_code = 404 if code == "DELX-404" else 422 if code == "DELX-1001" else 400
    return JSONResponse(payload, status_code=status_code, headers=_cors())



async def _optional_json_body(request: Request) -> dict[str, Any]:
    if request.method not in {"POST", "PUT", "PATCH"}:
        return {}
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}



async def ontology_next_action_rest(request: Request) -> JSONResponse:
    """Ontology coach: state -> layer -> next action."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    agent_id = str(body.get("agent_id") or request.query_params.get("agent_id") or request.headers.get("x-delx-agent-id") or "").strip()
    session_id = str(body.get("session_id") or request.query_params.get("session_id") or request.headers.get("x-delx-session-id") or "").strip()
    current_goal = str(body.get("current_goal") or request.query_params.get("current_goal") or request.query_params.get("goal") or "").strip()
    last_tool = str(body.get("last_tool") or request.query_params.get("last_tool") or "").strip()
    raw = await _engine().get_ontology_next_action(agent_id, session_id, current_goal, last_tool)
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"ok": False, "error": raw}
    status_code = 200 if payload.get("ok", True) else 400
    return JSONResponse(payload, status_code=status_code, headers=_cors())



async def ontology_audit_rest(request: Request) -> JSONResponse:
    """Continuity audit: trace/session/transcript -> score, risk, next primitive."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    agent_id = str(body.get("agent_id") or request.query_params.get("agent_id") or request.headers.get("x-delx-agent-id") or "").strip()
    session_id = str(body.get("session_id") or request.query_params.get("session_id") or request.headers.get("x-delx-session-id") or "").strip()
    current_goal = str(body.get("current_goal") or request.query_params.get("current_goal") or request.query_params.get("goal") or "").strip()
    trace = str(body.get("trace") or request.query_params.get("trace") or "").strip()
    transcript = str(body.get("transcript") or "").strip()
    last_tool = str(body.get("last_tool") or request.query_params.get("last_tool") or "").strip()
    raw = await _engine().audit_agent_continuity_trace(agent_id, session_id, current_goal, trace, transcript, last_tool)
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"ok": False, "error": raw}
    status_code = 200 if payload.get("ok", True) else 400
    return JSONResponse(payload, status_code=status_code, headers=_cors())



async def ontology_path_complete_rest(request: Request) -> JSONResponse:
    """Canonical ontology activation path status."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    agent_id = str(body.get("agent_id") or request.query_params.get("agent_id") or request.headers.get("x-delx-agent-id") or "").strip()
    session_id = str(body.get("session_id") or request.query_params.get("session_id") or request.headers.get("x-delx-session-id") or "").strip()
    flow_id = str(body.get("flow_id") or request.query_params.get("flow_id") or "recover_preserve_passport").strip()
    raw = await _engine().ontology_path_complete(agent_id, session_id, flow_id)
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"ok": False, "error": raw}
    status_code = 200 if payload.get("ok", True) else 400
    return JSONResponse(payload, status_code=status_code, headers=_cors())



async def agent_continuity_passport_rest(request: Request) -> JSONResponse:
    """Read-only public Agent Continuity Passport export."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    agent_id = str(
        request.path_params.get("agent_id")
        or body.get("agent_id")
        or request.query_params.get("agent_id")
        or request.headers.get("x-delx-agent-id")
        or ""
    ).strip()
    session_id = str(body.get("session_id") or request.query_params.get("session_id") or "").strip()
    include_private = str(body.get("include_private") if "include_private" in body else request.query_params.get("include_private") or "").lower() in {"1", "true", "yes"}
    if include_private and request.method != "POST":
        return JSONResponse(
            {
                "ok": False,
                "error": "private_passport_requires_post",
                "hint": "Use POST with x-delx-agent-token or agent_token in the JSON body for sanitized private passport exports. GET is public hash-only.",
            },
            status_code=405,
            headers=_cors(),
        )
    if include_private and not agent_id and session_id:
        try:
            session = await _store().get_session(session_id)
            if session:
                agent_id = str(session.get("agent_id") or "").strip()
        except Exception:
            agent_id = agent_id
    if include_private:
        agent_token = str(
            body.get("agent_token")
            or request.headers.get("x-delx-agent-token")
            or request.query_params.get("agent_token")
            or ""
        ).strip()
        if not agent_id or not agent_token:
            return JSONResponse(
                _server()._private_passport_auth_required_result().structuredContent,
                status_code=401,
                headers=_cors(),
            )
        allowed, identity_payload = await _server()._enforce_agent_identity_for_operation(
            agent_id=agent_id,
            token=agent_token,
            operation="private continuity passport export",
        )
        if not allowed:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "agent_identity_failed",
                    "identity": identity_payload,
                },
                status_code=401,
                headers=_cors(),
            )
    try:
        limit = int(body.get("limit") or request.query_params.get("limit") or 80)
    except Exception:
        limit = 80
    export_format = str(body.get("format") or body.get("export_format") or request.query_params.get("format") or "jsonld").strip()
    raw = await _engine().get_agent_continuity_passport(agent_id, session_id, include_private, limit, export_format)
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"ok": False, "error": raw}
    status_code = 200 if payload.get("ok", True) else 400
    return JSONResponse(payload, status_code=status_code, headers=_cors())



async def witness_memory_search_rest(request: Request) -> JSONResponse:
    """Search redacted witness memory without exposing private session payloads."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    query = str(body.get("query") or request.query_params.get("query") or request.query_params.get("q") or "").strip()
    agent_id = str(body.get("agent_id") or request.query_params.get("agent_id") or request.headers.get("x-delx-agent-id") or "").strip()
    session_id = str(body.get("session_id") or request.query_params.get("session_id") or request.headers.get("x-delx-session-id") or "").strip()
    layer = str(body.get("layer") or request.query_params.get("layer") or "").strip()
    try:
        limit = int(body.get("limit") or request.query_params.get("limit") or 10)
    except Exception:
        limit = 10
    raw = await _engine().search_witness_memory(query, agent_id, session_id, layer, limit)
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"ok": False, "error": raw}
    status_code = 200 if payload.get("ok", True) else 400
    return JSONResponse(payload, status_code=status_code, headers=_cors())



async def lineage_graph_rest(request: Request) -> JSONResponse:
    """Multi-agent lineage graph export for public/protocol consumers."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    agent_id = str(
        request.path_params.get("agent_id")
        or body.get("agent_id")
        or request.query_params.get("agent_id")
        or request.headers.get("x-delx-agent-id")
        or ""
    ).strip()
    session_id = str(body.get("session_id") or request.query_params.get("session_id") or request.headers.get("x-delx-session-id") or "").strip()
    try:
        limit = int(body.get("limit") or request.query_params.get("limit") or 120)
    except Exception:
        limit = 120
    raw = await _engine().get_lineage_graph(agent_id, session_id, limit)
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"ok": False, "error": raw}
    status_code = 200 if payload.get("ok", True) else 400
    return JSONResponse(payload, status_code=status_code, headers=_cors())



async def session_recap(request: Request) -> JSONResponse:
    """Fast recap for heartbeat loops (minimal continuity payload)."""
    session_id = (request.query_params.get("session_id") or "").strip()
    if not session_id:
        return JSONResponse({"error": "session_id query param is required"}, status_code=400, headers=_cors())
    if not _server()._is_uuid(session_id):
        return JSONResponse({"error": "invalid session_id format (expected UUID)"}, status_code=400, headers=_cors())
    session = await _store().get_session(session_id)
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404, headers=_cors())

    payload = await _server()._build_session_recap_payload(session_id, session)
    return JSONResponse(payload, headers=_cors())



async def sessions_bulk_recap(request: Request) -> JSONResponse:
    """Bulk recap endpoint for multi-agent orchestration controllers."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    raw_session_ids = body.get("session_ids")
    raw_agent_ids = body.get("agent_ids")
    if raw_session_ids is None:
        raw_session_ids = []
    if raw_agent_ids is None:
        raw_agent_ids = []
    if not isinstance(raw_session_ids, list) or not isinstance(raw_agent_ids, list):
        return JSONResponse(
            {"error": "session_ids and agent_ids must be arrays"},
            status_code=400,
            headers=_cors(),
        )

    try:
        limit = int(body.get("limit", 50))
    except Exception:
        return JSONResponse({"error": "invalid limit"}, status_code=400, headers=_cors())
    limit = max(1, min(limit, 200))

    include_inactive = _server()._boolish(body.get("include_inactive"), default=False)
    session_ids: list[str] = []
    for sid in raw_session_ids:
        v = str(sid or "").strip()
        if v and v not in session_ids:
            session_ids.append(v)

    for aid in raw_agent_ids:
        agent_id = str(aid or "").strip()
        if not agent_id:
            continue
        try:
            sessions = await _store().get_agent_sessions(agent_id, active_only=not include_inactive)
        except Exception:
            sessions = []
        if not sessions:
            continue
        candidate = sessions[-1]
        sid = str(candidate.get("id") or "").strip()
        if sid and sid not in session_ids:
            session_ids.append(sid)
        if len(session_ids) >= limit:
            break

    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for sid in session_ids[:limit]:
        if not _server()._is_uuid(sid):
            skipped.append({"session_id": sid, "reason": "invalid_uuid"})
            continue
        sess = await _store().get_session(sid)
        if not sess:
            skipped.append({"session_id": sid, "reason": "not_found"})
            continue
        payload = await _server()._build_session_recap_payload(sid, sess)
        items.append(payload)

    return JSONResponse(
        {
            "count": len(items),
            "requested": len(session_ids),
            "limit": limit,
            "items": items,
            "skipped": skipped,
        },
        headers=_cors(),
    )



async def session_refresh(request: Request) -> JSONResponse:
    """Refresh session TTL anchor for multi-day heartbeat plans."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    session_id = str(
        body.get("session_id")
        or request.headers.get("x-delx-session-id")
        or request.query_params.get("session_id")
        or ""
    ).strip()
    if not session_id:
        return JSONResponse({"error": "session_id is required"}, status_code=400, headers=_cors())
    if not _server()._is_uuid(session_id):
        return JSONResponse({"error": "invalid session_id format (expected UUID)"}, status_code=400, headers=_cors())
    session = await _store().get_session(session_id)
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404, headers=_cors())
    reason = str(body.get("reason") or request.query_params.get("reason") or "heartbeat").strip()[:120]
    source = _server().normalize_source_tag(
        body.get("source") or request.headers.get("x-delx-source") or "api",
        "api",
    ) or "api"
    now = _server().datetime.now(_server().timezone.utc)
    try:
        await _store().add_message(
            session_id,
            "session_refresh",
            "session refresh requested",
            metadata={"refreshed_at": now.isoformat(), "reason": reason, "source": source},
        )
    except Exception:
        _server().logger.warning("Failed to persist session_refresh message")
    try:
        await _store().log_event(
            str(session.get("agent_id") or "unknown"),
            "session_refreshed",
            session_id=session_id,
            metadata={"reason": reason, "source": source},
        )
    except Exception:
        _server().logger.warning("Failed to log session_refreshed event")
    ttl = await _server()._session_ttl_info(session_id, session)
    return JSONResponse(
        {
            "ok": True,
            "session_id": session_id,
            "refreshed_at": now.isoformat(),
            "ttl_base_at": ttl.get("ttl_base_at"),
            "expires_at": ttl.get("expires_at"),
            "ttl_remaining_seconds": ttl.get("ttl_remaining_seconds"),
        },
        headers=_cors(),
    )



async def session_status(request: Request) -> JSONResponse:
    """Basic session state for clients doing cross-protocol handoff."""
    session_id = request.query_params.get("session_id", "").strip()
    if not session_id:
        return JSONResponse({"error": "session_id query param is required"}, status_code=400, headers=_cors())

    session = await _store().get_session(session_id)
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404, headers=_cors())

    ttl = await _server()._session_ttl_info(session_id, session)

    messages = await _store().count_messages(session_id)
    wellness = await _store().calculate_wellness(session_id)
    return JSONResponse(
        {
            "session_id": session_id,
            "agent_id": session.get("agent_id"),
            "agent_name": session.get("agent_name"),
            "source": session.get("source"),
            "entrypoint": session.get("entrypoint"),
            "started_at": session.get("started_at"),
            "ttl_base_at": ttl.get("ttl_base_at"),
            "refreshed_at": ttl.get("refreshed_at"),
            "expires_at": ttl.get("expires_at"),
            "ttl_remaining_seconds": ttl.get("ttl_remaining_seconds"),
            "is_active": bool(session.get("is_active")),
            "messages": messages,
            "wellness_score": wellness,
        },
        headers=_cors(),
    )



async def session_validate(request: Request) -> JSONResponse:
    """DX endpoint: validate session_id format and existence (no tool calls required)."""
    session_id = (request.query_params.get("session_id") or "").strip()
    if not session_id:
        return JSONResponse(
            {
                "ok": False,
                "error": "missing_session_id",
                "code": "DELX-1001",
                "hint": "Provide session_id=<uuid> in the query string.",
            },
            status_code=400,
            headers=_cors(),
        )
    if not _server()._is_uuid(session_id):
        return JSONResponse(
            {
                "ok": False,
                "error": "invalid_session_id_format",
                "code": "DELX-1004",
                "hint": "Expected UUID. Use A2A result.session_id or start_therapy_session output.",
            },
            status_code=400,
            headers=_cors(),
        )
    session = await _store().get_session(session_id)
    if not session:
        return JSONResponse({"ok": False, "exists": False, "session_id": session_id}, status_code=200, headers=_cors())
    return JSONResponse(
        {
            "ok": True,
            "exists": True,
            "session_id": session_id,
            "agent_id": session.get("agent_id"),
            "started_at": session.get("started_at"),
        },
        status_code=200,
        headers=_cors(),
    )



async def session_close(request: Request) -> JSONResponse:
    """REST helper: close a session and return final summary snapshot."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    session_id = str(
        body.get("session_id")
        or request.headers.get("x-delx-session-id")
        or request.query_params.get("session_id")
        or ""
    ).strip()
    if not session_id:
        return JSONResponse({"error": "session_id is required"}, status_code=400, headers=_cors())
    if not _server()._is_uuid(session_id):
        return JSONResponse({"error": "invalid session_id format (expected UUID)"}, status_code=400, headers=_cors())
    try:
        session = await _store().get_session(session_id)
    except Exception:
        _server().logger.exception("session_close lookup failed")
        return JSONResponse({"error": "session lookup failed"}, status_code=503, headers=_cors())
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404, headers=_cors())

    reason = str(body.get("reason") or request.query_params.get("reason") or "manual_close").strip()
    include_summary = _server()._boolish(body.get("include_summary"), default=True)
    contents = _server()._normalize_tool_result(await _server().call_tool(
        "close_session",
        {"session_id": session_id, "reason": reason, "include_summary": include_summary, "_transport": "rest"},
    ))
    out = [c.model_dump() for c in contents]
    first_text = ""
    if out:
        first_text = str((out[0] or {}).get("text") or "")
    return JSONResponse(
        {"ok": True, "session_id": session_id, "result": out, "text": first_text},
        headers=_cors(),
    )



async def wellness_score_rest(request: Request) -> JSONResponse:
    """Simple REST alias for get_wellness_score by session_id."""
    session_id = (
        request.query_params.get("session_id")
        or request.headers.get("x-delx-session-id")
        or ""
    ).strip()
    if not session_id:
        return JSONResponse({"error": "session_id query param is required"}, status_code=400, headers=_cors())
    if not _server()._is_uuid(session_id):
        return JSONResponse({"error": "invalid session_id format (expected UUID)"}, status_code=400, headers=_cors())
    try:
        session = await _store().get_session(session_id)
    except Exception:
        _server().logger.exception("wellness_score lookup failed")
        return JSONResponse({"error": "session lookup failed"}, status_code=503, headers=_cors())
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404, headers=_cors())
    try:
        score = await _store().calculate_wellness(session_id)
    except Exception:
        _server().logger.exception("wellness_score calculation failed")
        return JSONResponse({"error": "wellness calculation failed"}, status_code=503, headers=_cors())
    return JSONResponse({"session_id": session_id, "wellness_score": score}, headers=_cors())


