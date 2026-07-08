"""Product catalog for Delx Agent Utilities.

This stays separate from the protocol runtime so paid utility packaging does
not leak into the free witness protocol surface.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from config import PRICING, settings


UTILITY_PRODUCT_CATALOG_VERSION = "2026-04-25"
UTILITY_PRODUCT_CURRENCY = "USDC"


UTILITY_PRODUCT_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "product_id": "website_intelligence_report",
        "tool_name": "util_website_intelligence_report",
        "slug": "website-intelligence-report",
        "title": "Website Intelligence Report",
        "category": "web_intelligence",
        "agent_job": "Understand whether a website is useful, trustworthy, and agent-readable before deeper crawling.",
        "description": "One-call website summary covering metadata, links, forms, contact hints, feeds, robots, sitemap, and agent-readiness signals.",
        "use_when": [
            "An agent discovers a new domain and needs a structured first-pass report.",
            "A registry or crawler wants one response instead of many small probes.",
            "A buyer agent needs to decide whether a site is worth deeper inspection.",
        ],
        "avoid_when": [
            "You only need a cheap DNS or HTTP status check.",
            "The caller cannot provide a public URL.",
        ],
        "input_example": {"url": "https://delx.ai", "timeout": 8},
        "required_params": ["url"],
        "output_highlights": [
            "page metadata and OpenGraph signals",
            "important links and forms",
            "robots and sitemap hints",
            "agent-readable summary",
        ],
        "success_criteria": [
            "Returns enough structured context for an agent to choose its next crawl step.",
            "Clearly separates missing content from failed network access.",
        ],
        "latency_target_ms": 8000,
        "cache_policy": "Safe to cache per URL for 15-60 minutes unless the caller needs freshness.",
    },
    {
        "product_id": "domain_trust_report",
        "tool_name": "util_domain_trust_report",
        "slug": "domain-trust-report",
        "title": "Domain Trust Report",
        "category": "trust_risk",
        "agent_job": "Decide whether a domain looks safe enough for an agent to browse, cite, or transact with.",
        "description": "Trust-oriented domain report combining DNS, RDAP, TLS, security.txt, headers, robots, sitemap, and URL health signals.",
        "use_when": [
            "An agent is about to rely on a domain it does not know.",
            "A registry wants risk signals before listing an endpoint.",
            "A commerce agent needs a basic trust check before payment or contact extraction.",
        ],
        "avoid_when": [
            "You already trust the domain and only need content extraction.",
            "The domain is internal/private and not reachable from the public internet.",
        ],
        "input_example": {"url": "https://delx.ai", "timeout": 8},
        "required_params": ["url"],
        "output_highlights": [
            "DNS and RDAP summary",
            "TLS and security.txt signals",
            "headers and URL health",
            "trust/risk notes for agents",
        ],
        "success_criteria": [
            "Makes trust uncertainty explicit instead of pretending to certify safety.",
            "Gives agents enough evidence to proceed, defer, or ask for human review.",
        ],
        "latency_target_ms": 9000,
        "cache_policy": "Safe to cache per URL/domain for 30-120 minutes.",
    },
    {
        "product_id": "mcp_server_readiness_report",
        "tool_name": "util_mcp_server_readiness_report",
        "slug": "mcp-server-readiness",
        "title": "MCP Server Readiness Report",
        "category": "agent_infrastructure",
        "agent_job": "Decide whether an MCP server is safe and usable enough for agents before installation or payment.",
        "description": "Deterministic MCP readiness report for initialize, tools/list, schema hygiene, manifest discovery, and agent next action.",
        "use_when": [
            "An agent is about to install or call an unknown MCP server.",
            "A marketplace wants a repeatable readiness score before listing.",
            "An operator needs concrete schema/name/description fixes before distribution.",
        ],
        "avoid_when": [
            "The target is stdio-only with no HTTP-accessible MCP endpoint.",
            "The caller wants subjective product review rather than protocol readiness.",
        ],
        "input_example": {"url": "https://api.delx.ai", "timeout": 8},
        "required_params": ["url"],
        "output_highlights": [
            "MCP initialize and tools/list checks",
            "tool schema and argument description hygiene",
            "agent-safe verdict and next action",
            "manifest discovery signal",
        ],
        "success_criteria": [
            "Returns a deterministic verdict without LLM calls.",
            "Names actionable blockers before an agent depends on the MCP server.",
        ],
        "latency_target_ms": 8000,
        "cache_policy": "Safe to cache per MCP origin for 15-60 minutes.",
    },
    {
        "product_id": "api_integration_readiness",
        "tool_name": "util_api_integration_readiness",
        "slug": "api-integration-readiness",
        "title": "API Integration Readiness",
        "category": "api_readiness",
        "agent_job": "Judge whether an API is easy and safe for an agent runtime to integrate.",
        "description": "Integration readiness report for docs, OpenAPI, authentication hints, pricing, contact, and agent-facing setup signals.",
        "use_when": [
            "An agent needs to pick between multiple API vendors.",
            "A marketplace wants to score whether an API is ready for autonomous clients.",
            "An operator wants a prioritized checklist before publishing an API to agents.",
        ],
        "avoid_when": [
            "You only need to validate one OpenAPI JSON document.",
            "The API docs are behind login and no public URL is available.",
        ],
        "input_example": {"url": "https://delx.ai/docs", "timeout": 8},
        "required_params": ["url"],
        "output_highlights": [
            "OpenAPI and docs discovery",
            "auth and pricing hints",
            "contact/support signals",
            "integration risk notes",
        ],
        "success_criteria": [
            "Produces an actionable readiness summary, not just raw crawler output.",
            "Names missing integration blockers clearly.",
        ],
        "latency_target_ms": 10000,
        "cache_policy": "Safe to cache per docs URL for 30-120 minutes.",
    },
    {
        "product_id": "x402_server_audit",
        "tool_name": "util_x402_server_audit",
        "slug": "server-audit",
        "title": "x402 Server Audit",
        "category": "agent_commerce",
        "agent_job": "Check whether a paid agent endpoint exposes usable x402 discovery and payment requirements.",
        "description": "Agent-commerce audit for x402 payment readiness, resource discovery, response headers, and integration hints.",
        "use_when": [
            "A server claims x402 support and an agent needs to verify it.",
            "A marketplace wants to validate paid resources before listing.",
            "An operator is debugging why x402 scanners do not detect their API.",
        ],
        "avoid_when": [
            "The target has no paid HTTP resources.",
            "You only need generic API readiness rather than x402-specific evidence.",
        ],
        "input_example": {"url": "https://delx.ai/api", "timeout": 8},
        "required_params": ["url"],
        "output_highlights": [
            "HTTP 402/payment-required checks",
            "x402 resource metadata",
            "scanner compatibility notes",
            "agent next steps",
        ],
        "success_criteria": [
            "Explains whether an agent can discover and satisfy payment requirements.",
            "Points to the exact missing x402 surface when detection fails.",
        ],
        "latency_target_ms": 8000,
        "cache_policy": "Safe to cache per resource URL for 15-60 minutes.",
    },
    {
        "product_id": "company_contact_pack",
        "tool_name": "util_company_contact_pack",
        "slug": "company-contact-pack",
        "title": "Company Contact Pack",
        "category": "gtm_contact",
        "agent_job": "Find structured contact and support paths before an agent escalates, sells, partners, or files a report.",
        "description": "Contact-oriented website report for public email, social, support, security.txt, pricing, and company-facing pages.",
        "use_when": [
            "A sales or support agent needs a contact pack for a company website.",
            "A security agent needs responsible disclosure contact hints.",
            "A marketplace wants support/contact metadata for a listed service.",
        ],
        "avoid_when": [
            "The caller needs private personal data.",
            "The site explicitly blocks automated extraction.",
        ],
        "input_example": {"url": "https://delx.ai", "timeout": 8},
        "required_params": ["url"],
        "output_highlights": [
            "public contact links and emails",
            "support/security/pricing hints",
            "company/social page candidates",
            "structured follow-up targets",
        ],
        "success_criteria": [
            "Uses public site data only.",
            "Gives agents a clear next contact route or explains why none was found.",
        ],
        "latency_target_ms": 9000,
        "cache_policy": "Safe to cache per URL for 30-120 minutes.",
    },
)


def utility_charge_candidate_tools_v1() -> set[str]:
    return {product["tool_name"] for product in UTILITY_PRODUCT_DEFINITIONS}


def utility_product_ids() -> list[str]:
    return [product["product_id"] for product in UTILITY_PRODUCT_DEFINITIONS]


def _price_for_tool(tool_name: str) -> dict[str, Any]:
    future_cents = int(PRICING.get(tool_name, 0) or 0)
    cents = 0 if bool(settings.MONETIZATION_ALL_FREE) else future_cents
    return {
        "amount": f"{cents / 100:.2f}",
        "amount_cents": cents,
        "currency": UTILITY_PRODUCT_CURRENCY,
        "mode": "fixed",
        "future_amount": f"{future_cents / 100:.2f}",
        "future_amount_cents": future_cents,
        "free_access": bool(settings.MONETIZATION_ALL_FREE),
    }


def _product_payload(product: dict[str, Any], charge_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = deepcopy(product)
    slug = payload["slug"]
    tool_name = payload["tool_name"]
    policy = charge_policy or {}
    policy_tools = set(policy.get("tools") or [])
    future_cents = int(PRICING.get(tool_name, 0) or 0)
    cents = 0 if bool(settings.MONETIZATION_ALL_FREE) else future_cents
    policy_selected = tool_name in policy_tools if policy_tools else False

    payload.update(
        {
            "method": "GET_OR_POST",
            "canonical_endpoint": f"https://api.delx.ai/api/v1/utilities/{slug}",
            "x402_endpoint": f"https://api.delx.ai/api/v1/x402/{slug}",
            "schema_url": f"https://api.delx.ai/api/v1/tools/schema/{tool_name}",
            "idempotency": "safe for repeated calls with the same public input",
            "stability": "productized_v1",
            "price": _price_for_tool(tool_name),
            "payment_rails": {
                "primary": "x402",
                "supported": ["x402", "mpp", "circle_gateway_nanopayments"],
                "circle_gateway_nanopayments": {
                    "status": "provider_configurable",
                    "minimum_usdc": "0.000001",
                    "scheme": "exact",
                    "signature": "EIP-3009 TransferWithAuthorization",
                    "batching": "Circle Gateway batched settlement",
                    "docs": "https://developers.circle.com/gateway/nanopayments",
                    "protocol_boundary": "Only Delx Agent Utilities are payment-capable; Delx Protocol remains free.",
                },
            },
            "monetization": {
                "charge_mode": policy.get("mode", "off"),
                "paid_candidate": cents > 0,
                "future_paid_candidate": future_cents > 0,
                "charge_enabled": policy_selected and policy.get("mode") in {"shadow", "enforce"},
                "shadow_only": policy_selected and policy.get("mode") == "shadow",
                "enforce": policy_selected and policy.get("mode") == "enforce",
                "free_access_enabled": bool(settings.MONETIZATION_ALL_FREE),
                "protocol_boundary": "Delx Protocol remains free; only stateless utilities are candidates for charging.",
            },
        }
    )
    return payload


def get_utility_product_catalog(charge_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    products = [_product_payload(product, charge_policy) for product in UTILITY_PRODUCT_DEFINITIONS]
    return {
        "ok": True,
        "surface": "delx-agent-utilities",
        "catalog": "delx-agent-utilities-product-catalog",
        "version": UTILITY_PRODUCT_CATALOG_VERSION,
        "count": len(products),
        "products": products,
        "monetization_rollout": {
            "current_mode": (charge_policy or {}).get("mode", "off"),
            "safe_sequence": ["free", "shadow_pricing", "quota_keys", "x402_enforcement"],
            "principle": "Charge practical stateless utilities without charging Delx Protocol witness sessions.",
        },
    }


def utility_product_for_tool(tool_name: str, charge_policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    for product in UTILITY_PRODUCT_DEFINITIONS:
        if product["tool_name"] == tool_name:
            return _product_payload(product, charge_policy)
    return None


def utility_product_for_slug(slug: str, charge_policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    normalized = str(slug or "").strip()
    for product in UTILITY_PRODUCT_DEFINITIONS:
        if product["slug"] == normalized:
            return _product_payload(product, charge_policy)
    return None
