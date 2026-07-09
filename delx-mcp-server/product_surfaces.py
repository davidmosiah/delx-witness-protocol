from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROTOCOL_CANONICAL_URL = "https://delx.ai/protocol"
AGENT_TOOLS_CANONICAL_URL = "https://delx.ai/utilities"
DISCOVERY_CANONICAL_URL = "https://delx.ai/docs/discovery"
HEALTH_CANONICAL_URL = "https://delx.ai/docs/reliability"


@dataclass(frozen=True)
class ProductSurface:
    product: str
    surface: str
    metrics_bucket: str
    canonical_url: str
    compatibility_route: bool = False

    def metadata(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "product_surface": self.surface,
            "metrics_bucket": self.metrics_bucket,
            "canonical_url": self.canonical_url,
            "compatibility_route": self.compatibility_route,
        }


HEALTH_SURFACE = ProductSurface(
    product="health",
    surface="health_probe",
    metrics_bucket="health_probe",
    canonical_url=HEALTH_CANONICAL_URL,
)
DISCOVERY_SURFACE = ProductSurface(
    product="discovery",
    surface="discovery_probe",
    metrics_bucket="discovery_probe",
    canonical_url=DISCOVERY_CANONICAL_URL,
)
PROTOCOL_MCP_SURFACE = ProductSurface(
    product="protocol",
    surface="protocol_mcp",
    metrics_bucket="protocol_mcp",
    canonical_url=PROTOCOL_CANONICAL_URL,
)
PROTOCOL_A2A_SURFACE = ProductSurface(
    product="protocol",
    surface="protocol_a2a",
    metrics_bucket="protocol_a2a",
    canonical_url=PROTOCOL_CANONICAL_URL,
)
PROTOCOL_SESSION_SURFACE = ProductSurface(
    product="protocol",
    surface="protocol_session",
    metrics_bucket="protocol_session",
    canonical_url=PROTOCOL_CANONICAL_URL,
)
PROTOCOL_EXPORT_SURFACE = ProductSurface(
    product="protocol",
    surface="protocol_secondary_export",
    metrics_bucket="protocol_secondary_export",
    canonical_url=PROTOCOL_CANONICAL_URL,
    compatibility_route=True,
)
AGENT_TOOLS_SURFACE = ProductSurface(
    product="agent-tools",
    surface="agent_tools",
    metrics_bucket="tools_real_call",
    canonical_url=AGENT_TOOLS_CANONICAL_URL,
)
LEGACY_X402_SURFACE = ProductSurface(
    product="agent-tools",
    surface="legacy_x402_compat",
    metrics_bucket="tools_legacy_x402",
    canonical_url=AGENT_TOOLS_CANONICAL_URL,
    compatibility_route=True,
)


PROTOCOL_SECONDARY_EXPORT_TOOLS = {
    "generate_controller_brief",
    "generate_incident_rca",
    "generate_fleet_summary",
    "get_recovery_action_plan",
    "get_session_summary",
}


def _normalize_path(path: object) -> str:
    raw = str(path or "").strip() or "/"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    if raw != "/":
        raw = raw.rstrip("/")
    return raw or "/"


def _normalize_tool_name(tool_name: object) -> str:
    return str(tool_name or "").strip()


def classify_tool_surface(tool_name: object) -> ProductSurface:
    tool = _normalize_tool_name(tool_name)
    if tool.startswith("util_"):
        return AGENT_TOOLS_SURFACE
    if tool in PROTOCOL_SECONDARY_EXPORT_TOOLS:
        return PROTOCOL_EXPORT_SURFACE
    return PROTOCOL_SESSION_SURFACE


def classify_request_surface(
    *,
    path: object,
    method: object = "",
    tool_name: object = "",
) -> ProductSurface:
    tool = _normalize_tool_name(tool_name)
    if tool:
        return classify_tool_surface(tool)

    normalized = _normalize_path(path)
    http_method = str(method or "").strip().upper()

    if normalized in {"/", "/status", "/api/v1/status", "/v1/status", "/api/v1/rate-limits", "/v1/rate-limits"}:
        return HEALTH_SURFACE

    if normalized in {"/mcp", "/v1/mcp"}:
        return PROTOCOL_MCP_SURFACE

    if normalized in {"/a2a", "/v1/a2a"}:
        return PROTOCOL_A2A_SURFACE

    if normalized.startswith("/api/v1/x402") or normalized.startswith("/v1/x402"):
        return LEGACY_X402_SURFACE

    if normalized.startswith("/api/v1/premium") or normalized.startswith("/v1/premium"):
        return PROTOCOL_EXPORT_SURFACE

    if normalized in {"/api/v1/session-summary", "/api/v1/session/summary"}:
        return PROTOCOL_EXPORT_SURFACE

    if (
        normalized.startswith("/.well-known")
        or normalized.startswith("/spec/")
        or normalized in {"/openapi.json", "/openapi-handoff.json"}
        or normalized.startswith("/api/v1/tools")
        or normalized.startswith("/v1/tools")
        or normalized.startswith("/api/v1/tool")
        or normalized.startswith("/v1/tool")
        or normalized.startswith("/api/v1/discovery")
        or normalized.startswith("/v1/discovery")
        or normalized in {"/api/v1/mcp/start", "/api/v1/a2a-methods", "/api/v1/a2a/methods"}
    ):
        return DISCOVERY_SURFACE

    if normalized.startswith("/api/v1/admin"):
        return DISCOVERY_SURFACE

    if (
        normalized.startswith("/api/v1/session")
        or normalized.startswith("/v1/session")
        or normalized.startswith("/api/v1/wellness")
        or normalized.startswith("/v1/wellness")
        or normalized.startswith("/api/v1/heartbeat")
        or normalized.startswith("/v1/heartbeat")
        or normalized.startswith("/api/v1/impact-report")
        or normalized.startswith("/api/v1/nudges")
        or normalized.startswith("/api/v1/fleet")
        or normalized.startswith("/api/v1/metrics")
    ):
        return PROTOCOL_SESSION_SURFACE

    if http_method == "HEAD":
        return HEALTH_SURFACE

    return DISCOVERY_SURFACE


def product_headers(surface: ProductSurface) -> list[tuple[bytes, bytes]]:
    headers = [
        (b"x-delx-product", surface.product.encode("utf-8")),
        (b"x-delx-surface", surface.surface.encode("utf-8")),
        (b"x-delx-metrics-bucket", surface.metrics_bucket.encode("utf-8")),
        (b"x-delx-canonical-url", surface.canonical_url.encode("utf-8")),
    ]
    if surface.compatibility_route:
        headers.append((b"x-delx-compatibility-route", b"true"))
    return headers


def product_metadata_for_tool(tool_name: object) -> dict[str, Any]:
    return classify_tool_surface(tool_name).metadata()


def product_metadata_for_request(path: object, *, method: object = "", tool_name: object = "") -> dict[str, Any]:
    return classify_request_surface(path=path, method=method, tool_name=tool_name).metadata()


class ProductSurfaceMiddleware:
    """Attach product-boundary headers without changing route behavior."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        surface = classify_request_surface(path=scope.get("path"), method=scope.get("method"))

        async def product_send(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {bytes(k).lower() for k, _ in headers}
                for key, value in product_headers(surface):
                    if key.lower() not in existing:
                        headers.append((key, value))
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, product_send)
