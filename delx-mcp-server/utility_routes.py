"""REST transport helpers for Delx Agent Utilities.

The functions here are intentionally transport-oriented and avoid importing
server state. Business rules remain in utility_monetization, utility_registry,
and utility_product_catalog.
"""

from __future__ import annotations

from typing import Any

from config import get_tool_pricing_payload
from utility_monetization import (
    get_metered_utility_pricing_payload,
    is_utility_charge_candidate,
    utility_charge_headers,
    utility_charge_policy,
)
from utility_product_catalog import utility_product_for_tool
from utility_registry import (
    utility_schema_for_tool,
    utility_slug_for_tool,
)


async def parse_utility_request_args(request: Any) -> dict[str, Any]:
    if str(getattr(request, "method", "")).upper() == "GET":
        args: dict[str, Any] = dict(getattr(request, "query_params", {}) or {})
    else:
        try:
            parsed = await request.json()
        except Exception:
            parsed = {}
        args = parsed if isinstance(parsed, dict) else {}
    for int_key in ("code", "count", "timeout"):
        if int_key in args:
            try:
                args[int_key] = int(args[int_key])
            except (TypeError, ValueError):
                pass
    return args


def utility_pricing_payload(tool_name: str, first_seen_at: str | None = None) -> dict[str, object]:
    policy = utility_charge_policy()
    if is_utility_charge_candidate(tool_name) and policy.get("mode") in {"shadow", "enforce"}:
        return get_metered_utility_pricing_payload(tool_name)
    return get_tool_pricing_payload(
        str(tool_name),
        first_seen_at=first_seen_at,
        grandfathered=None,
    )


def utility_price_usdc(product: dict[str, Any] | None, pricing_payload: dict[str, object]) -> str:
    if product:
        price = product.get("price") or {}
        amount = price.get("amount")
        if amount is not None:
            return str(amount)
    return str(pricing_payload.get("price_usdc") or "0.00")


def utility_product_is_paid(product: dict[str, Any] | None) -> bool:
    return bool(product and int((product.get("price") or {}).get("amount_cents") or 0) > 0)


def utility_product_charge_enabled(
    tool_name: str,
    product: dict[str, Any] | None,
    charge_policy: dict[str, Any],
) -> bool:
    if utility_product_is_paid(product) and charge_policy.get("mode") in {"shadow", "enforce"}:
        return True
    return is_utility_charge_candidate(tool_name) and charge_policy.get("mode") in {"shadow", "enforce"}


def utility_product_shadow_only(
    tool_name: str,
    product: dict[str, Any] | None,
    charge_policy: dict[str, Any],
) -> bool:
    if not utility_product_charge_enabled(tool_name, product, charge_policy):
        return False
    return charge_policy.get("mode") == "shadow"


def utility_rest_headers(
    tool_name: str,
    pricing_payload: dict[str, object],
    *,
    cors_headers: dict[str, str],
) -> dict[str, str]:
    headers = dict(cors_headers)
    headers.update(utility_charge_headers(tool_name, dict(pricing_payload)))
    charge_policy = utility_charge_policy()
    product = utility_product_for_tool(tool_name, charge_policy)
    if utility_product_charge_enabled(tool_name, product, charge_policy):
        headers.update(
            {
                "x-delx-utility-charge-mode": str(charge_policy.get("mode") or "off"),
                "x-delx-utility-paid-candidate": "true",
                "x-delx-utility-price-usdc": utility_price_usdc(product, pricing_payload),
            }
        )
    return headers


def utility_missing_required_payload(
    *,
    tool_name: str,
    request: Any,
    missing: list[str],
    pricing_payload: dict[str, object],
    compatibility_route: bool,
) -> dict[str, Any]:
    slug = utility_slug_for_tool(tool_name)
    schema = utility_schema_for_tool(tool_name)
    canonical_endpoint = f"https://api.delx.ai/api/v1/utilities/{slug}"
    legacy_endpoint = f"https://api.delx.ai/api/v1/x402/{slug}"
    charge_policy = utility_charge_policy()
    product = utility_product_for_tool(tool_name, charge_policy)
    path = str(getattr(getattr(request, "url", None), "path", "") or "")
    payload: dict[str, Any] = {
        "ok": False,
        "tool_name": tool_name,
        "surface": "delx-agent-utilities",
        "status": "missing_required_input",
        "code": "DELX-UTIL-1001",
        "missing": missing,
        "required": missing,
        "schema": schema.get("inputSchema") or {},
        "schema_url": f"https://api.delx.ai/api/v1/tools/schema/{tool_name}",
        "canonical_endpoint": canonical_endpoint,
        "legacy_endpoint": legacy_endpoint,
        "compatibility_route": bool(compatibility_route),
        "hint": f"Pass {', '.join(missing)} as query parameters or JSON body and retry the same endpoint.",
        "example": {
            "get": f"{canonical_endpoint}?{missing[0]}=https://example.com" if missing else canonical_endpoint,
            "post": {"url" if "url" in missing else missing[0] if missing else "input": "https://example.com"},
        },
        "monetization": {
            "mode": charge_policy.get("mode"),
            "paid_candidate": utility_product_is_paid(product),
            "charge_enabled": utility_product_charge_enabled(tool_name, product, charge_policy),
            "price_usdc": utility_price_usdc(product, pricing_payload),
            "protocol_note": "Delx Protocol remains free; only selected stateless utilities are candidates for metering.",
        },
        "legacy_note": "The old x402 URL is now a compatibility route for Delx Agent Utilities, not a Protocol adoption signal.",
        "requested_path": path,
    }
    if product:
        payload["product"] = product
    return payload
