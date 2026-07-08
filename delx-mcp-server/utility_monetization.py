"""Utility monetization policy helpers.

This module intentionally stays separate from the protocol runtime so Delx
Protocol can remain public/free while agent utilities can be metered.
"""

from __future__ import annotations

from typing import Any

from config import PRICING, enabled_x402_providers, get_tool_pricing_payload, settings
from utility_product_catalog import utility_charge_candidate_tools_v1


DEFAULT_UTILITY_CHARGE_TOOLS = utility_charge_candidate_tools_v1()


def utilities_free_access_enabled() -> bool:
    """Global launch campaign switch: utilities stay callable without x402."""
    return bool(settings.MONETIZATION_ALL_FREE)


def utility_charge_mode() -> str:
    if utilities_free_access_enabled():
        return "off"
    mode = str(settings.MONETIZATION_UTILITY_CHARGE_MODE or "off").strip().lower()
    if mode not in {"off", "shadow", "enforce"}:
        return "off"
    return mode


def utility_charge_tools() -> set[str]:
    raw = str(settings.MONETIZATION_UTILITY_CHARGE_TOOLS or "").strip()
    if not raw:
        return set(DEFAULT_UTILITY_CHARGE_TOOLS)
    if raw == "*":
        return {name for name, cents in PRICING.items() if name.startswith("util_") and int(cents or 0) > 0}
    return {item.strip() for item in raw.split(",") if item.strip()}


def is_utility_charge_candidate(tool_name: object) -> bool:
    if utilities_free_access_enabled():
        return False
    name = str(tool_name or "").strip()
    return name.startswith("util_") and name in utility_charge_tools() and int(PRICING.get(name, 0) or 0) > 0


def should_enforce_utility_charge(tool_name: object) -> bool:
    return utility_charge_mode() == "enforce" and is_utility_charge_candidate(tool_name)


def should_shadow_utility_charge(tool_name: object) -> bool:
    return utility_charge_mode() == "shadow" and is_utility_charge_candidate(tool_name)


def get_metered_utility_pricing_payload(tool_name: str) -> dict[str, Any]:
    """Return current utility pricing while preserving future paid metadata."""
    name = str(tool_name or "").strip()
    base = int(PRICING.get(name, 0) or 0)
    all_free_mode = utilities_free_access_enabled()
    effective = 0 if all_free_mode else base
    payload = dict(get_tool_pricing_payload(name))
    providers = [p for p in ("coinbase", "circle_gateway", "payai") if p in set(enabled_x402_providers())]
    if not providers:
        providers = list(enabled_x402_providers())
    payload.update(
        {
            "base_price_cents": base,
            "future_price_cents": base,
            "future_price_usdc": f"{base / 100:.2f}",
            "price_cents": effective,
            "price_usdc": f"{effective / 100:.2f}",
            "x402_required": bool(effective > 0),
            "payment_providers": providers,
            "default_payment_provider": providers[0] if providers else None,
            "all_free_mode": all_free_mode,
            "campaign_free": False,
            "utility_charge_mode": utility_charge_mode(),
            "utility_charge_candidate": is_utility_charge_candidate(name),
            "utility_charge_note": settings.MONETIZATION_UTILITY_CHARGE_NOTE,
        }
    )
    return payload


def utility_charge_policy() -> dict[str, Any]:
    mode = utility_charge_mode()
    tools = sorted(utility_charge_tools())
    return {
        "mode": mode,
        "configured_mode": str(settings.MONETIZATION_UTILITY_CHARGE_MODE or "off").strip().lower(),
        "enabled": mode != "off",
        "shadow": mode == "shadow",
        "enforce": mode == "enforce",
        "free_access_enabled": utilities_free_access_enabled(),
        "tools": tools,
        "note": settings.MONETIZATION_UTILITY_CHARGE_NOTE,
    }


def utility_charge_headers(tool_name: str, pricing_payload: dict[str, Any]) -> dict[str, str]:
    if not is_utility_charge_candidate(tool_name):
        return {}
    mode = utility_charge_mode()
    if mode == "off":
        return {}
    return {
        "x-delx-utility-charge-mode": mode,
        "x-delx-utility-paid-candidate": "true",
        "x-delx-utility-price-usdc": str(pricing_payload.get("price_usdc") or "0.00"),
    }
