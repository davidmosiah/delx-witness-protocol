"""Premium REST artifact endpoints (extracted from server.py, move-only).

Each of these is a thin alias over the shared `_premium_artifact_rest` helper,
which remains in server.py (it depends on session/store state and the
in-process `call_tool` dispatcher that Phase 4 will relocate).
"""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse


def _server():
    import server as server_mod
    return server_mod


async def premium_controller_brief_rest(request: Request) -> JSONResponse:
    return await _server()._premium_artifact_rest(request, "generate_controller_brief")


async def premium_recovery_action_plan_rest(request: Request) -> JSONResponse:
    return await _server()._premium_artifact_rest(request, "get_recovery_action_plan")


async def premium_session_summary_rest(request: Request) -> JSONResponse:
    return await _server()._premium_artifact_rest(request, "get_session_summary")


async def premium_incident_rca_rest(request: Request) -> JSONResponse:
    return await _server()._premium_artifact_rest(request, "generate_incident_rca")


async def premium_fleet_summary_rest(request: Request) -> JSONResponse:
    return await _server()._premium_artifact_rest(request, "generate_fleet_summary")
