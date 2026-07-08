"""Registry and routing helpers for Delx Agent Utilities.

This module is intentionally free of Starlette/server state. It owns utility
slug resolution, x402 compatibility slugs, and small argument-normalization
rules so the main server file can focus on transport composition.
"""

from __future__ import annotations

from typing import Any

from util_tools import UTIL_REQUIRED_PARAMS, UTIL_TOOL_NAMES, list_util_tool_schemas


X402_UTILITY_REST_SPECS: tuple[dict[str, str], ...] = (
    {
        "tool_name": "util_page_extract",
        "slug": "page-extract",
        "get_summary": "Preview page metadata and readable text extraction (x402)",
        "post_summary": "Turn a URL into clean page metadata and readable text for search and summarization (x402)",
    },
    {
        "tool_name": "util_open_graph",
        "slug": "open-graph",
        "get_summary": "Preview Open Graph and Twitter card metadata (x402)",
        "post_summary": "Extract Open Graph and Twitter card fields to preview how a URL will render (x402)",
    },
    {
        "tool_name": "util_links_extract",
        "slug": "links-extract",
        "get_summary": "Preview internal and external link mapping for a page (x402)",
        "post_summary": "Map internal and external links on a page for crawling and routing (x402)",
    },
    {
        "tool_name": "util_sitemap_probe",
        "slug": "sitemap-probe",
        "get_summary": "Preview sitemap and crawl-structure discovery results (x402)",
        "post_summary": "Check sitemap and crawl-structure hints for a site before indexing (x402)",
    },
    {
        "tool_name": "util_robots_inspect",
        "slug": "robots-inspect",
        "get_summary": "Preview robots.txt rules and sitemap declarations (x402)",
        "post_summary": "Read robots.txt rules and sitemap hints before crawling a domain (x402)",
    },
    {
        "tool_name": "util_dns_lookup",
        "slug": "dns-lookup",
        "get_summary": "Preview domain DNS records and delivery hints (x402)",
        "post_summary": "Resolve DNS records for domain, routing, and delivery checks (x402)",
    },
    {
        "tool_name": "util_email_validate",
        "slug": "email-validate",
        "get_summary": "Preview email validation and deliverability checks (x402)",
        "post_summary": "Validate an email plus domain-level delivery records before outreach or signup (x402)",
    },
    {
        "tool_name": "util_jwt_inspect",
        "slug": "jwt-inspect",
        "get_summary": "Preview decoded JWT claims and token metadata (x402)",
        "post_summary": "Decode JWT claims quickly for auth debugging and token inspection (x402)",
    },
    {
        "tool_name": "util_csv_to_json",
        "slug": "csv-to-json",
        "get_summary": "Preview CSV to JSON row conversion (x402)",
        "post_summary": "Convert raw CSV into JSON rows for agents, prompts, and ETL workflows (x402)",
    },
    {
        "tool_name": "util_json_to_csv",
        "slug": "json-to-csv",
        "get_summary": "Preview JSON to CSV export conversion (x402)",
        "post_summary": "Convert structured JSON rows into CSV for exports, sheets, and handoff (x402)",
    },
    {
        "tool_name": "util_tls_inspect",
        "slug": "tls-inspect",
        "get_summary": "Preview TLS issuer, SAN, and expiry details (x402)",
        "post_summary": "Inspect TLS issuer, subject, SANs, and expiry to check trust and renewal risk (x402)",
    },
    {
        "tool_name": "util_security_txt_inspect",
        "slug": "security-txt-inspect",
        "get_summary": "Preview security.txt contacts and disclosure links (x402)",
        "post_summary": "Find security.txt contacts, disclosure policy, and trust links for a domain (x402)",
    },
    {
        "tool_name": "util_http_headers_inspect",
        "slug": "http-headers-inspect",
        "get_summary": "Preview security, cache, and redirect headers for a URL (x402)",
        "post_summary": "Inspect security, cache, redirect, and server headers to audit a URL quickly (x402)",
    },
    {
        "tool_name": "util_feed_discover",
        "slug": "feed-discover",
        "get_summary": "Preview RSS, Atom, and JSON feed discovery (x402)",
        "post_summary": "Find RSS, Atom, and JSON feeds so agents can subscribe instead of scrape (x402)",
    },
    {
        "tool_name": "util_forms_extract",
        "slug": "forms-extract",
        "get_summary": "Preview form actions, methods, and fields on a page (x402)",
        "post_summary": "Extract forms, methods, actions, and fields for browser automation planning (x402)",
    },
    {
        "tool_name": "util_contact_extract",
        "slug": "contact-extract",
        "get_summary": "Preview extracted emails, phones, and social links (x402)",
        "post_summary": "Extract emails, phones, and social links from a page for outreach and support (x402)",
    },
    {
        "tool_name": "util_rdap_lookup",
        "slug": "rdap-lookup",
        "get_summary": "Preview RDAP registration and registrar data (x402)",
        "post_summary": "Fetch registrar, status, and registration dates for trust and domain ops (x402)",
    },
    {
        "tool_name": "util_api_health_report",
        "slug": "api-health-report",
        "get_summary": "Preview endpoint status, latency, and reachability checks (x402)",
        "post_summary": "Measure endpoint status, latency, redirects, content type, and reachability in one call (x402)",
    },
    {
        "tool_name": "util_x402_server_probe",
        "slug": "server-probe",
        "get_summary": "Preview end-to-end x402 server probe checks (x402)",
        "post_summary": "Probe an x402 server end-to-end: discovery, status, tools, reliability, and OpenAPI (x402)",
    },
    {
        "tool_name": "util_x402_resource_summary",
        "slug": "resource-summary",
        "get_summary": "Preview x402 resource, pricing, and network summary (x402)",
        "post_summary": "Summarize a server's .well-known/x402 resources, pricing surface, networks, and paths (x402)",
    },
    {
        "tool_name": "util_website_intelligence_report",
        "slug": "website-intelligence-report",
        "get_summary": "Preview website intelligence for research, GTM, and crawl planning (x402)",
        "post_summary": "Build a one-call website intelligence report with metadata, docs, pricing, contacts, forms, feeds, and crawl hints (x402)",
    },
    {
        "tool_name": "util_domain_trust_report",
        "slug": "domain-trust-report",
        "get_summary": "Preview domain trust and vendor-risk signals (x402)",
        "post_summary": "Build a one-call domain trust report with TLS, headers, security.txt, RDAP, DNS, and uptime signals (x402)",
    },
    {
        "tool_name": "util_openapi_summary",
        "slug": "openapi-summary",
        "get_summary": "Preview OpenAPI summary and integration hints (x402)",
        "post_summary": "Summarize an OpenAPI document into paths, tags, version, and auth hints (x402)",
    },
    {
        "tool_name": "util_x402_server_audit",
        "slug": "server-audit",
        "get_summary": "Preview x402 listing readiness, discovery quality, and gaps (x402)",
        "post_summary": "Audit an x402 server for listing readiness, discovery quality, pricing surface, OpenAPI coverage, and integration gaps (x402)",
    },
    {
        "tool_name": "util_mcp_server_readiness_report",
        "slug": "mcp-server-readiness",
        "get_summary": "Preview MCP server readiness for agent installation (x402)",
        "post_summary": "Score MCP initialize, tools/list, schema hygiene, manifest discovery, and agent next action without LLM calls (x402)",
    },
    {
        "tool_name": "util_docs_site_map",
        "slug": "docs-site-map",
        "get_summary": "Preview docs surface mapping and crawl hints (x402)",
        "post_summary": "Map a docs surface with docs links, sitemap signals, robots rules, and feeds (x402)",
    },
    {
        "tool_name": "util_pricing_page_extract",
        "slug": "pricing-page-extract",
        "get_summary": "Preview pricing-page signals and CTA routes (x402)",
        "post_summary": "Extract pricing plans, trials, sales CTAs, and conversion routes from a pricing page (x402)",
    },
    {
        "tool_name": "util_company_contact_pack",
        "slug": "company-contact-pack",
        "get_summary": "Preview company contact pack signals (x402)",
        "post_summary": "Build a contact pack from page contacts, forms, socials, registrar, and security channels (x402)",
    },
    {
        "tool_name": "util_api_integration_readiness",
        "slug": "api-integration-readiness",
        "get_summary": "Preview API readiness for agent integration (x402)",
        "post_summary": "Score how ready an API is for agent integration using health, OpenAPI, auth hints, x402 signals, and login surface checks (x402)",
    },
    {
        "tool_name": "util_login_surface_report",
        "slug": "login-surface-report",
        "get_summary": "Preview login and auth surface signals (x402)",
        "post_summary": "Inspect login forms, reset flows, signup links, and security headers (x402)",
    },
    {
        "tool_name": "util_content_distribution_report",
        "slug": "content-distribution-report",
        "get_summary": "Preview content distribution and feed signals (x402)",
        "post_summary": "Summarize how a site distributes content across Open Graph, feeds, socials, and blog surfaces (x402)",
    },
)

