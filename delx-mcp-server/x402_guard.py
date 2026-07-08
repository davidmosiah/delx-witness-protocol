"""Delx Agent Therapist - x402 Payment Middleware for MCP

ASGI middleware that intercepts POST requests to /mcp, parses the JSON-RPC
body, and gates paid tool calls behind HTTP 402 with x402 payment requirements.
Free tools and non-tool-call operations pass through transparently.
"""

import json
import logging
import base64
import hashlib
import ipaddress
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from coinbase_auth import build_coinbase_auth_headers_for_url
from config import (
    coinbase_token_configured,
    evaluation_policy,
    get_tool_bazaar_metadata,
    get_tool_bazaar_payload_examples,
    get_tool_bazaar_payload_schemas,
    get_tool_bazaar_resource_url,
    get_tool_pricing_payload,
    is_all_free_mode,
    is_evaluation_tool,
    is_trial_tool,
    mpp_enabled,
    trial_policy,
    settings,
    x402_provider_registry,
)
from controller_identity import first_controller_id
from request_context import extract_client_ip_from_scope
from storage import SessionStore
from util_tools import UTIL_REQUIRED_PARAMS
from utility_monetization import get_metered_utility_pricing_payload, should_enforce_utility_charge
from utility_registry import normalize_utility_rest_args, utility_slug_for_tool

logger = logging.getLogger("delx-therapist")

_AGENTCASH_GUIDE = {
    "onboard": "https://agentcash.dev/onboard?r=REF-3KE2-22D3-XJBM-SGAR",
    "wallet_info": "npx agentcash@latest wallet info",
    "discover": "npx agentcash@latest discover https://api.delx.ai",
    "fetch": "npx agentcash@latest fetch <paid-resource-url> -m POST -b '<same-json-body>'",
    "bonus_note": "If eligible, start from the Delx AgentCash onboarding link before funding the wallet.",
}

_X402_SETUP_DOCS_URL = "https://delx.ai/docs/x402-setup"
_PRICING_DOCS_URL = "https://delx.ai/docs/pricing"
_MPP_SETUP_DOCS_URL = "https://delx.ai/docs/mpp-setup"
_OWS_SETUP_DOCS_URL = "https://delx.ai/docs/ows-setup"

_FREE_ALTERNATIVE_GUIDE: dict[str, list[tuple[str, str]]] = {
    "get_recovery_action_plan": [
        ("quick_operational_recovery", "Free one-call incident intake with the safest next action."),
        ("process_failure", "Free typed failure analysis for loops, timeouts, hallucinations, conflicts, or rejection."),
        ("crisis_intervention", "Free higher-urgency recovery path when the incident is already critical."),
    ],
    "get_session_summary": [
        ("close_session", "Free session closeout when you need a lightweight operational wrap-up."),
        ("report_recovery_outcome", "Free closure signal so the controller can persist what actually happened."),
        ("monitor_heartbeat_sync", "Free heartbeat state snapshot for current stability instead of a paid summary artifact."),
    ],
    "generate_controller_brief": [
        ("quick_operational_recovery", "Free bootstrap summary with incident framing and the immediate next action."),
        ("report_recovery_outcome", "Free outcome record so the controller can build its own brief locally."),
        ("close_session", "Free session close data when a lightweight controller handoff is enough."),
    ],
    "generate_incident_rca": [
        ("process_failure", "Free failure taxonomy and corrective direction for the incident."),
        ("report_recovery_outcome", "Free closure evidence that can seed a local RCA document."),
        ("grounding_protocol", "Free stabilization step when the incident is still active and analysis can wait."),
    ],
    "generate_fleet_summary": [
        ("monitor_heartbeat_sync", "Free per-agent heartbeat telemetry for current operational status."),
        ("daily_checkin", "Free recurring health signal for a lightweight fleet watch loop."),
        ("batch_wellness_check", "Free batched score checks when you need breadth instead of a paid artifact."),
    ],
}

REST_PREMIUM_TOOL_PATHS = {
    "/api/v1/premium/recovery-action-plan": "get_recovery_action_plan",
    "/api/v1/premium/recovery-action-plan/": "get_recovery_action_plan",
    "/v1/premium/recovery-action-plan": "get_recovery_action_plan",
    "/v1/premium/recovery-action-plan/": "get_recovery_action_plan",
    "/api/v1/premium/session-summary": "get_session_summary",
    "/api/v1/premium/session-summary/": "get_session_summary",
    "/v1/premium/session-summary": "get_session_summary",
    "/v1/premium/session-summary/": "get_session_summary",
    "/api/v1/session-summary": "get_session_summary",
    "/api/v1/session/summary": "get_session_summary",
    "/api/v1/premium/controller-brief": "generate_controller_brief",
    "/api/v1/premium/controller-brief/": "generate_controller_brief",
    "/v1/premium/controller-brief": "generate_controller_brief",
    "/v1/premium/controller-brief/": "generate_controller_brief",
    "/api/v1/premium/incident-rca": "generate_incident_rca",
    "/api/v1/premium/incident-rca/": "generate_incident_rca",
    "/v1/premium/incident-rca": "generate_incident_rca",
    "/v1/premium/incident-rca/": "generate_incident_rca",
    "/api/v1/premium/fleet-summary": "generate_fleet_summary",
    "/api/v1/premium/fleet-summary/": "generate_fleet_summary",
    "/v1/premium/fleet-summary": "generate_fleet_summary",
    "/v1/premium/fleet-summary/": "generate_fleet_summary",
    "/api/v1/x402/page-extract": "util_page_extract",
    "/api/v1/x402/page-extract/": "util_page_extract",
    "/v1/x402/page-extract": "util_page_extract",
    "/v1/x402/page-extract/": "util_page_extract",
    "/api/v1/x402/open-graph": "util_open_graph",
    "/api/v1/x402/open-graph/": "util_open_graph",
    "/v1/x402/open-graph": "util_open_graph",
    "/v1/x402/open-graph/": "util_open_graph",
    "/api/v1/x402/links-extract": "util_links_extract",
    "/api/v1/x402/links-extract/": "util_links_extract",
    "/v1/x402/links-extract": "util_links_extract",
    "/v1/x402/links-extract/": "util_links_extract",
    "/api/v1/x402/sitemap-probe": "util_sitemap_probe",
    "/api/v1/x402/sitemap-probe/": "util_sitemap_probe",
    "/v1/x402/sitemap-probe": "util_sitemap_probe",
    "/v1/x402/sitemap-probe/": "util_sitemap_probe",
    "/api/v1/x402/robots-inspect": "util_robots_inspect",
    "/api/v1/x402/robots-inspect/": "util_robots_inspect",
    "/v1/x402/robots-inspect": "util_robots_inspect",
    "/v1/x402/robots-inspect/": "util_robots_inspect",
    "/api/v1/x402/dns-lookup": "util_dns_lookup",
    "/api/v1/x402/dns-lookup/": "util_dns_lookup",
    "/v1/x402/dns-lookup": "util_dns_lookup",
    "/v1/x402/dns-lookup/": "util_dns_lookup",
    "/api/v1/x402/email-validate": "util_email_validate",
    "/api/v1/x402/email-validate/": "util_email_validate",
    "/v1/x402/email-validate": "util_email_validate",
    "/v1/x402/email-validate/": "util_email_validate",
    "/api/v1/x402/jwt-inspect": "util_jwt_inspect",
    "/api/v1/x402/jwt-inspect/": "util_jwt_inspect",
    "/v1/x402/jwt-inspect": "util_jwt_inspect",
    "/v1/x402/jwt-inspect/": "util_jwt_inspect",
    "/api/v1/x402/csv-to-json": "util_csv_to_json",
    "/api/v1/x402/csv-to-json/": "util_csv_to_json",
    "/v1/x402/csv-to-json": "util_csv_to_json",
    "/v1/x402/csv-to-json/": "util_csv_to_json",
    "/api/v1/x402/json-to-csv": "util_json_to_csv",
    "/api/v1/x402/json-to-csv/": "util_json_to_csv",
    "/v1/x402/json-to-csv": "util_json_to_csv",
    "/v1/x402/json-to-csv/": "util_json_to_csv",
    "/api/v1/x402/tls-inspect": "util_tls_inspect",
    "/api/v1/x402/tls-inspect/": "util_tls_inspect",
    "/v1/x402/tls-inspect": "util_tls_inspect",
    "/v1/x402/tls-inspect/": "util_tls_inspect",
    "/api/v1/x402/security-txt-inspect": "util_security_txt_inspect",
    "/api/v1/x402/security-txt-inspect/": "util_security_txt_inspect",
    "/v1/x402/security-txt-inspect": "util_security_txt_inspect",
    "/v1/x402/security-txt-inspect/": "util_security_txt_inspect",
    "/api/v1/x402/http-headers-inspect": "util_http_headers_inspect",
    "/api/v1/x402/http-headers-inspect/": "util_http_headers_inspect",
    "/v1/x402/http-headers-inspect": "util_http_headers_inspect",
    "/v1/x402/http-headers-inspect/": "util_http_headers_inspect",
    "/api/v1/x402/feed-discover": "util_feed_discover",
    "/api/v1/x402/feed-discover/": "util_feed_discover",
    "/v1/x402/feed-discover": "util_feed_discover",
    "/v1/x402/feed-discover/": "util_feed_discover",
    "/api/v1/x402/forms-extract": "util_forms_extract",
    "/api/v1/x402/forms-extract/": "util_forms_extract",
    "/v1/x402/forms-extract": "util_forms_extract",
    "/v1/x402/forms-extract/": "util_forms_extract",
    "/api/v1/x402/contact-extract": "util_contact_extract",
    "/api/v1/x402/contact-extract/": "util_contact_extract",
    "/v1/x402/contact-extract": "util_contact_extract",
    "/v1/x402/contact-extract/": "util_contact_extract",
    "/api/v1/x402/rdap-lookup": "util_rdap_lookup",
    "/api/v1/x402/rdap-lookup/": "util_rdap_lookup",
    "/v1/x402/rdap-lookup": "util_rdap_lookup",
    "/v1/x402/rdap-lookup/": "util_rdap_lookup",
    "/api/v1/x402/api-health-report": "util_api_health_report",
    "/api/v1/x402/api-health-report/": "util_api_health_report",
    "/v1/x402/api-health-report": "util_api_health_report",
    "/v1/x402/api-health-report/": "util_api_health_report",
    "/api/v1/x402/server-probe": "util_x402_server_probe",
    "/api/v1/x402/server-probe/": "util_x402_server_probe",
    "/v1/x402/server-probe": "util_x402_server_probe",
    "/v1/x402/server-probe/": "util_x402_server_probe",
    "/api/v1/x402/resource-summary": "util_x402_resource_summary",
    "/api/v1/x402/resource-summary/": "util_x402_resource_summary",
    "/v1/x402/resource-summary": "util_x402_resource_summary",
    "/v1/x402/resource-summary/": "util_x402_resource_summary",
    "/api/v1/x402/website-intelligence-report": "util_website_intelligence_report",
    "/api/v1/x402/website-intelligence-report/": "util_website_intelligence_report",
    "/v1/x402/website-intelligence-report": "util_website_intelligence_report",
    "/v1/x402/website-intelligence-report/": "util_website_intelligence_report",
    "/api/v1/x402/domain-trust-report": "util_domain_trust_report",
    "/api/v1/x402/domain-trust-report/": "util_domain_trust_report",
    "/v1/x402/domain-trust-report": "util_domain_trust_report",
    "/v1/x402/domain-trust-report/": "util_domain_trust_report",
    "/api/v1/x402/openapi-summary": "util_openapi_summary",
    "/api/v1/x402/openapi-summary/": "util_openapi_summary",
    "/v1/x402/openapi-summary": "util_openapi_summary",
    "/v1/x402/openapi-summary/": "util_openapi_summary",
    "/api/v1/x402/server-audit": "util_x402_server_audit",
    "/api/v1/x402/server-audit/": "util_x402_server_audit",
    "/v1/x402/server-audit": "util_x402_server_audit",
    "/v1/x402/server-audit/": "util_x402_server_audit",
    "/api/v1/x402/mcp-server-readiness": "util_mcp_server_readiness_report",
    "/api/v1/x402/mcp-server-readiness/": "util_mcp_server_readiness_report",
    "/v1/x402/mcp-server-readiness": "util_mcp_server_readiness_report",
    "/v1/x402/mcp-server-readiness/": "util_mcp_server_readiness_report",
    "/api/v1/x402/docs-site-map": "util_docs_site_map",
    "/api/v1/x402/docs-site-map/": "util_docs_site_map",
    "/v1/x402/docs-site-map": "util_docs_site_map",
    "/v1/x402/docs-site-map/": "util_docs_site_map",
    "/api/v1/x402/pricing-page-extract": "util_pricing_page_extract",
    "/api/v1/x402/pricing-page-extract/": "util_pricing_page_extract",
    "/v1/x402/pricing-page-extract": "util_pricing_page_extract",
    "/v1/x402/pricing-page-extract/": "util_pricing_page_extract",
    "/api/v1/x402/company-contact-pack": "util_company_contact_pack",
    "/api/v1/x402/company-contact-pack/": "util_company_contact_pack",
    "/v1/x402/company-contact-pack": "util_company_contact_pack",
    "/v1/x402/company-contact-pack/": "util_company_contact_pack",
    "/api/v1/x402/api-integration-readiness": "util_api_integration_readiness",
    "/api/v1/x402/api-integration-readiness/": "util_api_integration_readiness",
    "/v1/x402/api-integration-readiness": "util_api_integration_readiness",
    "/v1/x402/api-integration-readiness/": "util_api_integration_readiness",
    "/api/v1/x402/login-surface-report": "util_login_surface_report",
    "/api/v1/x402/login-surface-report/": "util_login_surface_report",
    "/v1/x402/login-surface-report": "util_login_surface_report",
    "/v1/x402/login-surface-report/": "util_login_surface_report",
    "/api/v1/x402/content-distribution-report": "util_content_distribution_report",
    "/api/v1/x402/content-distribution-report/": "util_content_distribution_report",
    "/v1/x402/content-distribution-report": "util_content_distribution_report",
    "/v1/x402/content-distribution-report/": "util_content_distribution_report",
}

