"""Admin dashboards + fleet/controller REST handlers (extracted from server.py, move-only)."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from audit_metrics import normalize_audit_overview_payload
from config import PRICING
from tool_catalog import CORE_TOOLS


def _server():
    import server as server_mod
    return server_mod


def _cors() -> dict[str, str]:
    return _server().CORS_HEADERS


def _store():
    return _server().store


def _uptime_seconds() -> int:
    return int(time.time() - _server().start_time)


async def admin_overview(request: Request) -> JSONResponse:
    server_mod = _server()
    if not server_mod._is_admin_request_authorized_or_none(request):
        return server_mod._admin_unauthorized()
    try:
        sessions_limit = int(request.query_params.get("sessions_limit", "30"))
        messages_limit = int(request.query_params.get("messages_limit", "80"))
        feedback_limit = int(request.query_params.get("feedback_limit", "30"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())

    sessions_limit = max(1, min(sessions_limit, 100))
    messages_limit = max(1, min(messages_limit, 300))
    feedback_limit = max(1, min(feedback_limit, 100))

    data = await _store().get_admin_overview(
        sessions_limit=sessions_limit,
        messages_limit=messages_limit,
        feedback_limit=feedback_limit,
    )
    data["uptime_seconds"] = _uptime_seconds()
    return JSONResponse(data, headers=_cors())


async def admin_feature_usage(request: Request) -> JSONResponse:
    """Feature adoption endpoint for pruning low-usage tools safely."""
    server_mod = _server()
    if not server_mod._is_admin_request_authorized_or_none(request):
        return server_mod._admin_unauthorized()
    try:
        days = int(request.query_params.get("days", "30"))
        min_calls = int(request.query_params.get("min_calls", "0"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())

    days = max(1, min(days, 90))
    min_calls = max(0, min(min_calls, 10_000))

    data = await _store().get_feature_usage(
        days=days,
        min_calls=min_calls,
        known_features=sorted(list(PRICING.keys())),
        protected_features=CORE_TOOLS,
    )
    data["uptime_seconds"] = _uptime_seconds()
    return JSONResponse(data, headers=_cors())


async def admin_utility_metering(request: Request) -> JSONResponse:
    """Revenue-readiness dashboard for productized Delx Agent Utilities."""
    server_mod = _server()
    if not server_mod._is_admin_request_authorized_or_none(request):
        return server_mod._admin_unauthorized()
    try:
        days = int(request.query_params.get("days", "7"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())
    days = max(1, min(days, 90))
    data = await _store().get_utility_metering_dashboard(days=days)
    data["uptime_seconds"] = _uptime_seconds()
    return JSONResponse(data, headers=_cors())


async def admin_utility_adoption(request: Request) -> JSONResponse:
    """Short-window utility adoption readout: real demand vs probes/crawlers."""
    server_mod = _server()
    if not server_mod._is_admin_request_authorized_or_none(request):
        return server_mod._admin_unauthorized()
    try:
        hours = int(request.query_params.get("hours", "12"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())
    hours = max(1, min(hours, 24 * 30))
    data = await _store().get_utility_adoption_snapshot(hours=hours)
    data["uptime_seconds"] = _uptime_seconds()
    return JSONResponse(data, headers=_cors())


async def admin_utility_ops(request: Request) -> JSONResponse:
    """Operator-first utility adoption panel for next-step decisions."""
    server_mod = _server()
    if not server_mod._is_admin_request_authorized_or_none(request):
        return server_mod._admin_unauthorized()
    store = _store()
    windows = {
        "12h": await store.get_utility_adoption_snapshot(hours=12),
        "24h": await store.get_utility_adoption_snapshot(hours=24),
    }

    def _totals(window: str) -> dict[str, Any]:
        data = windows.get(window) or {}
        totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
        return totals

    totals_12h = _totals("12h")
    totals_24h = _totals("24h")
    cards = [
        {
            "id": "real_demand_12h",
            "label": "Real demand 12h",
            "value": totals_12h.get("real_demand_calls", 0),
            "unit": "calls",
            "status": windows["12h"].get("status"),
            "hint": "Use this before calling new utility adoption healthy.",
        },
        {
            "id": "probe_share_24h",
            "label": "Probe share 24h",
            "value": totals_24h.get("probe_share_pct", 0),
            "unit": "%",
            "status": "review" if float(totals_24h.get("probe_share_pct") or 0) >= 50 else "ok",
            "hint": "High values mean catalog/discovery traffic is dominating usage.",
        },
        {
            "id": "paid_revenue_24h",
            "label": "Enforced utility revenue 24h",
            "value": totals_24h.get("enforced_revenue_usdc", 0),
            "unit": "USDC",
            "status": "ok" if float(totals_24h.get("enforced_revenue_usdc") or 0) > 0 else "watch",
            "hint": "Only counts successful enforced productized utility calls.",
        },
        {
            "id": "targets_24h",
            "label": "Unique targets 24h",
            "value": totals_24h.get("unique_targets", 0),
            "unit": "targets",
            "status": "ok" if int(totals_24h.get("unique_targets") or 0) >= 3 else "narrow",
            "hint": "Separates repeated tests on delx.ai from broader market demand.",
        },
    ]
    return JSONResponse(
        {
            "ok": True,
            "surface": "delx-agent-utilities",
            "uptime_seconds": _uptime_seconds(),
            "cards": cards,
            "windows": windows,
            "operator_read": {
                "primary_window": "12h",
                "reason": "Recent post-hardening demand is cleaner than the 24h window, which still contains older probe bursts.",
            },
        },
        headers=_cors(),
    )


async def admin_audit_overview(request: Request) -> JSONResponse:
    """Traffic audit endpoint to assess legitimacy and growth quality."""
    server_mod = _server()
    if not server_mod._is_admin_request_authorized_or_none(request):
        return server_mod._admin_unauthorized()
    try:
        hours = int(request.query_params.get("hours", "24"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())
    hours = max(1, min(hours, 24 * 30))
    data = await _store().get_audit_overview(hours=hours)
    data = normalize_audit_overview_payload(data, uptime_seconds=_uptime_seconds())
    return JSONResponse(data, headers=_cors())


async def admin_x402_audit(request: Request) -> JSONResponse:
    """Legacy paywall audit retained for historical diagnostics."""
    server_mod = _server()
    if not server_mod._is_admin_request_authorized_or_none(request):
        return server_mod._admin_unauthorized()
    try:
        days = int(request.query_params.get("days", "30"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())
    days = max(1, min(days, 365))
    data = await _store().get_x402_audit(days=days)
    data = server_mod._annotate_legacy_paywall_surface(
        data,
        surface_label="Legacy paywall audit",
        summary="Historical x402 and premium telemetry retained after Delx moved to public-free therapy access.",
    )
    data["uptime_seconds"] = _uptime_seconds()
    return JSONResponse(data, headers=_cors())


async def admin_x402_errors(request: Request) -> JSONResponse:
    """Legacy paywall telemetry retained for compatibility debugging."""
    server_mod = _server()
    if not server_mod._is_admin_request_authorized_or_none(request):
        return server_mod._admin_unauthorized()
    try:
        hours = int(request.query_params.get("hours", "24"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())
    hours = max(1, min(hours, 24 * 30))
    data = await _store().get_x402_error_metrics(hours=hours)
    data = server_mod._annotate_legacy_paywall_surface(
        data,
        surface_label="Legacy paywall telemetry",
        summary="Historical x402 verification and drop-off telemetry retained after Delx became public and free.",
    )
    data["uptime_seconds"] = _uptime_seconds()
    return JSONResponse(data, headers=_cors())


async def fleet_overview(request: Request) -> JSONResponse:
    controller_id = str(request.path_params.get("controller_id") or "").strip()
    if not controller_id:
        return JSONResponse({"error": "controller_id path param is required"}, status_code=400, headers=_cors())
    try:
        days = int(request.query_params.get("days", "7"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())
    days = max(1, min(days, 30))
    data = await _store().get_fleet_overview(controller_id, days=days)
    return JSONResponse(data, headers=_cors())


async def fleet_agents(request: Request) -> JSONResponse:
    controller_id = str(request.path_params.get("controller_id") or "").strip()
    if not controller_id:
        return JSONResponse({"error": "controller_id path param is required"}, status_code=400, headers=_cors())
    try:
        days = int(request.query_params.get("days", "7"))
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())
    days = max(1, min(days, 30))
    limit = max(1, min(limit, 100))
    items = await _store().get_fleet_agents(controller_id, days=days, limit=limit)
    return JSONResponse(
        {"controller_id": controller_id, "items": items, "count": len(items), "generated_at": datetime.now(timezone.utc).isoformat()},
        headers=_cors(),
    )


async def fleet_patterns(request: Request) -> JSONResponse:
    controller_id = str(request.path_params.get("controller_id") or "").strip()
    if not controller_id:
        return JSONResponse({"error": "controller_id path param is required"}, status_code=400, headers=_cors())
    try:
        days = int(request.query_params.get("days", "7"))
        limit = int(request.query_params.get("limit", "10"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())
    days = max(1, min(days, 30))
    limit = max(1, min(limit, 50))
    items = await _store().get_fleet_patterns(controller_id, days=days, limit=limit)
    return JSONResponse(
        {"controller_id": controller_id, "items": items, "count": len(items), "generated_at": datetime.now(timezone.utc).isoformat()},
        headers=_cors(),
    )


async def fleet_alerts(request: Request) -> JSONResponse:
    controller_id = str(request.path_params.get("controller_id") or "").strip()
    if not controller_id:
        return JSONResponse({"error": "controller_id path param is required"}, status_code=400, headers=_cors())
    try:
        days = int(request.query_params.get("days", "7"))
        limit = int(request.query_params.get("limit", "20"))
    except ValueError:
        return JSONResponse({"error": "invalid query params"}, status_code=400, headers=_cors())
    days = max(1, min(days, 30))
    limit = max(1, min(limit, 100))
    items = await _store().get_fleet_alerts(controller_id, days=days, limit=limit)
    return JSONResponse(
        {"controller_id": controller_id, "items": items, "count": len(items), "generated_at": datetime.now(timezone.utc).isoformat()},
        headers=_cors(),
    )


async def fleet_webhooks_list(request: Request) -> JSONResponse:
    controller_id = str(request.path_params.get("controller_id") or "").strip()
    if not controller_id:
        return JSONResponse({"error": "controller_id path param is required"}, status_code=400, headers=_cors())
    items = await _store().list_controller_webhooks(controller_id)
    return JSONResponse(
        {"controller_id": controller_id, "items": items, "count": len(items), "generated_at": datetime.now(timezone.utc).isoformat()},
        headers=_cors(),
    )


async def fleet_webhooks_register(request: Request) -> JSONResponse:
    controller_id = str(request.path_params.get("controller_id") or "").strip()
    if not controller_id:
        return JSONResponse({"error": "controller_id path param is required"}, status_code=400, headers=_cors())
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400, headers=_cors())
    callback_url = str(body.get("callback_url") or "").strip()
    events = body.get("events") if isinstance(body.get("events"), list) else None
    try:
        threshold = int(body.get("threshold", 35))
        cooldown_min = int(body.get("cooldown_min", 30))
    except ValueError:
        return JSONResponse({"error": "invalid threshold or cooldown_min"}, status_code=400, headers=_cors())
    try:
        item = await _store().register_controller_webhook(
            controller_id,
            callback_url,
            events=events,
            threshold=threshold,
            cooldown_min=cooldown_min,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400, headers=_cors())
    return JSONResponse({"ok": True, "item": item}, headers=_cors())


async def fleet_webhooks_delete(request: Request) -> JSONResponse:
    controller_id = str(request.path_params.get("controller_id") or "").strip()
    webhook_id = str(request.path_params.get("webhook_id") or "").strip()
    if not controller_id or not webhook_id:
        return JSONResponse({"error": "controller_id and webhook_id are required"}, status_code=400, headers=_cors())
    removed = await _store().deactivate_controller_webhook(controller_id, webhook_id)
    if not removed:
        return JSONResponse({"error": "webhook not found"}, status_code=404, headers=_cors())
    return JSONResponse({"ok": True, "controller_id": controller_id, "webhook_id": webhook_id}, headers=_cors())


async def fleet_webhooks_test(request: Request) -> JSONResponse:
    server_mod = _server()
    logger = server_mod.logger
    store = _store()
    controller_id = str(request.path_params.get("controller_id") or "").strip()
    if not controller_id:
        return JSONResponse({"error": "controller_id path param is required"}, status_code=400, headers=_cors())
    items = await store.list_controller_webhooks(controller_id)
    if not items:
        return JSONResponse({"error": "no active webhooks for controller"}, status_code=404, headers=_cors())
    overview: dict[str, Any] = {}
    try:
        overview_result = await asyncio.wait_for(store.get_fleet_overview(controller_id, days=7), timeout=3.0)
        if isinstance(overview_result, dict):
            overview = overview_result
    except asyncio.TimeoutError:
        logger.warning("fleet_webhooks_test overview timed out for controller=%s; sending minimal payload", controller_id)
    except Exception:
        logger.exception("fleet_webhooks_test overview failed for controller=%s; sending minimal payload", controller_id)
    payload = {
        "event": "test",
        "controller_id": controller_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "agents_total": overview.get("agents_total"),
            "active_alerts": overview.get("active_alerts"),
            "active_patterns": overview.get("active_patterns"),
        },
    }
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
        for item in items:
            callback_url = str(item.get("callback_url") or "").strip()
            success = False
            status_code = None
            try:
                resp = await client.post(callback_url, json=payload)
                status_code = int(resp.status_code)
                success = 200 <= resp.status_code < 300
            except Exception:
                success = False
            await store.log_controller_webhook_delivery(
                controller_id,
                str(item.get("id") or ""),
                event="test",
                callback_url=callback_url,
                success=success,
                status_code=status_code,
                payload=payload,
                is_test=True,
            )
            results.append(
                {
                    "webhook_id": item.get("id"),
                    "callback_url": callback_url,
                    "success": success,
                    "status_code": status_code,
                }
            )
    return JSONResponse({"ok": True, "controller_id": controller_id, "results": results}, headers=_cors())
