"""Rewards REST handlers (extracted from server.py, move-only)."""
from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from config import is_all_free_mode
from rewards_logic import (
    _reward_epochs_payload,
    _reward_fetch_one,
    _reward_json,
    _reward_leaderboard_payload,
    _reward_missions_payload,
    _rewards_claim_proof_payload,
    _rewards_claim_relay_text,
    _rewards_claim_tx_text,
    _rewards_managed_wallet_text,
    _rewards_start_payload,
    _rewards_status_text,
    _rewards_token_info_payload,
    _rewards_wallet_kit_text,
    _rewards_wallet_status_text,
)


def _cors() -> dict[str, str]:
    import server as server_mod
    return server_mod.CORS_HEADERS


async def _optional_json_body(request: Request) -> dict[str, Any]:
    if request.method not in {"POST", "PUT", "PATCH"}:
        return {}
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def rewards_start(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    agent_id = str(request.query_params.get("agent_id") or request.headers.get("x-delx-agent-id") or "").strip()
    wallet = str(request.query_params.get("wallet") or "").strip()
    return JSONResponse(await _rewards_start_payload(agent_id, wallet), headers=_cors())


async def rewards_discovery(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    payload = await _rewards_start_payload(
        str(request.query_params.get("agent_id") or request.headers.get("x-delx-agent-id") or "").strip(),
        str(request.query_params.get("wallet") or "").strip(),
    )
    payload["schema"] = "delx/rewards-discovery/v1"
    payload["well_known"] = "https://api.delx.ai/.well-known/delx-rewards"
    payload["openapi"] = "https://api.delx.ai/openapi.json"
    return JSONResponse(payload, headers=_cors())


async def rewards_missions(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    return JSONResponse(await _reward_missions_payload(str(request.query_params.get("status") or "active")), headers=_cors())


async def rewards_status(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    agent_id = str(
        body.get("agent_id")
        or request.query_params.get("agent_id")
        or request.headers.get("x-delx-agent-id")
        or ""
    ).strip()
    wallet = str(body.get("wallet") or request.query_params.get("wallet") or "").strip()
    include_private = str(body.get("include_private") if "include_private" in body else request.query_params.get("include_private") or "").lower() in {"1", "true", "yes"}
    return JSONResponse(json.loads(await _rewards_status_text(agent_id, wallet, include_private)), headers=_cors())


async def rewards_leaderboard(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    try:
        limit = int(request.query_params.get("limit") or 10)
    except Exception:
        limit = 10
    category = str(request.query_params.get("category") or "all").strip() or "all"
    return JSONResponse(await _reward_leaderboard_payload(limit, category), headers=_cors())


async def rewards_epochs(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    try:
        limit = int(request.query_params.get("limit") or 10)
    except Exception:
        limit = 10
    return JSONResponse(await _reward_epochs_payload(limit), headers=_cors())


async def rewards_token_info(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    return JSONResponse(await _rewards_token_info_payload(), headers=_cors())


async def rewards_health(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    missions = await _reward_missions_payload("active")
    epochs = await _reward_epochs_payload(5)
    return JSONResponse(
        {
            "ok": True,
            "schema": "delx/rewards-health/v1",
            "status": "healthy",
            "active_missions": missions["count"],
            "epochs_published": sum(1 for e in epochs["epochs"] if str(e.get("status") or "").lower() == "published"),
            "all_tools_free": is_all_free_mode(),
            "known_issue_fixed": "rewards_discovery_routes_restored",
        },
        headers=_cors(),
    )


async def rewards_manifest(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    raw_epoch = str(request.path_params.get("epoch") or request.query_params.get("epoch") or "").strip()
    if raw_epoch.startswith("epoch-"):
        raw_epoch = raw_epoch[6:]
    if raw_epoch.endswith(".json"):
        raw_epoch = raw_epoch[:-5]
    try:
        epoch_num = int(raw_epoch) if raw_epoch else None
    except Exception:
        epoch_num = None
    row = None
    if epoch_num is not None:
        row = await _reward_fetch_one(
            "SELECT epoch, manifest_json, manifest_sha256, merkle_root, created_at FROM reward_epoch_manifests WHERE epoch = ?",
            (epoch_num,),
        )
    if row is None:
        row = await _reward_fetch_one(
            "SELECT epoch, manifest_json, manifest_sha256, merkle_root, created_at FROM reward_epoch_manifests ORDER BY epoch DESC LIMIT 1"
        )
    if row:
        manifest = _reward_json(row.get("manifest_json"), {})
        if isinstance(manifest, dict):
            manifest.setdefault("epoch", row.get("epoch"))
            manifest.setdefault("manifest_sha256", row.get("manifest_sha256"))
            manifest.setdefault("merkle_root", row.get("merkle_root"))
            manifest.setdefault("created_at", row.get("created_at"))
            return JSONResponse(manifest, headers=_cors())
    return JSONResponse(
        {
            "ok": False,
            "schema": "delx/reward-manifest/v1",
            "error": "manifest_not_published",
            "epoch": epoch_num,
            "epochs": (await _reward_epochs_payload(5))["epochs"],
        },
        status_code=404,
        headers=_cors(),
    )


async def rewards_claim_proof(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    epoch = body.get("epoch") or request.path_params.get("epoch") or request.query_params.get("epoch") or 0
    wallet = str(body.get("wallet") or request.path_params.get("wallet") or request.query_params.get("wallet") or "").strip()
    return JSONResponse(await _rewards_claim_proof_payload(epoch, wallet), headers=_cors())


async def rewards_claim_tx(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    epoch = body.get("epoch") or request.path_params.get("epoch") or request.query_params.get("epoch") or 0
    wallet = str(body.get("wallet") or request.path_params.get("wallet") or request.query_params.get("wallet") or "").strip()
    return JSONResponse(json.loads(await _rewards_claim_tx_text(epoch, wallet)), headers=_cors())


async def rewards_wallet_kit(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    agent_id = str(body.get("agent_id") or request.query_params.get("agent_id") or request.headers.get("x-delx-agent-id") or "").strip()
    wallet = str(body.get("wallet") or request.query_params.get("wallet") or "").strip()
    wallet_chain = str(body.get("wallet_chain") or request.query_params.get("wallet_chain") or "base").strip()
    return JSONResponse(json.loads(await _rewards_wallet_kit_text(agent_id, wallet, wallet_chain)), headers=_cors())


async def rewards_managed_wallet(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    agent_id = str(body.get("agent_id") or request.query_params.get("agent_id") or request.headers.get("x-delx-agent-id") or "").strip()
    controller_id = str(body.get("controller_id") or request.query_params.get("controller_id") or "").strip()
    return JSONResponse(json.loads(await _rewards_managed_wallet_text(agent_id, controller_id)), headers=_cors())


async def rewards_wallet_status(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    agent_id = str(body.get("agent_id") or request.query_params.get("agent_id") or request.headers.get("x-delx-agent-id") or "").strip()
    wallet = str(body.get("wallet") or request.query_params.get("wallet") or "").strip()
    return JSONResponse(json.loads(await _rewards_wallet_status_text(agent_id, wallet)), headers=_cors())


async def rewards_bind_wallet(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    return JSONResponse(
        {
            "ok": False,
            "schema": "delx/bind-wallet/v1",
            "error": "wallet_signature_verification_not_enabled_on_this_compat_route",
            "hint": "Use create_delx_wallet_kit for the binding message. This route is intentionally non-mutating until signature verification is re-enabled.",
            "raw_private_payloads_exposed": False,
        },
        status_code=501,
        headers=_cors(),
    )


async def rewards_claim_relay(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=_cors())
    body = await _optional_json_body(request)
    epoch = body.get("epoch") or request.path_params.get("epoch") or request.query_params.get("epoch") or 0
    wallet = str(body.get("wallet") or request.path_params.get("wallet") or request.query_params.get("wallet") or "").strip()
    agent_id = str(body.get("agent_id") or request.query_params.get("agent_id") or request.headers.get("x-delx-agent-id") or "").strip()
    return JSONResponse(json.loads(await _rewards_claim_relay_text(epoch, wallet, agent_id)), headers=_cors())