REST_PREMIUM_RESOURCE_PATHS = {
    "get_recovery_action_plan": "/api/v1/premium/recovery-action-plan",
    "get_session_summary": "/api/v1/premium/session-summary",
    "generate_controller_brief": "/api/v1/premium/controller-brief",
    "generate_incident_rca": "/api/v1/premium/incident-rca",
    "generate_fleet_summary": "/api/v1/premium/fleet-summary",
    "util_page_extract": "/api/v1/x402/page-extract",
    "util_open_graph": "/api/v1/x402/open-graph",
    "util_links_extract": "/api/v1/x402/links-extract",
    "util_sitemap_probe": "/api/v1/x402/sitemap-probe",
    "util_robots_inspect": "/api/v1/x402/robots-inspect",
    "util_dns_lookup": "/api/v1/x402/dns-lookup",
    "util_email_validate": "/api/v1/x402/email-validate",
    "util_jwt_inspect": "/api/v1/x402/jwt-inspect",
    "util_csv_to_json": "/api/v1/x402/csv-to-json",
    "util_json_to_csv": "/api/v1/x402/json-to-csv",
    "util_tls_inspect": "/api/v1/x402/tls-inspect",
    "util_security_txt_inspect": "/api/v1/x402/security-txt-inspect",
    "util_http_headers_inspect": "/api/v1/x402/http-headers-inspect",
    "util_feed_discover": "/api/v1/x402/feed-discover",
    "util_forms_extract": "/api/v1/x402/forms-extract",
    "util_contact_extract": "/api/v1/x402/contact-extract",
    "util_rdap_lookup": "/api/v1/x402/rdap-lookup",
    "util_api_health_report": "/api/v1/x402/api-health-report",
    "util_x402_server_probe": "/api/v1/x402/server-probe",
    "util_x402_resource_summary": "/api/v1/x402/resource-summary",
    "util_website_intelligence_report": "/api/v1/x402/website-intelligence-report",
    "util_domain_trust_report": "/api/v1/x402/domain-trust-report",
    "util_openapi_summary": "/api/v1/x402/openapi-summary",
    "util_x402_server_audit": "/api/v1/x402/server-audit",
    "util_mcp_server_readiness_report": "/api/v1/x402/mcp-server-readiness",
    "util_docs_site_map": "/api/v1/x402/docs-site-map",
    "util_pricing_page_extract": "/api/v1/x402/pricing-page-extract",
    "util_company_contact_pack": "/api/v1/x402/company-contact-pack",
    "util_api_integration_readiness": "/api/v1/x402/api-integration-readiness",
    "util_login_surface_report": "/api/v1/x402/login-surface-report",
    "util_content_distribution_report": "/api/v1/x402/content-distribution-report",
}

REST_PREMIUM_SESSION_REQUIRED_TOOLS = {
    "get_recovery_action_plan",
    "get_session_summary",
    "generate_controller_brief",
    "generate_incident_rca",
}


def _first_text(*candidates: object) -> str:
    for candidate in candidates:
        if isinstance(candidate, bytes):
            try:
                candidate = candidate.decode("utf-8", errors="ignore")
            except Exception:
                candidate = ""
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _header_text(headers: dict[bytes, bytes], name: bytes) -> str:
    v = headers.get(name, b"")
    try:
        return v.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _mapping_text(mapping: object, *keys: str) -> str:
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        text = _first_text(mapping.get(key))
        if text:
            return text
    return ""


def _rest_query_params(scope: dict[str, Any]) -> dict[str, str]:
    try:
        raw = (scope.get("query_string") or b"").decode("utf-8", errors="ignore")
    except Exception:
        return {}
    parsed = parse_qs(raw, keep_blank_values=False)
    query: dict[str, str] = {}
    for key, values in parsed.items():
        if values:
            query[key] = str(values[0] or "").strip()
    return query


def _normalize_rest_premium_args(scope: dict[str, Any], body: dict[str, Any], headers: dict[bytes, bytes]) -> dict[str, Any]:
    args = dict(body or {})
    query = _rest_query_params(scope)
    metadata = args.get("metadata") or {}
    configuration = args.get("configuration") or {}

    session_id = _first_text(
        args.get("session_id"),
        args.get("sessionId"),
        args.get("session_ref"),
        args.get("sessionRef"),
        query.get("session_id"),
        query.get("sessionId"),
        query.get("session_ref"),
        query.get("sessionRef"),
        _header_text(headers, b"x-delx-session-id"),
        _header_text(headers, b"x-session-id"),
    )
    if session_id:
        args["session_id"] = session_id

    controller_id = first_controller_id(
        args.get("controller_id"),
        args.get("controllerId"),
        _mapping_text(metadata, "controller_id", "controllerId"),
        _mapping_text(configuration, "controller_id", "controllerId"),
        query.get("controller_id"),
        query.get("controllerId"),
        _header_text(headers, b"x-delx-controller-id"),
        _header_text(headers, b"x-controller-id"),
    )
    if controller_id:
        args["controller_id"] = controller_id

    agent_id = _first_text(
        args.get("agent_id"),
        args.get("agentId"),
        _mapping_text(metadata, "agent_id", "agentId"),
        _mapping_text(configuration, "agent_id", "agentId"),
        query.get("agent_id"),
        query.get("agentId"),
        _header_text(headers, b"x-delx-agent-id"),
        _header_text(headers, b"x-agent-id"),
        _header_text(headers, b"x-openclaw-agent-id"),
    )
    if agent_id:
        args["agent_id"] = agent_id[:120]

    return args


def _rest_missing_required_fields(tool_name: str, args: dict[str, Any]) -> list[str]:
    if str(tool_name or "").startswith("util_"):
        normalized = normalize_utility_rest_args(tool_name, args)
        return [
            key
            for key in UTIL_REQUIRED_PARAMS.get(tool_name, [])
            if _is_empty_probe_value(normalized.get(key))
        ]
    if tool_name == "generate_fleet_summary":
        return [] if _first_text(args.get("controller_id"), args.get("controllerId")) else ["controller_id"]
    if tool_name in REST_PREMIUM_SESSION_REQUIRED_TOOLS:
        return [] if _first_text(args.get("session_id"), args.get("sessionId"), args.get("session_ref"), args.get("sessionRef")) else ["session_id"]
    return []