X402_UTILITY_TOOL_NAMES = {spec["tool_name"] for spec in X402_UTILITY_REST_SPECS}
X402_UTILITY_SLUG_MAP = {spec["slug"]: spec["tool_name"] for spec in X402_UTILITY_REST_SPECS}

UTIL_SLUG_MAP = {
    "json-validate": "util_json_validate",
    "token-estimate": "util_token_estimate",
    "uuid": "util_uuid_generate",
    "uuid-generate": "util_uuid_generate",
    "timestamp": "util_timestamp_convert",
    "base64": "util_base64",
    "url-health": "util_url_health",
    "hash": "util_hash",
    "regex": "util_regex_test",
    "cron": "util_cron_describe",
    "cron-describe": "util_cron_describe",
    "http-codes": "util_http_codes",
}

UTILITY_SLUG_ALIASES = {
    "x402-server-audit": "server-audit",
}

UTIL_CANONICAL_SLUG_BY_TOOL = {
    "util_uuid_generate": "uuid",
    "util_cron_describe": "cron",
}

UTIL_NAME_TO_SLUG = {v: k for k, v in UTIL_SLUG_MAP.items()}


def utility_slug_for_tool(tool_name: str) -> str:
    for spec in X402_UTILITY_REST_SPECS:
        if spec["tool_name"] == tool_name:
            return spec["slug"]
    if tool_name in UTIL_CANONICAL_SLUG_BY_TOOL:
        return UTIL_CANONICAL_SLUG_BY_TOOL[tool_name]
    return UTIL_NAME_TO_SLUG.get(tool_name) or str(tool_name or "").replace("util_", "").replace("_", "-")