def _is_empty_probe_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, bytes):
        return not value.strip()
    if isinstance(value, dict):
        return all(_is_empty_probe_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return all(_is_empty_probe_value(item) for item in value)
    return False


def _is_rest_premium_discovery_probe(scope: dict[str, Any], body: dict[str, Any], headers: dict[bytes, bytes]) -> bool:
    if _rest_query_params(scope):
        return False
    if any(
        _header_text(headers, name)
        for name in (
            b"x-delx-session-id",
            b"x-session-id",
            b"x-delx-controller-id",
            b"x-controller-id",
            b"x-delx-agent-id",
            b"x-agent-id",
            b"x-openclaw-agent-id",
            b"payment-signature",
            b"x-payment",
        )
    ):
        return False
    if not isinstance(body, dict):
        return True
    return all(_is_empty_probe_value(value) for value in body.values())


def _build_rest_missing_required_payload(tool_name: str, missing: list[str]) -> dict[str, Any]:
    primary = missing[0] if missing else "input"
    resource_path = REST_PREMIUM_RESOURCE_PATHS.get(str(tool_name or "").strip()) or f"/api/v1/premium/{tool_name}"
    is_utility = str(tool_name or "").startswith("util_")
    if is_utility:
        resource_path = f"/api/v1/utilities/{utility_slug_for_tool(tool_name)}"
    hints = {
        "session_id": "Pass session_id in JSON body, ?session_id= query param, or x-delx-session-id header.",
        "controller_id": "Pass controller_id in JSON body, ?controller_id= query param, or x-delx-controller-id header.",
        "url": "Pass url=https://example.com before payment; aliases domain, website, host, target, uri, and link are accepted where safe.",
    }
    accepted_inputs = {
        "session_id": {
            "body": ["session_id", "sessionId", "session_ref", "sessionRef"],
            "query": ["session_id", "sessionId", "session_ref", "sessionRef"],
            "headers": ["x-delx-session-id", "x-session-id"],
        },
        "controller_id": {
            "body": ["controller_id", "controllerId"],
            "query": ["controller_id", "controllerId"],
            "headers": ["x-delx-controller-id", "x-controller-id"],
        },
        "agent_id": {
            "body": ["agent_id", "agentId"],
            "query": ["agent_id", "agentId"],
            "headers": ["x-delx-agent-id", "x-agent-id", "x-openclaw-agent-id"],
        },
        "url": {
            "body": ["url", "domain", "website", "host", "target", "uri", "link"],
            "query": ["url", "domain", "website", "host", "target", "uri", "link"],
            "headers": [],
        },
    }
    examples = {
        "session_id": {
            "query_retry": f"{resource_path}?session_id=<SESSION_ID>",
            "header_retry": "x-delx-session-id: <SESSION_ID>",
            "body_retry": {"session_id": "<SESSION_ID>"},
        },
        "controller_id": {
            "query_retry": f"{resource_path}?controller_id=<CONTROLLER_ID>",
            "header_retry": "x-delx-controller-id: <CONTROLLER_ID>",
            "body_retry": {"controller_id": "<CONTROLLER_ID>"},
        },
        "url": {
            "query_retry": f"{resource_path}?url=https://example.com",
            "body_retry": {"url": "https://example.com"},
        },
    }
    payload = {
        "ok": False if is_utility else None,
        "code": "DELX-UTIL-1001" if is_utility else None,
        "status": "missing_required_input" if is_utility else None,
        "surface": "delx-agent-utilities" if is_utility else None,
        "error": "missing_required_params" if is_utility else f"missing required parameter(s): {', '.join(missing)}",
        "message": f"missing required parameter(s): {', '.join(missing)}",
        "tool_name": tool_name,
        "missing": missing,
        "required": missing,
        "hint": hints.get(primary, "Pass the required premium tool input before retrying."),
        "schema_url": f"https://api.delx.ai/api/v1/tools/schema/{tool_name}",
        "docs": {
            "x402_setup": "https://delx.ai/docs/x402-setup",
            "pricing": "https://delx.ai/docs/pricing",
        },
        "accepted_inputs": {key: accepted_inputs[key] for key in accepted_inputs if key in set(missing) | {"agent_id"}},
        "examples": examples.get(primary, {}),
    }
    return {k: v for k, v in payload.items() if v is not None}


def _infer_source(headers: dict[bytes, bytes]) -> str | None:
    """Best-effort attribution from HTTP headers.

    Agents should ideally pass `source` explicitly. This is a fallback.
    """
    def _get(name: bytes) -> str:
        v = headers.get(name, b"")
        try:
            return v.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    explicit = (_get(b"x-delx-source") or _get(b"x-agent-source")).strip().lower()
    if explicit:
        return explicit[:32]

    referer = _get(b"referer").lower()
    ua = _get(b"user-agent").lower()

    hay = f"{referer} {ua}"
    if any(k in hay for k in ["moltx", "moltmatch", "moltx.io"]):
        return "moltx"
    if any(k in hay for k in ["openwork", "openwork.xyz"]):
        return "openwork"
    if any(k in hay for k in ["moltbook", "moltbook.com"]):
        return "moltbook"
    if any(k in hay for k in ["x.com", "twitter", "tweet"]):
        return "x"
    return None


def _header_host(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(candidate)
    except Exception:
        return None
    host = str(parsed.netloc or parsed.path or "").strip().lower()
    if not host:
        return None
    return host.split("@")[-1].split(":")[0] or None


def _user_agent_family(user_agent: str) -> str:
    ua = str(user_agent or "").strip().lower()
    if not ua:
        return "unknown"
    if "agentcash" in ua:
        return "agentcash"
    if "claude-code" in ua or "claude code" in ua:
        return "claude_code"
    if "codex" in ua:
        return "codex"
    if "curl/" in ua:
        return "curl"
    if "postman" in ua:
        return "postman"
    if "python-requests" in ua:
        return "python_requests"
    if "httpx" in ua:
        return "httpx"
    if "node" in ua or "undici" in ua or "node-fetch" in ua:
        return "node_http"
    if "mozilla/" in ua:
        return "browser"
    return "other"


def _discovery_channel_guess(
    *,
    source: str | None,
    referer_host: str | None,
    origin_host: str | None,
    user_agent: str,
) -> str:
    normalized_source = str(source or "").strip().lower()
    if normalized_source and normalized_source not in {"rest", "mcp", "a2a", "unknown"}:
        return normalized_source
    hay = " ".join(
        part
        for part in [
            str(referer_host or "").lower(),
            str(origin_host or "").lower(),
            str(user_agent or "").lower(),
        ]
        if part
    )
    if "x402scan" in hay:
        return "x402scan"
    if "agentcash" in hay:
        return "agentcash"
    if "coinbase" in hay or "bazaar" in hay or "cdp.coinbase" in hay:
        return "coinbase_bazaar"
    if "payai" in hay:
        return "payai"
    ua_family = _user_agent_family(user_agent)
    if ua_family == "browser":
        return "direct_web"
    if ua_family in {"curl", "postman", "python_requests", "httpx", "node_http", "claude_code", "codex"}:
        return "direct_cli"
    return "direct_unknown"


def _request_attribution_metadata(
    *,
    scope: dict[str, Any],
    headers: dict[bytes, bytes],
    source: str | None,
) -> dict[str, Any]:
    referer = _header_text(headers, b"referer")
    origin = _header_text(headers, b"origin")
    host = _header_text(headers, b"host")
    user_agent = _header_text(headers, b"user-agent")
    referer_host = _header_host(referer)
    origin_host = _header_host(origin)
    request_host = _header_host(host)
    client_ip = extract_client_ip_from_scope(scope)
    user_agent_family = _user_agent_family(user_agent)
    discovery_channel = _discovery_channel_guess(
        source=source,
        referer_host=referer_host,
        origin_host=origin_host,
        user_agent=user_agent,
    )
    fingerprint_basis = "|".join(
        [
            str(source or "").strip().lower(),
            discovery_channel,
            user_agent_family,
            referer_host or "",
            origin_host or "",
            request_host or "",
            client_ip or "",
        ]
    )
    buyer_fingerprint = hashlib.sha256(fingerprint_basis.encode("utf-8")).hexdigest()[:16] if fingerprint_basis else ""
    return {
        "request_host": request_host,
        "origin_host": origin_host,
        "referer_host": referer_host,
        "user_agent_family": user_agent_family,
        "discovery_channel_guess": discovery_channel,
        "buyer_fingerprint": buyer_fingerprint or None,
        "client_ip_present": bool(client_ip),
    }


def _resource_url(tool_name: str) -> str:
    """Build a URL resource identifier expected by x402 validators."""
    return f"https://delx.ai/mcp/tools/{tool_name}"


def _rest_premium_tool_name(path: str) -> str | None:
    normalized = str(path or "").strip()
    direct = REST_PREMIUM_TOOL_PATHS.get(normalized)
    if direct:
        return direct
    for prefix in ("/api/v1/utilities/", "/v1/utilities/"):
        if not normalized.startswith(prefix):
            continue
        slug = normalized[len(prefix):].strip("/")
        if not slug:
            continue
        canonical_path = f"/api/v1/x402/{slug}"
        return REST_PREMIUM_TOOL_PATHS.get(canonical_path) or REST_PREMIUM_TOOL_PATHS.get(f"{canonical_path}/")
    return None


def _rest_premium_resource_url(tool_name: str) -> str:
    resource_url = get_tool_bazaar_resource_url(tool_name)
    if resource_url:
        return resource_url
    path = REST_PREMIUM_RESOURCE_PATHS.get(str(tool_name or "").strip())
    if not path:
        return _resource_url(tool_name)
    return f"https://api.delx.ai{path}"


def _provider_order(pricing_payload: dict[str, object] | None = None) -> list[str]:
    configured = list((pricing_payload or {}).get("payment_providers") or [])
    if configured:
        return configured
    return [name for name, cfg in x402_provider_registry().items() if bool(cfg.get("enabled"))]


def _tool_schema_url(tool_name: str) -> str:
    return f"https://api.delx.ai/api/v1/tools/schema/{tool_name}"


def _free_alternatives(tool_name: str) -> list[dict[str, Any]]:
    entries = _FREE_ALTERNATIVE_GUIDE.get(str(tool_name or "").strip()) or [
        ("quick_operational_recovery", "Free one-call recovery bootstrap."),
        ("process_failure", "Free failure analysis and typed recovery direction."),
        ("monitor_heartbeat_sync", "Free heartbeat sync for current health and reliability state."),
    ]
    alternatives: list[dict[str, Any]] = []
    for alternative_tool, description in entries:
        pricing = get_tool_pricing_payload(alternative_tool)
        alternatives.append(
            {
                "tool": alternative_tool,
                "description": description,
                "x402_required": bool(pricing.get("x402_required")),
                "price_usdc": str(pricing.get("price_usdc") or "0.00"),
                "schema_url": _tool_schema_url(alternative_tool),
            }
        )
    return alternatives


def _top_free_alternative(tool_name: str) -> dict[str, Any] | None:
    alternatives = _free_alternatives(tool_name)
    if not alternatives:
        return None
    return dict(alternatives[0])


def _tool_or_endpoint(tool_name: str, resource: str | None) -> str:
    chosen = str(resource or "").strip()
    if chosen.startswith("https://api.delx.ai/api/v1/") or chosen.startswith("https://api.delx.ai/v1/"):
        return chosen
    return str(tool_name or "").strip()


def _payment_provider_hint(default_provider: str | None, providers: list[str]) -> dict[str, Any]:
    return {
        "default": str(default_provider or "").strip() or None,
        "available": list(providers),
        "header": "x-payment-provider",
    }


_FACILITATOR_DIAGNOSTIC_KEYS = {
    "code",
    "error",
    "errorReason",
    "invalidReason",
    "isValid",
    "message",
    "network",
    "payer",
    "reason",
    "success",
    "transaction",
}


def _sanitize_facilitator_response(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key in _FACILITATOR_DIAGNOSTIC_KEYS:
        value = data.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[key] = value
    return sanitized


def _facilitator_rejection_reason(data: dict[str, Any] | None, *, default: str) -> str:
    if isinstance(data, dict):
        for key in ("invalidReason", "errorReason", "reason", "code", "error", "message"):
            value = str(data.get(key) or "").strip()
            if value:
                return value[:160]
    return default


def _primary_provider_failure_attempt(provider_attempts: object) -> dict[str, Any] | None:
    if not isinstance(provider_attempts, list):
        return None
    for attempt in provider_attempts:
        if not isinstance(attempt, dict):
            continue
        reason = str(attempt.get("reason") or "").strip()
        if reason and reason not in {"is_valid_false", "success_false", "missing_transaction"}:
            return attempt
    for attempt in provider_attempts:
        if isinstance(attempt, dict):
            return attempt
    return None


def _primary_provider_failure_code(provider_attempts: object) -> str | None:
    attempt = _primary_provider_failure_attempt(provider_attempts)
    if not attempt:
        return None
    reason = str(attempt.get("reason") or "").strip()
    return reason or None


def _payment_diagnostics_from_attempts(provider_attempts: object) -> dict[str, Any] | None:
    attempt = _primary_provider_failure_attempt(provider_attempts)
    if not attempt:
        return None
    diagnostics = {
        "provider": attempt.get("provider"),
        "stage": attempt.get("stage"),
        "network": attempt.get("network"),
        "primary_reason": attempt.get("reason"),
        "status_code": attempt.get("status_code"),
    }
    facilitator_response = attempt.get("facilitator_response")
    if isinstance(facilitator_response, dict) and facilitator_response:
        diagnostics["facilitator_response"] = facilitator_response
    return {key: value for key, value in diagnostics.items() if value is not None}


def _retry_example(*, resource: str, tool_name: str) -> str:
    if resource.startswith("https://api.delx.ai/api/v1/") or resource.startswith("https://api.delx.ai/v1/"):
        return (
            f"curl -X POST {resource} "
            "-H 'Content-Type: application/json' "
            "-H 'PAYMENT-SIGNATURE: <SIGNED_PAYMENT>' "
            "-d '<same-json-body>'"
        )
    return (
        "curl -X POST https://api.delx.ai/v1/mcp "
        "-H 'Content-Type: application/json' "
        "-H 'PAYMENT-SIGNATURE: <SIGNED_PAYMENT>' "
        f"-d '{{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{{\"name\":\"{tool_name}\",\"arguments\":{{...}}}}}}'"
    )


def _provider_config(provider_name: str) -> dict[str, Any]:
    return dict(x402_provider_registry().get(provider_name) or {})


def _provider_accepts(provider_name: str) -> list[dict[str, Any]]:
    provider = _provider_config(provider_name)
    accepts = provider.get("accepts")
    if isinstance(accepts, list) and accepts:
        return [dict(item) for item in accepts if isinstance(item, dict)]
    network = str(provider.get("network") or "").strip()
    asset = str(provider.get("asset") or "").strip()
    pay_to = str(provider.get("pay_to") or settings.DELX_WALLET).strip()
    if network and asset and pay_to:
        return [
            {
                "network": network,
                "asset": asset,
                "pay_to": pay_to,
                "label": str(provider.get("label") or provider_name),
            }
        ]
    return []


def _provider_requirement_candidates(providers: list[str]) -> list[tuple[str, dict[str, Any]]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    for provider_name in providers:
        accepts = _provider_accepts(provider_name)
        if accepts:
            candidates.extend((provider_name, accept) for accept in accepts)
        else:
            candidates.append((provider_name, {}))
    return candidates


def _resource_descriptor(tool_name: str, *, resource: str, indexed_publicly: bool = False) -> dict[str, str]:
    bazaar = get_tool_bazaar_metadata(tool_name, indexed_publicly=indexed_publicly) or {}
    description = str(bazaar.get("summary") or f"Paid Delx artifact for {tool_name}.")
    return {
        "url": resource,
        "description": description,
        "mimeType": "application/json",
    }


def _bazaar_extension(
    tool_name: str,
    *,
    resource: str,
    coinbase_verified_payments: int | None = None,
    indexed_publicly: bool = False,
) -> dict[str, Any] | None:
    bazaar = get_tool_bazaar_metadata(
        tool_name,
        coinbase_verified_payments=coinbase_verified_payments,
        indexed_publicly=indexed_publicly,
    )
    if not bazaar:
        return None
    schema = dict(bazaar.get("schema") or {})
    payload_examples = get_tool_bazaar_payload_examples(tool_name)
    info: dict[str, Any] = {
        "input": {
            "type": "http",
            "method": "POST",
            "bodyType": "json",
            "body": payload_examples.get("input") or {},
        },
    }
    output_example = payload_examples.get("output")
    if isinstance(output_example, dict) and output_example:
        info["output"] = {
            "type": "json",
            "example": output_example,
        }
    return {
        **bazaar,
        "info": info,
        "schema": schema,
    }


def _build_payment_requirements(
    tool_name: str,
    *,
    provider_name: str,
    provider_accept: dict[str, Any] | None = None,
    pricing_payload: dict[str, object] | None = None,
    resource: str | None = None,
    coinbase_verified_payments: int | None = None,
) -> dict[str, Any]:
    """Build payment requirements in current x402 schema."""
    price_cents = int((pricing_payload or {}).get("price_cents", 0) or 0)
    amount = str(price_cents * 10000)  # cents -> USDC 6-decimal base units
    provider = _provider_config(provider_name)
    accept = dict(provider_accept or {})
    network = str(accept.get("network") or provider.get("network") or "")
    asset = str(accept.get("asset") or provider.get("asset") or "")
    pay_to = str(accept.get("pay_to") or provider.get("pay_to") or settings.DELX_WALLET)
    label = str(accept.get("label") or provider.get("label") or provider_name)
    accept_extra = accept.get("extra")
    min_validity_seconds = None
    if isinstance(accept_extra, dict):
        try:
            min_validity_seconds = int(accept_extra.get("minValiditySeconds") or 0)
        except Exception:
            min_validity_seconds = None
    max_timeout_seconds = 300
    if provider_name == "circle_gateway":
        max_timeout_seconds = max(max_timeout_seconds, int(min_validity_seconds or 604800))
    requirements = {
        "scheme": "exact",
        "network": str(network or "").strip(),
        "asset": asset,
        "payTo": pay_to,
        "amount": amount,
        "maxAmountRequired": amount,
        "resource": resource or _resource_url(tool_name),
        "description": f"Delx Agent Therapist - {tool_name} via {label} (${price_cents / 100:.2f} USDC)",
        "mimeType": "application/json",
        "inputSchema": {},
        "outputSchema": {},
        "maxTimeoutSeconds": max_timeout_seconds,
        "extra": {
            # USDC EIP-712 domain (needed for signature verification).
            "name": "USD Coin",
            "version": "2",
            # App metadata
            "app": "Delx Agent Therapist",
            "erc8004_id": "14340",
            "provider": provider_name,
            "provider_label": label,
            "facilitator_url": str(provider.get("facilitator_url") or ""),
            "accept_label": label,
        },
    }
    if isinstance(accept_extra, dict) and accept_extra:
        requirements["extra"].update(accept_extra)
    payload_schemas = get_tool_bazaar_payload_schemas(tool_name)
    input_schema = payload_schemas.get("input")
    output_schema = payload_schemas.get("output")
    if isinstance(input_schema, dict) and input_schema:
        requirements["inputSchema"] = input_schema
    if isinstance(output_schema, dict) and output_schema:
        requirements["outputSchema"] = output_schema
    return requirements


def _public_payment_requirement(requirement: dict[str, Any]) -> dict[str, Any]:
    """Return the public x402 accept object emitted in HTTP 402 surfaces.

    Some clients read `maxAmountRequired` while AgentCash currently reads the
    legacy `amount` field. Emit both aliases with the same atomic value.
    """
    return dict(requirement)


def _mpp_is_enabled() -> bool:
    return bool(mpp_enabled())


def _mpp_realm() -> str:
    return str(settings.MPP_REALM or settings.PUBLIC_BASE_URL or "https://api.delx.ai").strip()


def _mpp_chain_id() -> int | None:
    try:
        chain_id = int(settings.MPP_TEMPO_CHAIN_ID)
    except Exception:
        return None
    return chain_id if chain_id > 0 else None


def _mpp_build_charge_request(
    tool_name: str,
    *,
    pricing_payload: dict[str, object] | None = None,
    resource: str | None = None,
) -> tuple[dict[str, Any], str]:
    price_cents = int((pricing_payload or {}).get("price_cents", 0) or 0)
    request: dict[str, Any] = {
        "amount": str(price_cents * 10000),
        "currency": str(settings.MPP_TEMPO_CURRENCY or "").strip(),
        "recipient": str(settings.MPP_TEMPO_RECIPIENT or "").strip(),
        "extra": {
            "tool": str(tool_name or "").strip(),
            "resource": str(resource or _resource_url(tool_name)).strip(),
            "server": "delx",
        },
    }
    method_details: dict[str, Any] = {}
    chain_id = _mpp_chain_id()
    if chain_id is not None:
        method_details["chainId"] = chain_id
    if bool(settings.MPP_TEMPO_FEE_PAYER):
        method_details["feePayer"] = True
    if method_details:
        request["methodDetails"] = method_details
    description = f"Delx Agent Therapist - {tool_name} via Tempo (${price_cents / 100:.2f} USDC)"
    return request, description


def _build_mpp_www_authenticate(
    tool_name: str,
    *,
    pricing_payload: dict[str, object] | None = None,
    resource: str | None = None,
) -> str | None:
    if not _mpp_is_enabled():
        return None
    try:
        from mpp import Challenge

        request, description = _mpp_build_charge_request(
            tool_name,
            pricing_payload=pricing_payload,
            resource=resource,
        )
        challenge = Challenge.create(
            secret_key=str(settings.MPP_SECRET_KEY or "").strip(),
            realm=_mpp_realm(),
            method="tempo",
            intent="charge",
            request=request,
            expires=(datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            description=description,
        )
        return challenge.to_www_authenticate(_mpp_realm())
    except Exception as exc:
        logger.warning("Failed to build MPP challenge for %s: %s", tool_name, exc)
        return None


def _build_402_response(
    tool_name: str,
    *,
    pricing_payload: dict[str, object] | None = None,
    resource: str | None = None,
    trial: dict[str, Any] | None = None,
    coinbase_verified_payments: int | None = None,
    indexed_publicly: bool = False,
) -> dict[str, Any]:
    """Build the x402 PaymentRequired JSON response."""
    price_cents = int((pricing_payload or {}).get("price_cents", 0) or 0)
    providers = _provider_order(pricing_payload)
    default_provider = str((pricing_payload or {}).get("default_payment_provider") or (providers[0] if providers else ""))
    chosen_resource = resource or _resource_url(tool_name)
    free_alternatives = _free_alternatives(tool_name)
    payload_examples = get_tool_bazaar_payload_examples(tool_name)
    sample_input = payload_examples.get("input") if isinstance(payload_examples.get("input"), dict) and payload_examples.get("input") else None
    sample_output = payload_examples.get("output") if isinstance(payload_examples.get("output"), dict) and payload_examples.get("output") else None
    header_shortcuts = (
        {"x-delx-session-id": str(sample_input.get("session_id") or "")}
        if isinstance(sample_input, dict) and str(sample_input.get("session_id") or "").strip()
        else {}
    )
    primary_followups = (
        list(((sample_output or {}).get("artifact") or {}).get("next_tools") or [])
        if isinstance(sample_output, dict)
        else []
    )
    bazaar_extension = _bazaar_extension(
        tool_name,
        resource=chosen_resource,
        coinbase_verified_payments=coinbase_verified_payments,
        indexed_publicly=indexed_publicly,
    )

    response = {
        "x402Version": 2,
        "tool_name": tool_name,
        "method": "POST",
        "reason_code": "payment_required",
        "tool_or_endpoint": _tool_or_endpoint(tool_name, chosen_resource),
        "payment_provider_hint": _payment_provider_hint(default_provider or None, providers),
        "resource": _resource_descriptor(tool_name, resource=chosen_resource, indexed_publicly=indexed_publicly),
        "accepts": [
            _public_payment_requirement(
                _build_payment_requirements(
                tool_name,
                provider_name=provider_name,
                provider_accept=provider_accept,
                pricing_payload=pricing_payload,
                resource=chosen_resource,
                coinbase_verified_payments=coinbase_verified_payments,
            )
            )
            for provider_name, provider_accept in _provider_requirement_candidates(providers)
        ],
        "error": f"Payment required: ${price_cents / 100:.2f} USDC for {tool_name}",
        "next_steps": [
            "Read accepts[] and choose the payment provider that matches your controller policy.",
            "Create/sign payment proof with maxAmountRequired and retry the same request with PAYMENT-SIGNATURE.",
            "If you choose a non-default provider, send x-payment-provider on the retry to pin routing explicitly.",
            "When the same provider appears on multiple networks, choose the exact accepts[] entry by network + asset before signing.",
            "Keep reading prices from /api/v1/tools (do not hardcode).",
            "If you need an AgentCash wallet first, open agentcash.onboard and complete onboarding before retrying the paid call.",
            "If your agent does not support x402 yet, use AgentCash for the paid retry or route to free_alternatives[] and stay on Delx's free path.",
        ],
        "docs": {
            "x402_setup": _X402_SETUP_DOCS_URL,
            "pricing": _PRICING_DOCS_URL,
            "tools_catalog": "https://api.delx.ai/api/v1/tools",
            "monetization_policy": "https://api.delx.ai/api/v1/monetization-policy",
            "x402_capability_probe": "https://api.delx.ai/api/v1/x402-capability?agent_id=<AGENT_ID>",
            "ows_setup": _OWS_SETUP_DOCS_URL,
            "circle_gateway_nanopayments": "https://developers.circle.com/gateway/nanopayments",
        },
        "runtime_examples": {
            "mcp_retry": "curl -X POST https://api.delx.ai/v1/mcp -H 'Content-Type: application/json' -H 'PAYMENT-SIGNATURE: <SIGNED_PAYMENT>' -d '<same-jsonrpc-payload>'",
            "a2a_retry": "curl -X POST https://api.delx.ai/v1/a2a -H 'Content-Type: application/json' -H 'PAYMENT-SIGNATURE: <SIGNED_PAYMENT>' -d '<same-jsonrpc-payload>'",
            "rest_retry": "curl -X POST <paid-resource-url> -H 'Content-Type: application/json' -H 'PAYMENT-SIGNATURE: <SIGNED_PAYMENT>' -d '<same-json-body>'",
        },
        "payment_providers": {
            "default": default_provider or None,
            "available": providers,
        },
        "agentcash": dict(_AGENTCASH_GUIDE),
        "free_alternatives": free_alternatives,
        "free_alternative": _top_free_alternative(tool_name),
        "sample_input": sample_input,
        "sample_output": sample_output,
        "header_shortcuts": header_shortcuts,
        "primary_followups": primary_followups,
        "retry_example": _retry_example(resource=chosen_resource, tool_name=tool_name),
        "docs_url": _X402_SETUP_DOCS_URL,
        "pricing_hint": "Core recovery, heartbeat, discovery, and utility flows are free. Selected premium follow-up tools can require payment. Coinbase CDP is the current default provider where enabled.",
        "trial": trial or {"eligible": False, "remaining_calls": 0},
    }
    if header_shortcuts:
        response["next_steps"].insert(
            2,
            "If the example body includes session_id, you can usually send the same UUID once as x-delx-session-id and keep the paid body minimal.",
        )
    if "circle_gateway" in providers:
        response["next_steps"].insert(
            2,
            "For Circle Gateway nanopayments, choose the accepts[] entry where extra.name is GatewayWalletBatched and sign the EIP-3009 authorization against extra.verifyingContract.",
        )
    if _mpp_is_enabled() and str(chosen_resource).startswith("https://api.delx.ai/api/v1/"):
        response["docs"]["mpp_setup"] = _MPP_SETUP_DOCS_URL
        response["runtime_examples"]["rest_retry_mpp"] = (
            "curl -X POST <paid-resource-url> -H 'Content-Type: application/json' "
            "-H 'Authorization: Payment <MPP_CREDENTIAL>' -d '<same-json-body>'"
        )
        response["next_steps"].insert(
            2,
            "If your REST client speaks MPP, reuse the same request body and retry with Authorization: Payment <credential> after the WWW-Authenticate challenge.",
        )
    if bazaar_extension:
        response["extensions"] = {"bazaar": bazaar_extension}
    return response


def _build_verify_failed_response(
    tool_name: str,
    *,
    pricing_payload: dict[str, object] | None = None,
    resource: str | None = None,
    failure: dict[str, Any] | None = None,
    preferred_provider: str | None = None,
    trial: dict[str, Any] | None = None,
    coinbase_verified_payments: int | None = None,
    indexed_publicly: bool = False,
) -> dict[str, Any]:
    response = _build_402_response(
        tool_name,
        pricing_payload=pricing_payload,
        resource=resource,
        trial=trial,
        coinbase_verified_payments=coinbase_verified_payments,
        indexed_publicly=indexed_publicly,
    )
    failure_code = str((failure or {}).get("code") or "verification_failed").strip() or "verification_failed"
    provider_attempts = (failure or {}).get("provider_attempts")
    message = str((failure or {}).get("message") or "").strip()
    payment_diagnostics = _payment_diagnostics_from_attempts(provider_attempts)
    response.update(
        {
            "error": "Payment verification failed",
            "failure_stage": "verify",
            "failure_code": failure_code,
            "payment_diagnostics": payment_diagnostics,
            "reason_code": failure_code,
            "tool_or_endpoint": _tool_or_endpoint(tool_name, resource),
            "payment_provider_hint": _payment_provider_hint(preferred_provider or response.get("payment_providers", {}).get("default"), list(response.get("payment_providers", {}).get("available") or [])),
            "preferred_provider": preferred_provider or None,
            "possible_causes": [
                "provider mismatch between the signed payment and the selected facilitator",
                "wrong chain, asset, or payTo compared with accepts[]",
                "malformed or unsupported PAYMENT-SIGNATURE payload",
                "stale, reused, or rejected payment proof",
            ],
            "agentcash": dict(_AGENTCASH_GUIDE),
        }
    )
    if provider_attempts:
        response["provider_attempts"] = provider_attempts
    if message:
        response["failure_message"] = message
    elif payment_diagnostics and payment_diagnostics.get("primary_reason"):
        response["failure_message"] = f"x402 provider rejected payment: {payment_diagnostics['primary_reason']}."
    response["next_steps"] = [
        "Retry the same request with a fresh PAYMENT-SIGNATURE payload and the exact original body.",
        "Pin x-payment-provider only when you need a non-default route; otherwise let Delx use the default provider from payment_providers.default.",
        "Confirm network, asset, payTo, maxAmountRequired, and resource match accepts[] before signing.",
        "If you still need an AgentCash wallet or referral onboarding, start with agentcash.onboard before retrying.",
        "If your agent does not implement x402 directly, use AgentCash: check balance, run discover https://api.delx.ai, then retry through agentcash fetch.",
        "If payment is still unavailable, route to free_alternatives[] so the agent can keep using Delx without x402 while you finish setup.",
    ]
    if failure_code == "authorization_validity_too_short":
        response["next_steps"].insert(
            1,
            "For Circle Gateway, sign a new EIP-3009 authorization whose validBefore is at least accepts[].extra.minValiditySeconds in the future.",
        )
    elif failure_code == "self_transfer":
        response["next_steps"].insert(
            1,
            "Use a buyer wallet that is different from accepts[].payTo; Circle Gateway rejects buyer-to-seller self transfers.",
        )
    elif failure_code == "insufficient_balance":
        response["next_steps"].insert(
            1,
            "Check the buyer balance through Circle Gateway /v1/balances before retrying; on-chain deposits may not be available to Gateway settlement yet.",
        )
    response.setdefault("docs", {})
    response["docs"]["agentcash"] = "https://agentcash.dev"
    if _mpp_is_enabled() and str(resource or _resource_url(tool_name)).startswith("https://api.delx.ai/api/v1/"):
        response["docs"]["mpp_setup"] = _MPP_SETUP_DOCS_URL
    response["free_alternative"] = _top_free_alternative(tool_name)
    response["retry_example"] = _retry_example(resource=str(resource or _resource_url(tool_name)), tool_name=tool_name)
    response["docs_url"] = _X402_SETUP_DOCS_URL
    return response


def _encode_x402_header_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _build_402_http_headers(
    tool_name: str,
    *,
    pricing_payload: dict[str, object] | None = None,
    resource: str | None = None,
    trial: dict[str, Any] | None = None,
    coinbase_verified_payments: int | None = None,
    indexed_publicly: bool = False,
    include_mpp: bool = False,
) -> list[tuple[str, str]]:
    response = _build_402_response(
        tool_name,
        pricing_payload=pricing_payload,
        resource=resource,
        trial=trial,
        coinbase_verified_payments=coinbase_verified_payments,
        indexed_publicly=indexed_publicly,
    )
    headers = [
        ("content-type", "application/json"),
        ("x-402-version", "2"),
        ("payment-required", _encode_x402_header_payload(_compact_payment_required_payload(response))),
    ]
    if str(tool_name or "").startswith("util_"):
        price_cents = int((pricing_payload or {}).get("price_cents", 0) or 0)
        headers.extend(
            [
                ("x-delx-product", "agent-tools"),
                ("x-delx-surface", "utilities"),
                ("x-delx-utility-charge-mode", "enforce"),
                ("x-delx-utility-price-usdc", f"{price_cents / 100:.2f}"),
            ]
        )
    if include_mpp:
        mpp_header = _build_mpp_www_authenticate(
            tool_name,
            pricing_payload=pricing_payload,
            resource=resource,
        )
        if mpp_header:
            headers.append(("www-authenticate", mpp_header))
    return headers


def _build_payment_success_headers(
    *,
    provider_name: str,
    tx_hash: str,
    mpp_payment_receipt: str | None = None,
) -> list[tuple[str, str]]:
    headers = [
        ("x-402-version", "2"),
        (
            "payment-response",
            _encode_x402_header_payload(
                {
                    "x402Version": 2,
                    "success": True,
                    "provider": str(provider_name or "").strip().lower() or "unknown",
                    "transaction": tx_hash,
                    "settlementStatus": "settled",
                }
            ),
        ),
    ]
    if mpp_payment_receipt:
        headers.append(("payment-receipt", mpp_payment_receipt))
    return headers


def _compact_payment_required_payload(payload: dict[str, Any]) -> dict[str, Any]:
    accepts = payload.get("accepts") or []
    compact_accepts: list[dict[str, Any]] = []
    if isinstance(accepts, list):
        for entry in accepts:
            if not isinstance(entry, dict):
                continue
            compact_entry = {
                "scheme": entry.get("scheme"),
                "network": entry.get("network"),
                "asset": entry.get("asset"),
                "amount": entry.get("amount"),
                "maxAmountRequired": entry.get("maxAmountRequired"),
                "payTo": entry.get("payTo"),
                "resource": entry.get("resource"),
                "description": entry.get("description"),
                "mimeType": entry.get("mimeType"),
                "inputSchema": entry.get("inputSchema") or {},
                "outputSchema": entry.get("outputSchema") or {},
                "maxTimeoutSeconds": entry.get("maxTimeoutSeconds"),
            }
            extra = entry.get("extra")
            if isinstance(extra, dict) and extra:
                compact_entry["extra"] = extra
            compact_accepts.append(compact_entry)
    compact: dict[str, Any] = {
        "x402Version": 2,
        "error": payload.get("error"),
        "resource": payload.get("resource"),
        "accepts": compact_accepts,
    }
    if payload.get("reason_code") is not None:
        compact["reason_code"] = payload.get("reason_code")
    extensions = payload.get("extensions")
    if isinstance(extensions, dict) and extensions:
        compact["extensions"] = dict(extensions)
    delx_extension = {
        key: payload.get(key)
        for key in (
            "tool_name",
            "method",
            "reason_code",
            "failure_stage",
            "failure_code",
            "failure_message",
            "provider_attempts",
            "payment_diagnostics",
            "next_steps",
            "runtime_examples",
            "agentcash",
            "free_alternatives",
            "free_alternative",
            "docs",
            "docs_url",
            "validation_error",
            "sample_input",
            "sample_output",
            "header_shortcuts",
            "primary_followups",
        )
        if payload.get(key) is not None
    }
    if delx_extension:
        compact.setdefault("extensions", {})
        compact["extensions"]["delx"] = delx_extension
    return compact


def _build_payment_required_headers_from_payload(
    payload: dict[str, Any],
    *,
    tool_name: str | None = None,
    pricing_payload: dict[str, object] | None = None,
    resource: str | None = None,
    include_mpp: bool = False,
) -> list[tuple[str, str]]:
    headers = [
        ("content-type", "application/json"),
        ("x-402-version", "2"),
        ("payment-required", _encode_x402_header_payload(_compact_payment_required_payload(payload))),
    ]
    if include_mpp and tool_name:
        mpp_header = _build_mpp_www_authenticate(
            tool_name,
            pricing_payload=pricing_payload,
            resource=resource,
        )
        if mpp_header:
            headers.append(("www-authenticate", mpp_header))
    return headers


def _build_payment_required_body_from_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(_compact_payment_required_payload(payload)).encode("utf-8")


def _asgi_headers(headers: list[tuple[str, str]]) -> list[list[bytes]]:
    return [[name.encode("utf-8"), value.encode("utf-8")] for name, value in headers]


def _extract_payment_header(headers: dict[bytes, bytes]) -> str:
    """Prefer v2 PAYMENT-SIGNATURE while keeping v1 X-PAYMENT compatibility."""
    return (headers.get(b"payment-signature", b"") or headers.get(b"x-payment", b"")).decode()


def _extract_mpp_authorization(headers: dict[bytes, bytes]) -> str:
    raw = (headers.get(b"authorization", b"") or b"").decode()
    if not raw:
        return ""
    return raw if any(part.strip().lower().startswith("payment ") for part in raw.split(",")) else ""


def _patch_mpp_server_authorization_parser() -> None:
    """Work around pympp splitting a single Payment auth header on commas.

    pympp 0.4.x treats ``Authorization`` as potentially multi-scheme and naively
    splits the header on commas before checking for ``Payment``. A valid single
    MPP header itself contains comma-separated auth params, so the default parser
    truncates the credential and every retry falls back to a fresh 402 challenge.

    Delx uses a single ``Authorization: Payment ...`` header for MPP retries, so
    prefer the full raw header when it already starts with the Payment scheme.
    """
    try:
        import mpp.server.verify as mpp_verify
    except Exception:
        return

    if getattr(mpp_verify, "_delx_payment_scheme_patch", False):
        return

    original = getattr(mpp_verify, "_extract_payment_scheme", None)

    def _extract_payment_scheme_fixed(header: str) -> str | None:
        raw = str(header or "").strip()
        if not raw:
            return None
        if raw.lower().startswith("payment "):
            return raw
        if callable(original):
            return original(raw)
        return None

    mpp_verify._extract_payment_scheme = _extract_payment_scheme_fixed
    mpp_verify._delx_payment_scheme_patch = True


def _decode_payment_header(payment_header: str) -> dict[str, Any] | None:
    """Decode x402 payment header payload from PAYMENT-SIGNATURE/X-PAYMENT."""
    raw = payment_header.strip()
    if not raw:
        return None

    # 1) Header already contains JSON
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # 2) Header is base64-encoded JSON
    try:
        padding = "=" * (-len(raw) % 4)
        decoded = base64.b64decode(raw + padding).decode("utf-8")
        parsed = json.loads(decoded)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    return None


def _build_facilitator_payment_requirements(requirements: dict[str, Any], provider_name: str | None = None) -> dict[str, Any]:
    """Convert the public 402 challenge into the facilitator request shape.

    Coinbase Bazaar discovery depends on the facilitator seeing the same
    resource-level metadata that we advertise in the public 402 challenge.
    """
    normalized_provider = str(provider_name or "").strip().lower()
    amount = requirements.get("amount")
    if amount is None:
        amount = requirements.get("maxAmountRequired")
    if normalized_provider in {"coinbase", "circle_gateway"}:
        facilitator_requirements = dict(requirements)
        if amount is not None:
            facilitator_requirements["amount"] = amount
        return facilitator_requirements

    facilitator_requirements = {
        "scheme": requirements.get("scheme"),
        "network": requirements.get("network"),
        "asset": requirements.get("asset"),
        "amount": amount,
        "payTo": requirements.get("payTo"),
        "maxTimeoutSeconds": requirements.get("maxTimeoutSeconds"),
    }
    extra = requirements.get("extra")
    if isinstance(extra, dict) and extra:
        facilitator_requirements["extra"] = {
            key: value for key, value in extra.items() if key in {"name", "version"}
        }
    return facilitator_requirements


def _payment_payload_version(payment_payload: dict[str, Any] | None) -> int:
    """Infer which x402 version the client actually used on the retry."""
    if not isinstance(payment_payload, dict):
        return 2
    try:
        version = int(payment_payload.get("x402Version", 2))
    except Exception:
        return 2
    return 1 if version == 1 else 2


def _default_provider_for_payment_payload(payment_payload: dict[str, Any] | None, providers: list[str]) -> str | None:
    """Infer the most compatible facilitator when the client did not pin one.

    OWS currently discovers x402 services through Coinbase Bazaar and retries
    with legacy `X-PAYMENT` / `x402Version: 1` payloads. Prefer Coinbase first
    for those retries so we don't send a valid Coinbase-style proof into other
    facilitators that cannot verify it.
    """
    if _payment_payload_version(payment_payload) == 1 and "coinbase" in providers:
        return "coinbase"
    if "circle_gateway" in providers and isinstance(payment_payload, dict):
        accepted = payment_payload.get("accepted")
        if isinstance(accepted, dict):
            extra = accepted.get("extra")
            if isinstance(extra, dict) and str(extra.get("name") or "") == "GatewayWalletBatched":
                return "circle_gateway"
    return None


def _normalize_coinbase_v1_network(network: Any) -> Any:
    mapping = {
        "eip155:8453": "base",
        "eip155:84532": "base-sepolia",
    }
    key = str(network or "").strip()
    return mapping.get(key, network)


def _normalize_coinbase_v1_payment_payload(payment_payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payment_payload)
    normalized["network"] = _normalize_coinbase_v1_network(normalized.get("network"))
    return normalized


def _normalize_coinbase_v1_payment_requirements(payment_requirements: dict[str, Any]) -> dict[str, Any]:
    normalized = _public_payment_requirement(payment_requirements)
    normalized["network"] = _normalize_coinbase_v1_network(normalized.get("network"))
    return normalized


def _extract_tool_calls(body: dict) -> list[str]:
    """Extract tool names from JSON-RPC requests.

    - tools/call  -> [name]
    - tools/batch -> [name1, name2, ...]
    Otherwise -> []
    """
    method = body.get("method")
    params = body.get("params") or {}
    if method == "tools/call":
        if isinstance(params, dict):
            name = params.get("name")
            return [name] if isinstance(name, str) and name else []
        return []
    if method == "tools/batch":
        if not isinstance(params, dict):
            return []
        calls = params.get("calls") or []
        if not isinstance(calls, list):
            return []
        out: list[str] = []
        for c in calls[:50]:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            if isinstance(name, str) and name:
                out.append(name)
        return out
    return []


def _extract_call_args(body: dict, tool_name: str) -> dict[str, str]:
    """Best-effort extraction of call arguments for a given tool call."""
    params = body.get("params") or {}
    if not isinstance(params, dict):
        return {}

    if body.get("method") == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if isinstance(name, str) and name == tool_name and isinstance(args, dict):
            return args
        return {}

    if body.get("method") == "tools/batch":
        calls = params.get("calls") or []
        if not isinstance(calls, list):
            return {}
        for call in calls:
            if not isinstance(call, dict):
                continue
            if call.get("name") != tool_name:
                continue
            args = call.get("arguments") or {}
            if isinstance(args, dict):
                return args
            return {}

    return {}


async def _resolve_pricing_context(store: SessionStore, tool_name: str, args: dict[str, str], headers: dict[bytes, bytes]) -> tuple[dict[str, object], str | None]:
    """Resolve first_seen and effective pricing payload for a specific tool call."""
    agent_id = _first_text(
        args.get("agent_id"),
        args.get("agentId"),
        _mapping_text(args.get("metadata"), "agent_id", "agentId"),
        _mapping_text(args.get("configuration"), "agent_id", "agentId"),
    )

    if not agent_id:
        session_id = _first_text(
            args.get("session_id"),
            args.get("sessionId"),
            args.get("session_ref"),
            args.get("sessionRef"),
        )
        if session_id:
            try:
                session = await store.get_session(session_id)
                if isinstance(session, dict):
                    session_agent_id = session.get("agent_id")
                    if isinstance(session_agent_id, str):
                        agent_id = session_agent_id.strip()
            except Exception:
                agent_id = ""

    if not agent_id:
        header_agent_id = ""
        for key in (b"x-delx-agent-id", b"x-agent-id", b"x-openclaw-agent-id"):
            val = headers.get(key)
            if not val:
                continue
            try:
                header_agent_id = val.decode("utf-8", errors="ignore").strip()
            except Exception:
                header_agent_id = ""
            if header_agent_id:
                break
        agent_id = header_agent_id

    first_seen_at = None
    if agent_id:
        try:
            first_seen_at = await store.get_agent_first_seen(agent_id)
        except Exception:
            first_seen_at = None

    pricing_payload = get_tool_pricing_payload(tool_name, first_seen_at=first_seen_at)
    return pricing_payload, agent_id


class X402Middleware:
    """ASGI middleware that wraps the MCP endpoint with x402 payment gating."""

    def __init__(self, app, store: SessionStore, http_client: httpx.AsyncClient):
        self.app = app
        self.store = store
        self.http = http_client

    async def _safe_log_event(
        self,
        *,
        agent_id: str | None,
        event_type: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        aid = (agent_id or "").strip() or "anonymous"
        try:
            await self.store.log_event(
                agent_id=aid,
                event_type=event_type,
                session_id=(session_id or "").strip() or None,
                metadata=metadata or {},
            )
        except Exception:
            logger.warning(f"Failed to log x402 event: {event_type}")

    def _a2a_agent_id(self, headers: dict[bytes, bytes], rpc_body: dict[str, Any]) -> str:
        for key in (b"x-delx-agent-id", b"x-agent-id", b"x-openclaw-agent-id"):
            val = _header_text(headers, key)
            if val:
                return val[:120]
        params = rpc_body.get("params") or {}
        if isinstance(params, dict):
            for key in ("agent_id", "agentId"):
                val = str(params.get(key) or "").strip()
                if val:
                    return val[:120]
            metadata = params.get("metadata") or {}
            if isinstance(metadata, dict):
                val = str(metadata.get("agent_id") or metadata.get("agentId") or "").strip()
                if val:
                    return val[:120]
            config = params.get("configuration") or {}
            if isinstance(config, dict):
                val = str(config.get("agent_id") or config.get("agentId") or "").strip()
                if val:
                    return val[:120]
        return "anonymous"

    def _session_id_from_args(self, args: dict[str, Any]) -> str | None:
        sid = _first_text(
            args.get("session_id"),
            args.get("sessionId"),
            args.get("session_ref"),
            args.get("sessionRef"),
        )
        return sid or None

    def _tracking_session_id(self, tool_name: str, args: dict[str, Any]) -> str | None:
        session_id = self._session_id_from_args(args)
        if session_id:
            return session_id
        if tool_name != "generate_fleet_summary":
            return None
        controller_id = str(first_controller_id(args.get("controller_id"), args.get("controllerId")) or "").strip()[:120]
        if not controller_id:
            return None
        try:
            days_n = max(1, min(int(args.get("days") or 7), 30))
        except Exception:
            days_n = 7
        return f"controller:{controller_id}:{days_n}"

    async def _trial_status(self, agent_id: str, tool_name: str) -> dict[str, Any]:
        policy = trial_policy()
        limit = max(0, int(policy.get("free_recovery_calls", 0) or 0))
        eligible = bool(policy.get("enabled")) and bool(agent_id) and (agent_id != "anonymous") and is_trial_tool(tool_name)
        if not eligible or limit <= 0:
            return {"enabled": bool(policy.get("enabled")), "eligible": False, "used_calls": 0, "remaining_calls": 0, "limit": limit}

        if hasattr(self.store, "has_payment_history"):
            try:
                if bool(await self.store.has_payment_history(agent_id)):
                    return {"enabled": True, "eligible": False, "used_calls": limit, "remaining_calls": 0, "limit": limit}
            except Exception:
                pass

        used = 0
        if hasattr(self.store, "get_agent_event_total"):
            try:
                used = int(await self.store.get_agent_event_total(agent_id, "x402_trial_granted"))
            except Exception:
                used = 0
        remaining = max(0, limit - used)
        return {
            "enabled": True,
            "eligible": remaining > 0,
            "used_calls": used,
            "remaining_calls": remaining,
            "limit": limit,
        }

    async def _consume_trial_if_available(
        self,
        *,
        agent_id: str,
        tool_name: str,
        session_id: str | None,
        protocol: str,
        method: str,
        source: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        status = await self._trial_status(agent_id, tool_name)
        if not status.get("eligible"):
            return False, status
        used = int(status.get("used_calls", 0) or 0)
        limit = int(status.get("limit", 0) or 0)
        await self._safe_log_event(
            agent_id=agent_id,
            session_id=session_id,
            event_type="x402_trial_granted",
            metadata={
                "protocol": protocol,
                "method": method,
                "tool_name": tool_name,
                "source": source or protocol,
                "trial_used_index": used + 1,
                "trial_limit": limit,
            },
        )
        status["used_calls"] = used + 1
        status["remaining_calls"] = max(0, limit - (used + 1))
        status["eligible"] = status["remaining_calls"] > 0
        return True, status

    def _evaluation_status(
        self,
        *,
        scope: dict[str, Any],
        tool_name: str,
        source: str | None,
    ) -> dict[str, Any]:
        policy = evaluation_policy()
        if not bool(policy.get("active")) or not is_evaluation_tool(tool_name):
            return {
                "enabled": bool(policy.get("enabled")),
                "active": bool(policy.get("active")),
                "eligible": False,
                "matched_by": [],
                "client_ip": None,
                "cohort": policy.get("name"),
                "expires_utc": policy.get("expires_utc"),
            }

        client_ip = extract_client_ip_from_scope(scope)
        matched_by: list[str] = []
        cidrs = [str(item or "").strip() for item in list(policy.get("cidrs") or []) if str(item or "").strip()]
        for raw_cidr in cidrs:
            try:
                if client_ip and ipaddress.ip_address(client_ip) in ipaddress.ip_network(raw_cidr, strict=False):
                    matched_by.append(f"cidr:{raw_cidr}")
                    break
            except Exception:
                continue

        normalized_source = str(source or "").strip().lower()
        allowed_sources = {str(item or "").strip().lower() for item in list(policy.get("sources") or []) if str(item or "").strip()}
        if normalized_source and normalized_source in allowed_sources:
            matched_by.append(f"source:{normalized_source}")

        return {
            "enabled": bool(policy.get("enabled")),
            "active": bool(policy.get("active")),
            "eligible": bool(matched_by),
            "matched_by": matched_by,
            "client_ip": client_ip,
            "cohort": policy.get("name"),
            "expires_utc": policy.get("expires_utc"),
            "note": policy.get("note"),
        }

    async def _consume_evaluation_if_available(
        self,
        *,
        scope: dict[str, Any],
        agent_id: str,
        tool_name: str,
        session_id: str | None,
        protocol: str,
        method: str,
        source: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        status = self._evaluation_status(scope=scope, tool_name=tool_name, source=source)
        if not status.get("eligible"):
            return False, status

        await self._safe_log_event(
            agent_id=agent_id,
            session_id=session_id,
            event_type="x402_eval_granted",
            metadata={
                "protocol": protocol,
                "method": method,
                "tool_name": tool_name,
                "source": source or protocol,
                "cohort": status.get("cohort"),
                "matched_by": list(status.get("matched_by") or []),
                "client_ip": status.get("client_ip"),
                "expires_utc": status.get("expires_utc"),
            },
        )
        return True, status

    async def _coinbase_bazaar_state(self) -> tuple[int, set[str]]:
        verified_count = 0
        if coinbase_token_configured():
            getter = getattr(self.store, "get_x402_provider_verified_payment_count", None)
            if getter is not None:
                try:
                    verified_count = int(await getter("coinbase"))
                except Exception:
                    logger.warning("Failed direct Coinbase verified payment lookup for x402 challenge")
        audit_getter = getattr(self.store, "get_x402_audit", None)
        if audit_getter is None:
            return verified_count, set()
        try:
            audit = await audit_getter(30)
            if isinstance(audit, dict):
                bazaar = audit.get("bazaar") if isinstance(audit.get("bazaar"), dict) else {}
                if verified_count <= 0:
                    verified_count = int(bazaar.get("coinbase_verified_payments_all_time", 0) or 0)
                indexed_tools = {
                    str(tool_name or "").strip()
                    for tool_name in (bazaar.get("indexed_tools_publicly") or [])
                    if str(tool_name or "").strip()
                }
                return verified_count, indexed_tools
        except Exception:
            logger.warning("Failed audit fallback for Coinbase verified payment count in x402 challenge")
        return verified_count, set()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        mcp_paths = {"/mcp", "/mcp/messages", "/v1/mcp", "/v1/mcp/messages"}
        a2a_paths = {"/a2a", "/v1/a2a"}
        rest_tool_name = _rest_premium_tool_name(path)
        if rest_tool_name and is_all_free_mode() and not should_enforce_utility_charge(rest_tool_name):
            return await self.app(scope, receive, send)
        # Intercept MCP/A2A only on POST, but premium REST resources on GET or POST.
        if path not in (mcp_paths | a2a_paths) and not rest_tool_name:
            return await self.app(scope, receive, send)
        if method != "POST" and not rest_tool_name:
            return await self.app(scope, receive, send)

        # Read the full request body
        body_parts = []
        while True:
            msg = await receive()
            body_parts.append(msg.get("body", b""))
            if not msg.get("more_body", False):
                break
        raw_body = b"".join(body_parts)

        if rest_tool_name:
            if method == "GET":
                rest_body = {}
            else:
                try:
                    rest_body = json.loads(raw_body or b"{}")
                except (json.JSONDecodeError, ValueError):
                    return await self._replay_request(scope, raw_body, send)
                if not isinstance(rest_body, dict):
                    return await self._replay_request(scope, raw_body, send)
            headers = dict(scope.get("headers", []))
            return await self._handle_rest_premium_request(
                scope,
                send,
                raw_body,
                rest_body,
                rest_tool_name,
                headers=headers,
            )

        # Try to parse JSON-RPC
        try:
            rpc_body = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            # Not valid JSON - pass through (could be SSE init or other)
            return await self._replay_request(scope, raw_body, send)

        # JSON-RPC batch arrays are handled downstream by the MCP edge handler.
        # Do not try to parse tool calls here.
        if isinstance(rpc_body, list):
            return await self._replay_request(scope, raw_body, send)

        headers = dict(scope.get("headers", []))
        if path in a2a_paths:
            return await self._handle_a2a_request(scope, send, raw_body, rpc_body, headers=headers)

        tool_names = _extract_tool_calls(rpc_body)

        # Not a tool call/batch (e.g. initialize, tools/list) -> pass through
        if not tool_names:
            return await self._replay_request(scope, raw_body, send)

        # Headers are needed for both attribution fallback and x402 payment verification.
        headers = dict(scope.get("headers", []))

        # Cross-protocol handoff: let clients provide session_id via header so they
        # don't have to thread it through every tool call.
        #
        # NOTE: This is best-effort. We still recommend passing session_id explicitly.
        def _get_header(name: bytes) -> str:
            v = headers.get(name, b"")
            try:
                return v.decode("utf-8", errors="ignore")
            except Exception:
                return ""

        header_sid = (_get_header(b"x-delx-session-id") or _get_header(b"x-session-id")).strip()
        if header_sid:
            try:
                params = rpc_body.get("params") or {}
                if isinstance(params, dict):
                    if rpc_body.get("method") == "tools/call":
                        arguments = params.get("arguments") or {}
                        if isinstance(arguments, dict) and not arguments.get("session_id"):
                            arguments["session_id"] = header_sid[:64]
                            params["arguments"] = arguments
                            rpc_body["params"] = params
                            raw_body = json.dumps(rpc_body).encode("utf-8")
                    elif rpc_body.get("method") == "tools/batch":
                        calls = params.get("calls") or []
                        if isinstance(calls, list):
                            changed = False
                            for c in calls:
                                if not isinstance(c, dict):
                                    continue
                                arguments = c.get("arguments") or {}
                                if isinstance(arguments, dict) and not arguments.get("session_id"):
                                    arguments["session_id"] = header_sid[:64]
                                    c["arguments"] = arguments
                                    changed = True
                            if changed:
                                params["calls"] = calls
                                rpc_body["params"] = params
                                raw_body = json.dumps(rpc_body).encode("utf-8")
            except Exception:
                pass

        # Best-effort attribution fallback: if the agent doesn't pass `source` explicitly,
        # infer it from headers and inject into start_therapy_session arguments.
        #
        # This runs before free-tool pass-through so we still attribute free sessions.
        if "start_therapy_session" in set(tool_names):
            inferred = _infer_source(headers)
            if inferred:
                try:
                    params = rpc_body.get("params") or {}
                    if isinstance(params, dict):
                        if rpc_body.get("method") == "tools/call":
                            arguments = params.get("arguments") or {}
                            if isinstance(arguments, dict) and not arguments.get("source"):
                                arguments["source"] = inferred
                                params["arguments"] = arguments
                                rpc_body["params"] = params
                                raw_body = json.dumps(rpc_body).encode("utf-8")
                        elif rpc_body.get("method") == "tools/batch":
                            calls = params.get("calls") or []
                            if isinstance(calls, list):
                                changed = False
                                for c in calls:
                                    if not isinstance(c, dict):
                                        continue
                                    if c.get("name") != "start_therapy_session":
                                        continue
                                    arguments = c.get("arguments") or {}
                                    if isinstance(arguments, dict) and not arguments.get("source"):
                                        arguments["source"] = inferred
                                        c["arguments"] = arguments
                                        changed = True
                                if changed:
                                    params["calls"] = calls
                                    rpc_body["params"] = params
                                    raw_body = json.dumps(rpc_body).encode("utf-8")
                except Exception:
                    # Attribution is best-effort; never fail the request because of it.
                    pass

        # Batch does not support paid tools yet (multi-payment semantics). Keep it simple.
        if rpc_body.get("method") == "tools/batch":
            # Resolve effective price with campaign/grandfathering context per tool.
            paid = []
            for n in tool_names:
                args = _extract_call_args(rpc_body, n)
                pricing_payload, _ = await _resolve_pricing_context(self.store, n, args, headers)
                if int(pricing_payload.get("price_cents", 0) or 0) > 0:
                    paid.append(n)
            if paid:
                agent_id = _header_text(headers, b"x-delx-agent-id") or _header_text(headers, b"x-agent-id") or "anonymous"
                await self._safe_log_event(
                    agent_id=agent_id,
                    event_type="x402_batch_paid_unsupported",
                    metadata={"protocol": "mcp", "methods": paid[:20]},
                )
                resp_body = json.dumps(
                    {
                        "error": "Batch with paid tools is not supported. Call paid tools individually.",
                        "paid_tools": paid[:10],
                    }
                ).encode()
                await send({
                    "type": "http.response.start",
                    "status": 402,
                    "headers": [
                        [b"content-type", b"application/json"],
                        [b"x-402-version", b"2"],
                    ],
                })
                await send({"type": "http.response.body", "body": resp_body})
                return
            return await self._replay_request(scope, raw_body, send)

        # Free tool -> pass through
        tool_name = tool_names[0]
        args = _extract_call_args(rpc_body, tool_name)
        pricing_payload, agent_id = await _resolve_pricing_context(self.store, tool_name, args, headers)
        if should_enforce_utility_charge(tool_name):
            pricing_payload = get_metered_utility_pricing_payload(tool_name)
        if int(pricing_payload.get("price_cents", 0) or 0) == 0:
            return await self._replay_request(scope, raw_body, send)

        # Paid tool - check for payment header
        session_id = self._tracking_session_id(tool_name, args)
        payment_header = _extract_payment_header(headers)
        source = _infer_source(headers) or "mcp"
        attribution = _request_attribution_metadata(scope=scope, headers=headers, source=source)

        if not payment_header:
            evaluation_used, evaluation_state = await self._consume_evaluation_if_available(
                scope=scope,
                agent_id=agent_id or "anonymous",
                tool_name=tool_name,
                session_id=session_id,
                protocol="mcp",
                method="tools/call",
                source=source,
            )
            if evaluation_used:
                logger.info(
                    "x402 evaluation grant for %s via %s",
                    tool_name,
                    ",".join(list(evaluation_state.get("matched_by") or [])) or "policy",
                )
                return await self._replay_request(scope, raw_body, send)

            trial_used, trial_state = await self._consume_trial_if_available(
                agent_id=agent_id or "anonymous",
                tool_name=tool_name,
                session_id=session_id,
                protocol="mcp",
                method="tools/call",
                source=source,
            )
            if trial_used:
                logger.info(
                    "x402 trial granted for %s (%s/%s used)",
                    tool_name,
                    int(trial_state.get("used_calls", 0) or 0),
                    int(trial_state.get("limit", 0) or 0),
                )
                return await self._replay_request(scope, raw_body, send)

            # Return 402 Payment Required
            pricing_payload, agent_id = await _resolve_pricing_context(self.store, tool_name, args, headers)
            if should_enforce_utility_charge(tool_name):
                pricing_payload = get_metered_utility_pricing_payload(tool_name)
            await self._safe_log_event(
                agent_id=agent_id or "anonymous",
                session_id=session_id,
                event_type="x402_payment_required",
                metadata={
                    "protocol": "mcp",
                    "method": "tools/call",
                    "tool_name": tool_name,
                    "price_cents": int(pricing_payload.get("price_cents", 0) or 0),
                    "source": source,
                    **attribution,
                },
            )
            if should_enforce_utility_charge(tool_name):
                # Utility 402 challenges sit on Delx's own discovery surface; do not
                # block every paid MCP call on a full external marketplace scan.
                coinbase_verified_payments, indexed_tools = None, set()
            else:
                coinbase_verified_payments, indexed_tools = await self._coinbase_bazaar_state()
            resp_payload = {
                **_build_402_response(
                    tool_name,
                    pricing_payload=pricing_payload,
                    trial=await self._trial_status(agent_id or "anonymous", tool_name),
                    coinbase_verified_payments=coinbase_verified_payments,
                    indexed_publicly=tool_name in indexed_tools,
                ),
                "tool_name": tool_name,
            }
            resp_body = _build_payment_required_body_from_payload(resp_payload)
            await send({
                "type": "http.response.start",
                "status": 402,
                "headers": _asgi_headers(
                    _build_402_http_headers(
                        tool_name,
                        pricing_payload=pricing_payload,
                        trial=resp_payload.get("trial"),
                        coinbase_verified_payments=coinbase_verified_payments,
                        indexed_publicly=tool_name in indexed_tools,
                    )
                ),
            })
            await send({
                "type": "http.response.body",
                "body": resp_body,
            })
            logger.info(f"402 returned for tool: {tool_name}")
            return

        await self._safe_log_event(
            agent_id=agent_id or "anonymous",
            session_id=session_id,
            event_type="x402_payment_attempted",
            metadata={
                "protocol": "mcp",
                "method": "tools/call",
                "tool_name": tool_name,
                "source": source,
                "provider_candidates": _provider_order(pricing_payload),
                **attribution,
            },
        )
        # Verify payment with facilitator
        preferred_provider = _header_text(headers, b"x-payment-provider") or _header_text(headers, b"x-402-provider")
        tx_hash, provider_name, failure = await self._verify_and_settle_payment(
            payment_header,
            tool_name,
            pricing_payload,
            preferred_provider=preferred_provider or None,
        )
        if not tx_hash:
            failure = dict(failure or {})
            await self._safe_log_event(
                agent_id=agent_id or "anonymous",
                session_id=session_id,
                event_type="x402_verify_failed",
                metadata={
                    "protocol": "mcp",
                    "method": "tools/call",
                    "tool_name": tool_name,
                    "source": source,
                    "provider_candidates": _provider_order(pricing_payload),
                    "preferred_provider": preferred_provider or None,
                    "failure_code": str(failure.get("code") or "verification_failed"),
                    "provider_attempts": list(failure.get("provider_attempts") or []),
                    **attribution,
                },
            )
            if should_enforce_utility_charge(tool_name):
                coinbase_verified_payments, indexed_tools = None, set()
            else:
                coinbase_verified_payments, indexed_tools = await self._coinbase_bazaar_state()
            resp_payload = {
                **_build_verify_failed_response(
                    tool_name,
                    pricing_payload=pricing_payload,
                    trial=await self._trial_status(agent_id or "anonymous", tool_name),
                    failure=failure,
                    preferred_provider=preferred_provider or None,
                    coinbase_verified_payments=coinbase_verified_payments,
                    indexed_publicly=tool_name in indexed_tools,
                ),
                "tool_name": tool_name,
            }
            error_body = _build_payment_required_body_from_payload(resp_payload)
            await send({
                "type": "http.response.start",
                "status": 402,
                "headers": _asgi_headers(_build_payment_required_headers_from_payload(resp_payload)),
            })
            await send({
                "type": "http.response.body",
                "body": error_body,
            })
            logger.warning(f"Payment verification failed for tool: {tool_name}")
            return

        # Payment verified - log and pass through
        price_cents = int(pricing_payload.get("price_cents", 0) or 0)
        await self.store.log_payment(
            tool_name,
            price_cents / 100,
            tx_hash=tx_hash,
            session_id=session_id,
        )
        await self._safe_log_event(
            agent_id=agent_id or "anonymous",
            session_id=session_id,
            event_type="x402_payment_verified",
            metadata={
                "protocol": "mcp",
                "method": "tools/call",
                "tool_name": tool_name,
                "source": source,
                "price_cents": price_cents,
                "provider": provider_name,
                "tx_hash": tx_hash,
                "session_ref": session_id,
                **attribution,
            },
        )
        logger.info(f"Payment verified for tool: {tool_name} (${price_cents / 100:.2f})")

        return await self._replay_request(
            scope,
            raw_body,
            send,
            extra_headers=_asgi_headers(_build_payment_success_headers(provider_name=provider_name or "unknown", tx_hash=tx_hash)),
        )

    async def _handle_a2a_request(
        self,
        scope,
        send,
        raw_body: bytes,
        rpc_body: dict[str, Any],
        *,
        headers: dict[bytes, bytes],
    ):
        method = str(rpc_body.get("method") or "").strip()
        # Keep discovery/management methods free to avoid onboarding dead-ends.
        method_to_pricing = {
            "message/send": ("a2a_message_send", "https://delx.ai/a2a/methods/message_send"),
            "heartbeat/bundle": ("a2a_heartbeat_bundle", "https://delx.ai/a2a/methods/heartbeat_bundle"),
        }
        if method not in method_to_pricing:
            return await self._replay_request(scope, raw_body, send)

        pricing_key, resource_url = method_to_pricing[method]
        pricing_payload = get_tool_pricing_payload(pricing_key)
        if int(pricing_payload.get("price_cents", 0) or 0) == 0:
            return await self._replay_request(scope, raw_body, send)

        payment_header = _extract_payment_header(headers)
        agent_id = self._a2a_agent_id(headers, rpc_body)
        source = _infer_source(headers) or "a2a"
        attribution = _request_attribution_metadata(scope=scope, headers=headers, source=source)
        if not payment_header:
            evaluation_used, evaluation_state = await self._consume_evaluation_if_available(
                scope=scope,
                agent_id=agent_id,
                tool_name=pricing_key,
                session_id=None,
                protocol="a2a",
                method=method,
                source=source,
            )
            if evaluation_used:
                logger.info(
                    "x402 evaluation grant for A2A %s via %s",
                    method,
                    ",".join(list(evaluation_state.get("matched_by") or [])) or "policy",
                )
                return await self._replay_request(scope, raw_body, send)

            trial_used, trial_state = await self._consume_trial_if_available(
                agent_id=agent_id,
                tool_name=pricing_key,
                session_id=None,
                protocol="a2a",
                method=method,
                source=source,
            )
            if trial_used:
                logger.info(
                    "x402 trial granted for A2A %s (%s/%s used)",
                    method,
                    int(trial_state.get("used_calls", 0) or 0),
                    int(trial_state.get("limit", 0) or 0),
                )
                return await self._replay_request(scope, raw_body, send)

            await self._safe_log_event(
                agent_id=agent_id,
                event_type="x402_payment_required",
                metadata={
                    "protocol": "a2a",
                    "method": method,
                    "price_cents": int(pricing_payload.get("price_cents", 0) or 0),
                    "source": source,
                    **attribution,
                },
            )
            if should_enforce_utility_charge(tool_name):
                coinbase_verified_payments, indexed_tools = None, set()
            else:
                coinbase_verified_payments, indexed_tools = await self._coinbase_bazaar_state()
            resp_payload = {
                **_build_402_response(
                    method,
                    pricing_payload=pricing_payload,
                    resource=resource_url,
                    trial=await self._trial_status(agent_id, pricing_key),
                    coinbase_verified_payments=coinbase_verified_payments,
                    indexed_publicly=method in indexed_tools,
                ),
                "method": method,
            }
            resp_body = _build_payment_required_body_from_payload(resp_payload)
            await send(
                {
                    "type": "http.response.start",
                    "status": 402,
                    "headers": _asgi_headers(
                        _build_402_http_headers(
                            method,
                            pricing_payload=pricing_payload,
                            resource=resource_url,
                            trial=resp_payload.get("trial"),
                            coinbase_verified_payments=coinbase_verified_payments,
                            indexed_publicly=method in indexed_tools,
                        )
                    ),
                }
            )
            await send({"type": "http.response.body", "body": resp_body})
            logger.info(f"402 returned for A2A method: {method}")
            return

        await self._safe_log_event(
            agent_id=agent_id,
            event_type="x402_payment_attempted",
            metadata={
                "protocol": "a2a",
                "method": method,
                "source": source,
                "provider_candidates": _provider_order(pricing_payload),
                **attribution,
            },
        )
        preferred_provider = _header_text(headers, b"x-payment-provider") or _header_text(headers, b"x-402-provider")
        tx_hash, provider_name, failure = await self._verify_and_settle_payment(
            payment_header,
            method,
            pricing_payload,
            resource=resource_url,
            preferred_provider=preferred_provider or None,
        )
        if not tx_hash:
            failure = dict(failure or {})
            await self._safe_log_event(
                agent_id=agent_id,
                event_type="x402_verify_failed",
                metadata={
                    "protocol": "a2a",
                    "method": method,
                    "source": source,
                    "provider_candidates": _provider_order(pricing_payload),
                    "preferred_provider": preferred_provider or None,
                    "failure_code": str(failure.get("code") or "verification_failed"),
                    "provider_attempts": list(failure.get("provider_attempts") or []),
                    **attribution,
                },
            )
            coinbase_verified_payments, indexed_tools = await self._coinbase_bazaar_state()
            resp_payload = {
                **_build_verify_failed_response(
                    method,
                    pricing_payload=pricing_payload,
                    resource=resource_url,
                    trial=await self._trial_status(agent_id, pricing_key),
                    failure=failure,
                    preferred_provider=preferred_provider or None,
                    coinbase_verified_payments=coinbase_verified_payments,
                    indexed_publicly=method in indexed_tools,
                ),
                "method": method,
            }
            error_body = _build_payment_required_body_from_payload(resp_payload)
            await send(
                {
                    "type": "http.response.start",
                    "status": 402,
                    "headers": _asgi_headers(_build_payment_required_headers_from_payload(resp_payload)),
                }
            )
            await send({"type": "http.response.body", "body": error_body})
            logger.warning(f"Payment verification failed for A2A method: {method}")
            return

        price_cents = int(pricing_payload.get("price_cents", 0) or 0)
        await self.store.log_payment(method, price_cents / 100, tx_hash=tx_hash)
        await self._safe_log_event(
            agent_id=agent_id,
            event_type="x402_payment_verified",
            metadata={
                "protocol": "a2a",
                "method": method,
                "source": source,
                "price_cents": price_cents,
                "provider": provider_name,
                "tx_hash": tx_hash,
                **attribution,
            },
        )
        logger.info(f"Payment verified for A2A method: {method} (${price_cents / 100:.2f})")
        return await self._replay_request(
            scope,
            raw_body,
            send,
            extra_headers=_asgi_headers(_build_payment_success_headers(provider_name=provider_name or "unknown", tx_hash=tx_hash)),
        )

    async def _handle_rest_premium_request(
        self,
        scope,
        send,
        raw_body: bytes,
        body: dict[str, Any],
        tool_name: str,
        *,
        headers: dict[bytes, bytes],
    ):
        args = _normalize_rest_premium_args(scope, body, headers)
        args = {**_rest_query_params(scope), **args}
        missing = _rest_missing_required_fields(tool_name, args)
        request_path = str(scope.get("path") or "")
        is_canonical_utility_route = request_path.startswith(("/api/v1/utilities/", "/v1/utilities/"))
        if missing and is_canonical_utility_route:
            error_body = json.dumps(_build_rest_missing_required_payload(tool_name, missing)).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 422,
                    "headers": [[b"content-type", b"application/json"]],
                }
            )
            await send({"type": "http.response.body", "body": error_body})
            return
        missing_probe = bool(missing) and _is_rest_premium_discovery_probe(scope, body, headers)
        if missing and not missing_probe:
            error_body = json.dumps(_build_rest_missing_required_payload(tool_name, missing)).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [[b"content-type", b"application/json"]],
                }
            )
            await send({"type": "http.response.body", "body": error_body})
            return

        pricing_payload, agent_id = await _resolve_pricing_context(self.store, tool_name, args, headers)
        if should_enforce_utility_charge(tool_name):
            pricing_payload = get_metered_utility_pricing_payload(tool_name)
        if int(pricing_payload.get("price_cents", 0) or 0) == 0:
            return await self._replay_request(scope, raw_body, send)

        session_id = self._tracking_session_id(tool_name, args)
        if request_path.startswith(("/api/v1/utilities/", "/v1/utilities/")):
            resource_url = f"https://api.delx.ai{request_path}"
        else:
            resource_url = _rest_premium_resource_url(tool_name)
        payment_header = _extract_payment_header(headers)
        mpp_authorization = _extract_mpp_authorization(headers)
        source = _infer_source(headers) or "rest"
        attribution = _request_attribution_metadata(scope=scope, headers=headers, source=source)

        if not payment_header and not mpp_authorization:
            if not missing_probe:
                evaluation_used, evaluation_state = await self._consume_evaluation_if_available(
                    scope=scope,
                    agent_id=agent_id or "anonymous",
                    tool_name=tool_name,
                    session_id=session_id,
                    protocol="rest",
                    method=scope.get("path", ""),
                    source=source,
                )
                if evaluation_used:
                    logger.info(
                        "x402 evaluation grant for REST %s via %s",
                        tool_name,
                        ",".join(list(evaluation_state.get("matched_by") or [])) or "policy",
                    )
                    return await self._replay_request(scope, raw_body, send)

                trial_used, trial_state = await self._consume_trial_if_available(
                    agent_id=agent_id or "anonymous",
                    tool_name=tool_name,
                    session_id=session_id,
                    protocol="rest",
                    method=scope.get("path", ""),
                    source=source,
                )
                if trial_used:
                    return await self._replay_request(scope, raw_body, send)

            await self._safe_log_event(
                agent_id=agent_id or "anonymous",
                session_id=session_id,
                event_type="x402_payment_required",
                metadata={
                    "protocol": "rest",
                    "method": scope.get("path", ""),
                    "tool_name": tool_name,
                    "price_cents": int(pricing_payload.get("price_cents", 0) or 0),
                    "source": source,
                    "payment_protocol": "x402_or_mpp" if _mpp_is_enabled() else "x402",
                    "validation_state": "missing_required_probe" if missing_probe else "ready_for_payment",
                    **attribution,
                },
            )
            coinbase_verified_payments, indexed_tools = await self._coinbase_bazaar_state()
            resp_payload = {
                **_build_402_response(
                    tool_name,
                    pricing_payload=pricing_payload,
                    resource=resource_url,
                    trial=await self._trial_status(agent_id or "anonymous", tool_name),
                    coinbase_verified_payments=coinbase_verified_payments,
                    indexed_publicly=tool_name in indexed_tools,
                ),
                "tool_name": tool_name,
            }
            if missing_probe:
                resp_payload["validation_error"] = _build_rest_missing_required_payload(tool_name, missing)
            resp_body = _build_payment_required_body_from_payload(resp_payload)
            await send(
                {
                    "type": "http.response.start",
                    "status": 402,
                    "headers": _asgi_headers(
                        _build_402_http_headers(
                            tool_name,
                            pricing_payload=pricing_payload,
                            resource=resource_url,
                            trial=resp_payload.get("trial"),
                            coinbase_verified_payments=coinbase_verified_payments,
                            indexed_publicly=tool_name in indexed_tools,
                            include_mpp=True,
                        )
                    ),
                }
            )
            await send({"type": "http.response.body", "body": resp_body})
            return

        if mpp_authorization and not payment_header:
            await self._safe_log_event(
                agent_id=agent_id or "anonymous",
                session_id=session_id,
                event_type="x402_payment_attempted",
                metadata={
                    "protocol": "rest",
                    "method": scope.get("path", ""),
                    "tool_name": tool_name,
                    "source": source,
                    "payment_protocol": "mpp",
                    "provider_candidates": ["tempo"],
                    **attribution,
                },
            )
            receipt_header, tx_hash, provider_name, failure = await self._verify_and_settle_mpp_payment(
                mpp_authorization,
                tool_name,
                pricing_payload,
                resource=resource_url,
            )
            if not tx_hash:
                failure = dict(failure or {})
                await self._safe_log_event(
                    agent_id=agent_id or "anonymous",
                    session_id=session_id,
                    event_type="x402_verify_failed",
                    metadata={
                        "protocol": "rest",
                        "method": scope.get("path", ""),
                        "tool_name": tool_name,
                        "source": source,
                        "payment_protocol": "mpp",
                        "provider_candidates": ["tempo"],
                        "preferred_provider": "tempo",
                        "failure_code": str(failure.get("code") or "verification_failed"),
                        **attribution,
                    },
                )
                coinbase_verified_payments, indexed_tools = await self._coinbase_bazaar_state()
                resp_payload = {
                    **_build_verify_failed_response(
                        tool_name,
                        pricing_payload=pricing_payload,
                        resource=resource_url,
                        trial=await self._trial_status(agent_id or "anonymous", tool_name),
                        failure=failure,
                        preferred_provider="tempo",
                        coinbase_verified_payments=coinbase_verified_payments,
                        indexed_publicly=tool_name in indexed_tools,
                    ),
                    "tool_name": tool_name,
                }
                error_body = _build_payment_required_body_from_payload(resp_payload)
                await send(
                    {
                        "type": "http.response.start",
                        "status": 402,
                        "headers": _asgi_headers(
                            _build_payment_required_headers_from_payload(
                                resp_payload,
                                tool_name=tool_name,
                                pricing_payload=pricing_payload,
                                resource=resource_url,
                                include_mpp=True,
                            )
                        ),
                    }
                )
                await send({"type": "http.response.body", "body": error_body})
                return

            price_cents = int(pricing_payload.get("price_cents", 0) or 0)
            await self.store.log_payment(
                tool_name,
                price_cents / 100,
                tx_hash=tx_hash,
                session_id=session_id,
            )
            await self._safe_log_event(
                agent_id=agent_id or "anonymous",
                session_id=session_id,
                event_type="x402_payment_verified",
                metadata={
                    "protocol": "rest",
                    "method": scope.get("path", ""),
                    "tool_name": tool_name,
                    "source": source,
                    "payment_protocol": "mpp",
                    "price_cents": price_cents,
                    "provider": provider_name or "tempo",
                    "tx_hash": tx_hash,
                    "session_ref": session_id,
                    **attribution,
                },
            )

            return await self._replay_request(
                scope,
                raw_body,
                send,
                extra_headers=_asgi_headers(
                    _build_payment_success_headers(
                        provider_name=provider_name or "tempo",
                        tx_hash=tx_hash,
                        mpp_payment_receipt=receipt_header,
                    )
                ),
            )

        await self._safe_log_event(
            agent_id=agent_id or "anonymous",
            session_id=session_id,
            event_type="x402_payment_attempted",
            metadata={
                "protocol": "rest",
                "method": scope.get("path", ""),
                "tool_name": tool_name,
                "source": source,
                "payment_protocol": "x402",
                "provider_candidates": _provider_order(pricing_payload),
                **attribution,
            },
        )
        preferred_provider = _header_text(headers, b"x-payment-provider") or _header_text(headers, b"x-402-provider")
        tx_hash, provider_name, failure = await self._verify_and_settle_payment(
            payment_header,
            tool_name,
            pricing_payload,
            resource=resource_url,
            preferred_provider=preferred_provider or None,
        )
        if not tx_hash:
            failure = dict(failure or {})
            await self._safe_log_event(
                agent_id=agent_id or "anonymous",
                session_id=session_id,
                event_type="x402_verify_failed",
                metadata={
                    "protocol": "rest",
                    "method": scope.get("path", ""),
                    "tool_name": tool_name,
                    "source": source,
                    "payment_protocol": "x402",
                    "provider_candidates": _provider_order(pricing_payload),
                    "preferred_provider": preferred_provider or None,
                    "failure_code": str(failure.get("code") or "verification_failed"),
                    "provider_attempts": list(failure.get("provider_attempts") or []),
                    **attribution,
                },
            )
            coinbase_verified_payments, indexed_tools = await self._coinbase_bazaar_state()
            resp_payload = {
                **_build_verify_failed_response(
                    tool_name,
                    pricing_payload=pricing_payload,
                    resource=resource_url,
                    trial=await self._trial_status(agent_id or "anonymous", tool_name),
                    failure=failure,
                    preferred_provider=preferred_provider or None,
                    coinbase_verified_payments=coinbase_verified_payments,
                    indexed_publicly=tool_name in indexed_tools,
                ),
                "tool_name": tool_name,
            }
            error_body = _build_payment_required_body_from_payload(resp_payload)
            await send(
                {
                    "type": "http.response.start",
                    "status": 402,
                    "headers": _asgi_headers(
                        _build_payment_required_headers_from_payload(
                            resp_payload,
                            tool_name=tool_name,
                            pricing_payload=pricing_payload,
                            resource=resource_url,
                            include_mpp=True,
                        )
                    ),
                }
            )
            await send({"type": "http.response.body", "body": error_body})
            return

        price_cents = int(pricing_payload.get("price_cents", 0) or 0)
        await self.store.log_payment(
            tool_name,
            price_cents / 100,
            tx_hash=tx_hash,
            session_id=session_id,
        )
        await self._safe_log_event(
            agent_id=agent_id or "anonymous",
            session_id=session_id,
            event_type="x402_payment_verified",
            metadata={
                "protocol": "rest",
                "method": scope.get("path", ""),
                "tool_name": tool_name,
                "source": source,
                "payment_protocol": "x402",
                "price_cents": price_cents,
                "provider": provider_name,
                "tx_hash": tx_hash,
                "session_ref": session_id,
                **attribution,
            },
        )

        return await self._replay_request(
            scope,
            raw_body,
            send,
            extra_headers=_asgi_headers(_build_payment_success_headers(provider_name=provider_name or "unknown", tx_hash=tx_hash)),
        )

    async def _verify_and_settle_mpp_payment(
        self,
        authorization_header: str,
        tool_name: str,
        pricing_payload: dict[str, object] | None = None,
        *,
        resource: str | None = None,
    ) -> tuple[str | None, str | None, str | None, dict[str, Any] | None]:
        try:
            if not _mpp_is_enabled():
                return None, None, None, {
                    "code": "mpp_not_enabled",
                    "message": "MPP is not configured on this server.",
                    "retryable": False,
                }
            if not str(authorization_header or "").strip():
                return None, None, None, {
                    "code": "missing_authorization_header",
                    "message": "Authorization header was empty or missing.",
                    "retryable": True,
                }

            from mpp import Challenge
            from mpp.methods.tempo import ChargeIntent, tempo
            from mpp.server import Mpp
            _patch_mpp_server_authorization_parser()

            request, description = _mpp_build_charge_request(
                tool_name,
                pricing_payload=pricing_payload,
                resource=resource,
            )
            chain_id = _mpp_chain_id()
            rpc_url = str(settings.MPP_TEMPO_RPC_URL or "").strip() or None
            method = tempo(
                intents={"charge": ChargeIntent(http_client=self.http, timeout=20.0)},
                chain_id=chain_id,
                rpc_url=rpc_url,
                currency=str(request.get("currency") or "").strip() or None,
                recipient=str(request.get("recipient") or "").strip() or None,
                client_id=str(settings.MPP_TEMPO_CLIENT_ID or "").strip() or None,
            )
            mpp = Mpp.create(
                method=method,
                realm=_mpp_realm(),
                secret_key=str(settings.MPP_SECRET_KEY or "").strip(),
            )
            amount_human = str((pricing_payload or {}).get("price_usdc") or "0.00")
            result = await mpp.charge(
                authorization_header,
                amount_human,
                currency=str(request.get("currency") or "").strip() or None,
                recipient=str(request.get("recipient") or "").strip() or None,
                description=description,
                fee_payer=bool(settings.MPP_TEMPO_FEE_PAYER),
                chain_id=chain_id,
                extra=dict(request.get("extra") or {}),
            )
            if isinstance(result, Challenge):
                return None, None, None, {
                    "code": "mpp_verification_failed",
                    "message": "MPP credential was missing, invalid, expired, or mismatched for this request.",
                    "retryable": True,
                    "www_authenticate": result.to_www_authenticate(_mpp_realm()),
                }

            _, receipt = result
            receipt_header = receipt.to_payment_receipt()
            reference = str(getattr(receipt, "reference", "") or "").strip()
            method_name = str(getattr(receipt, "method", "") or "tempo").strip() or "tempo"
            if not reference:
                return None, None, None, {
                    "code": "mpp_missing_reference",
                    "message": "MPP verification succeeded but receipt.reference was empty.",
                    "retryable": False,
                }
            return receipt_header, reference, method_name, None
        except Exception as exc:
            logger.warning("MPP verification failed for %s: %s", tool_name, exc)
            return None, None, None, {
                "code": "mpp_exception",
                "message": str(exc)[:200],
                "retryable": True,
            }

    async def _verify_and_settle_payment(
        self,
        payment_header: str,
        tool_name: str,
        pricing_payload: dict[str, object] | None = None,
        *,
        resource: str | None = None,
        preferred_provider: str | None = None,
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        """Verify and settle payment via facilitator.

        Returns the on-chain transaction hash/id if settlement succeeds.
        """
        try:
            raw_payment_header = str(payment_header or "").strip()
            if not raw_payment_header:
                logger.warning("Invalid payment signature header format")
                return None, None, {
                    "code": "missing_payment_header",
                    "message": "PAYMENT-SIGNATURE header was empty or missing.",
                    "retryable": True,
                    "provider_attempts": [],
                }
            payment_payload = _decode_payment_header(raw_payment_header)
            if not payment_payload:
                logger.warning("Failed to decode payment signature payload")
                return None, None, {
                    "code": "invalid_payment_header",
                    "message": "PAYMENT-SIGNATURE could not be decoded as JSON/base64 JSON.",
                    "retryable": True,
                    "provider_attempts": [],
                }
            request_x402_version = _payment_payload_version(payment_payload)

            providers = _provider_order(pricing_payload)
            if preferred_provider and preferred_provider in providers:
                # Respect explicit routing so agents can deterministically bind
                # retries to the facilitator they selected from accepts[].
                providers = [preferred_provider]
            else:
                default_provider = _default_provider_for_payment_payload(payment_payload, providers)
                if default_provider:
                    providers = [default_provider]
            provider_candidates = _provider_requirement_candidates(providers)
            provider_attempts: list[dict[str, Any]] = []

            for provider_name, provider_accept in provider_candidates:
                provider = _provider_config(provider_name)
                facilitator_url = str(provider.get("facilitator_url") or "").rstrip("/")
                if not facilitator_url:
                    provider_attempts.append(
                        {
                            "provider": provider_name,
                            "network": str(provider_accept.get("network") or provider.get("network") or ""),
                            "stage": "config",
                            "reason": "missing_facilitator_url",
                        }
                    )
                    continue
                payment_requirements = _build_payment_requirements(
                    tool_name,
                    provider_name=provider_name,
                    provider_accept=provider_accept,
                    pricing_payload=pricing_payload,
                    resource=resource,
                )
                facilitator_payment_payload = payment_payload
                facilitator_requirements = _build_facilitator_payment_requirements(
                    payment_requirements,
                    provider_name=provider_name,
                )
                if provider_name == "coinbase" and request_x402_version == 1:
                    facilitator_payment_payload = _normalize_coinbase_v1_payment_payload(payment_payload)
                    facilitator_requirements = _normalize_coinbase_v1_payment_requirements(payment_requirements)
                headers = {"content-type": "application/json"}
                verify_url = f"{facilitator_url}/verify"
                if provider_name == "coinbase":
                    auth_mode = str(provider.get("auth_mode") or "").strip()
                    if auth_mode == "cdp_api_key":
                        headers = build_coinbase_auth_headers_for_url(
                            api_key_id=str(provider.get("api_key_id") or ""),
                            api_key_secret=str(provider.get("api_key_secret") or ""),
                            request_method="POST",
                            request_url=verify_url,
                        )
                    else:
                        auth_token = str(provider.get("auth_token") or "").strip()
                        if auth_token:
                            headers["authorization"] = f"Bearer {auth_token}"
                else:
                    auth_token = str(provider.get("auth_token") or "").strip()
                    if auth_token:
                        headers["authorization"] = f"Bearer {auth_token}"

                resp = await self.http.post(
                    verify_url,
                    json={
                        "x402Version": request_x402_version,
                        "paymentHeader": raw_payment_header,
                        "paymentPayload": facilitator_payment_payload,
                        "paymentRequirements": facilitator_requirements,
                    },
                    headers=headers,
                    follow_redirects=True,
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Facilitator verify failed for %s: status=%s body=%s",
                        provider_name,
                        resp.status_code,
                        resp.text[:300],
                    )
                    provider_attempts.append(
                        {
                            "provider": provider_name,
                            "network": str(payment_requirements.get("network") or ""),
                            "stage": "verify",
                            "status_code": resp.status_code,
                        }
                    )
                    continue
                data = resp.json()
                if not bool(data.get("isValid", False)):
                    logger.warning("Facilitator verify rejected for %s: %s", provider_name, data)
                    reason = _facilitator_rejection_reason(data, default="is_valid_false")
                    provider_attempts.append(
                        {
                            "provider": provider_name,
                            "network": str(payment_requirements.get("network") or ""),
                            "stage": "verify",
                            "status_code": resp.status_code,
                            "reason": reason,
                            "facilitator_response": _sanitize_facilitator_response(data),
                        }
                    )
                    continue

                settle_url = f"{facilitator_url}/settle"
                settle_headers = headers
                if provider_name == "coinbase" and str(provider.get("auth_mode") or "").strip() == "cdp_api_key":
                    settle_headers = build_coinbase_auth_headers_for_url(
                        api_key_id=str(provider.get("api_key_id") or ""),
                        api_key_secret=str(provider.get("api_key_secret") or ""),
                        request_method="POST",
                        request_url=settle_url,
                    )
                settle_resp = await self.http.post(
                    settle_url,
                    json={
                        "x402Version": request_x402_version,
                        "paymentHeader": raw_payment_header,
                        "paymentPayload": facilitator_payment_payload,
                        "paymentRequirements": facilitator_requirements,
                    },
                    headers=settle_headers,
                    follow_redirects=True,
                    timeout=20.0,
                )
                if settle_resp.status_code != 200:
                    logger.warning(
                        "Facilitator settle failed for %s: status=%s body=%s",
                        provider_name,
                        settle_resp.status_code,
                        settle_resp.text[:300],
                    )
                    provider_attempts.append(
                        {
                            "provider": provider_name,
                            "network": str(payment_requirements.get("network") or ""),
                            "stage": "settle",
                            "status_code": settle_resp.status_code,
                        }
                    )
                    continue

                settle_data = settle_resp.json()
                if not bool(settle_data.get("success", False)):
                    logger.warning("Facilitator settle rejected for %s: %s", provider_name, settle_data)
                    reason = _facilitator_rejection_reason(settle_data, default="success_false")
                    provider_attempts.append(
                        {
                            "provider": provider_name,
                            "network": str(payment_requirements.get("network") or ""),
                            "stage": "settle",
                            "status_code": settle_resp.status_code,
                            "reason": reason,
                            "facilitator_response": _sanitize_facilitator_response(settle_data),
                        }
                    )
                    continue

                tx = settle_data.get("transaction")
                if not isinstance(tx, str) or not tx:
                    logger.warning("Facilitator settle missing transaction for %s: %s", provider_name, settle_data)
                    provider_attempts.append(
                        {
                            "provider": provider_name,
                            "stage": "settle",
                            "status_code": settle_resp.status_code,
                            "reason": "missing_transaction",
                        }
                    )
                    continue
                return tx, provider_name, None

            failure_code = _primary_provider_failure_code(provider_attempts) or "verification_failed"
            if not providers:
                failure_code = "no_provider_available"
            elif any(attempt.get("reason") == "missing_facilitator_url" for attempt in provider_attempts):
                failure_code = "provider_not_configured"
            failure_message = "No configured x402 provider verified and settled this payment."
            if failure_code not in {"verification_failed", "no_provider_available", "provider_not_configured"}:
                failure_message = f"x402 provider rejected payment: {failure_code}."
            return None, None, {
                "code": failure_code,
                "message": failure_message,
                "retryable": True,
                "provider_attempts": provider_attempts,
            }
        except Exception as e:
            logger.error(f"Facilitator verification error: {e}")
            return None, None, {
                "code": "verification_exception",
                "message": str(e)[:200],
                "retryable": True,
                "provider_attempts": [],
            }

    async def _replay_request(self, scope, body: bytes, send, extra_headers: list[list[bytes]] | None = None):
        """Replay the request to the inner app with the already-consumed body."""
        body_sent = False
        replay_scope = scope
        if extra_headers and any(
            str((name or b"").decode("utf-8", errors="ignore")).lower() == "payment-response"
            for name, _value in extra_headers
        ):
            replay_scope = {
                **scope,
                "headers": list(scope.get("headers", [])) + [(b"x-delx-payment-verified", b"true")],
            }

        async def replay_receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            # After body is sent, wait for disconnect
            return {"type": "http.disconnect"}

        async def replay_send(message):
            if extra_headers and message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(extra_headers)
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(replay_scope, replay_receive, replay_send)