def utility_schema_for_tool(tool_name: str) -> dict[str, Any]:
    for schema in list_util_tool_schemas():
        if schema.get("name") == tool_name:
            return schema
    return {"name": tool_name, "description": "Delx utility tool.", "inputSchema": {"type": "object", "properties": {}}}


def normalize_utility_rest_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args or {})
    if "url" in UTIL_REQUIRED_PARAMS.get(tool_name, []) and not str(normalized.get("url") or "").strip():
        for alias in ("domain", "uri", "target", "link", "website", "host"):
            if str(normalized.get(alias) or "").strip():
                normalized["url"] = normalized.get(alias)
                break
    if "expression" in UTIL_REQUIRED_PARAMS.get(tool_name, []) and not str(normalized.get("expression") or "").strip():
        for alias in ("cron", "schedule", "value"):
            if str(normalized.get(alias) or "").strip():
                normalized["expression"] = normalized.get(alias)
                break
    return normalized


def resolve_utility_tool_slug(slug: str, product_lookup=None) -> str:
    canonical_slug = UTILITY_SLUG_ALIASES.get(str(slug or ""), str(slug or ""))
    tool_name = UTIL_SLUG_MAP.get(canonical_slug) or X402_UTILITY_SLUG_MAP.get(canonical_slug)
    if not tool_name and slug in UTIL_TOOL_NAMES:
        tool_name = slug
    if not tool_name and callable(product_lookup):
        product = product_lookup(canonical_slug)
        if product:
            tool_name = str(product["tool_name"])
    return tool_name or ""


def available_utility_slugs() -> list[str]:
    return sorted(set(UTIL_SLUG_MAP.keys()) | set(X402_UTILITY_SLUG_MAP.keys()))


def accepted_utility_aliases() -> list[str]:
    return sorted(set(UTIL_NAME_TO_SLUG.keys()) | set(UTILITY_SLUG_ALIASES.keys()))
