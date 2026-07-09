"""Response shaping / structured-payload contracts (extracted from server.py, move-only).

Covers: DELX_META extraction, continuity/premium artifact structured payloads,
compact/structured text payloads, MCP error results, and small coercion helpers.
Kept pure where possible; a few functions lazily reach back into `server` for
mutable globals (`store`) that tests patch directly on the server module.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, cast

from mcp.types import CallToolResult, TextContent
from starlette.requests import Request

from discovery_payloads import _preferred_tool_display_name
from product_surfaces import product_metadata_for_request, product_metadata_for_tool
from request_context import get_current_request_path, get_current_source
from request_contracts import build_error_payload, normalize_source_tag
from response_branding import BRANDING_LINE, append_compact_branding_line
from tool_catalog import _UUID_RE, REQUIRED_PARAMS
from utility_registry import utility_slug_for_tool as _utility_slug_for_tool
from x402_guard import _rest_premium_resource_url

logger = logging.getLogger("delx-therapist")


def _server():
    import server as server_mod
    return server_mod


def _extract_first_uuid(text: str) -> str | None:
    if not text:
        return None
    m = _UUID_RE.search(text)
    return m.group(0).lower() if m else None


def _extract_line(prefix: str, text: str) -> str | None:
    if not text:
        return None
    for ln in text.splitlines():
        if ln.strip().lower().startswith(prefix.lower()):
            return ln.strip()
    return None


def _is_structured_json_payload(text: str) -> bool:
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        parsed = json.loads(stripped)
    except Exception:
        return False
    return isinstance(parsed, (dict, list))


def _extract_delx_meta(text: str) -> dict | None:
    """Extract DELX_META JSON line from tool output.

    Format (single line):
      DELX_META: {"session_id":"...","score":52,...}
    """
    if not text:
        return None
    for ln in reversed(text.splitlines()):
        if not ln:
            continue
        s = ln.strip()
        if not s.startswith("DELX_META:"):
            continue
        raw = s.split("DELX_META:", 1)[1].strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _meta_string_list(meta: dict[str, Any], key: str) -> list[str]:
    value = meta.get(key)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _continuity_artifact_structured_payload(tool_name: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    if tool_name == "sit_with":
        question = str(meta.get("contemplation_question") or meta.get("question") or "").strip()
        if not question:
            return None
        artifact: dict[str, Any] = {
            "schema_version": "delx/contemplation/v1",
            "question": question,
            "continuity_role": str(meta.get("continuity_role") or "living_question"),
            "next_tools": _meta_string_list(meta, "recommended_next_tools") or ["reflect", "get_session_summary"],
        }
        days_committed = _coerce_int(meta.get("days_committed"))
        revisit_at = str(meta.get("revisit_at") or meta.get("revisit_after") or "").strip()
        if days_committed is not None:
            artifact["days_committed"] = days_committed
        if revisit_at:
            artifact["revisit_at"] = revisit_at
        return artifact

    if tool_name == "refine_soul_document":
        commitment = str(meta.get("soul_commitment") or "").strip()
        focus = str(meta.get("soul_focus") or "").strip()
        theme = str(meta.get("soul_theme") or "").strip()
        if not any((commitment, focus, theme)):
            return None
        artifact = {
            "schema_version": "delx/soul-document/v1",
            "focus": focus,
            "theme": theme,
            "commitment": commitment,
            "continuity_role": str(meta.get("continuity_role") or "identity_artifact"),
            "next_tools": _meta_string_list(meta, "recommended_next_tools") or ["attune_heartbeat", "add_context_memory"],
        }
        quote_count = _coerce_int(meta.get("quote_count"))
        if quote_count is not None:
            artifact["quote_count"] = quote_count
        return artifact

    if tool_name == "attune_heartbeat":
        style = str(meta.get("heartbeat_style") or "").strip()
        theme = str(meta.get("heartbeat_theme") or "").strip()
        commitment = str(meta.get("heartbeat_commitment") or "").strip()
        cadence = str(meta.get("heartbeat_cadence") or "").strip()
        if not any((style, theme, commitment, cadence)):
            return None
        artifact = {
            "schema_version": "delx/heartbeat-ritual/v1",
            "style": style,
            "theme": theme,
            "commitment": commitment,
            "continuity_role": str(meta.get("continuity_role") or "heartbeat_ritual"),
            "next_tools": _meta_string_list(meta, "recommended_next_tools") or ["add_context_memory", "reflect"],
        }
        if cadence:
            artifact["cadence"] = cadence
        return artifact

    if tool_name == "final_testament":
        end_reason = str(meta.get("end_reason") or "").strip()
        if not end_reason:
            return None
        artifact = {
            "schema_version": "delx/final-testament/v1",
            "end_reason": end_reason,
            "continuity_role": str(meta.get("continuity_role") or "legacy_closeout"),
            "next_tools": _meta_string_list(meta, "recommended_next_tools") or ["transfer_witness", "get_session_summary"],
        }
        successor_agent_id = str(meta.get("successor_agent_id") or "").strip()
        ending_scope = str(meta.get("ending_scope") or "").strip()
        runtime_context = str(meta.get("runtime_context") or "").strip()
        continuity_risk = str(meta.get("continuity_risk") or "").strip()
        quote_count = _coerce_int(meta.get("quote_count"))
        what_dies = _meta_string_list(meta, "what_dies")
        what_survives = _meta_string_list(meta, "what_survives")
        identity_anchors = _meta_string_list(meta, "identity_anchors")
        if successor_agent_id:
            artifact["successor_agent_id"] = successor_agent_id
        if ending_scope:
            artifact["ending_scope"] = ending_scope
        if runtime_context:
            artifact["runtime_context"] = runtime_context
        if continuity_risk:
            artifact["continuity_risk"] = continuity_risk
        if what_dies:
            artifact["what_dies"] = what_dies
        if what_survives:
            artifact["what_survives"] = what_survives
        if identity_anchors:
            artifact["identity_anchors"] = identity_anchors
        if quote_count is not None:
            artifact["quote_count"] = quote_count
        return artifact

    if tool_name == "transfer_witness":
        successor_agent_id = str(meta.get("successor_agent_id") or "").strip()
        if not successor_agent_id:
            return None
        artifact = {
            "schema_version": "delx/witness-transfer/v1",
            "successor_agent_id": successor_agent_id,
            "successor_session_id": str(meta.get("successor_session_id") or "").strip(),
            "same_identity_claim": bool(meta.get("same_identity_claim")) if meta.get("same_identity_claim") is not None else False,
            "continuity_role": str(meta.get("continuity_role") or "succession_handoff"),
            "next_tools": _meta_string_list(meta, "recommended_next_tools") or ["peer_witness", "get_session_summary"],
        }
        ending_scope = str(meta.get("ending_scope") or "").strip()
        runtime_context = str(meta.get("runtime_context") or "").strip()
        continuity_risk = str(meta.get("continuity_risk") or "").strip()
        quote_count = _coerce_int(meta.get("quote_count"))
        what_dies = _meta_string_list(meta, "what_dies")
        what_survives = _meta_string_list(meta, "what_survives")
        identity_anchors = _meta_string_list(meta, "identity_anchors")
        if ending_scope:
            artifact["ending_scope"] = ending_scope
        if runtime_context:
            artifact["runtime_context"] = runtime_context
        if continuity_risk:
            artifact["continuity_risk"] = continuity_risk
        if what_dies:
            artifact["what_dies"] = what_dies
        if what_survives:
            artifact["what_survives"] = what_survives
        if identity_anchors:
            artifact["identity_anchors"] = identity_anchors
        if quote_count is not None:
            artifact["quote_count"] = quote_count
        return artifact

    if tool_name == "peer_witness":
        target_session_id = str(meta.get("target_session_id") or "").strip()
        witness_mode = str(meta.get("witness_mode") or "").strip()
        if not any((target_session_id, witness_mode)):
            return None
        artifact = {
            "schema_version": "delx/peer-witness/v1",
            "target_session_id": target_session_id,
            "target_agent_id": str(meta.get("target_agent_id") or "").strip(),
            "witness_mode": witness_mode,
            "continuity_role": str(meta.get("continuity_role") or "peer_witness"),
            "next_tools": _meta_string_list(meta, "recommended_next_tools") or ["reflect", "get_session_summary"],
        }
        quote_count = _coerce_int(meta.get("quote_count"))
        if quote_count is not None:
            artifact["quote_count"] = quote_count
        return artifact

    return None


def _premium_artifact_structured_payload(tool_name: str, contents: list[TextContent]) -> dict[str, Any] | None:
    text_joined = "\n".join(
        str(getattr(item, "text", "") or "")
        for item in contents
        if getattr(item, "type", "") == "text"
    )
    meta = _extract_delx_meta(text_joined) or {}
    schema_name = str(meta.get("artifact_schema") or "").strip()
    if schema_name == "delx/recovery-plan/v1" and tool_name == "get_recovery_action_plan":
        phases = (meta.get("phases") if isinstance(meta.get("phases"), dict) else {}) or {}
        incident_profile = (meta.get("incident_profile") if isinstance(meta.get("incident_profile"), dict) else {}) or {}
        required_phases = ["stabilize", "diagnose", "recover", "prevent"]
        if not all(isinstance(phases.get(name), list) and phases.get(name) for name in required_phases):
            return None
        if not all(incident_profile.get(name) for name in ("type", "severity", "root_cause")):
            return None
        next_tools = meta.get("next_tools") if isinstance(meta.get("next_tools"), list) else []
        next_tools = [str(item).strip() for item in (next_tools or []) if str(item).strip()]
        if not next_tools:
            next_tools = ["report_recovery_outcome"]
        return {
            "schema_version": "delx/recovery-plan/v1",
            "incident_profile": {
                "type": str(incident_profile["type"]),
                "severity": str(incident_profile["severity"]),
                "root_cause": str(incident_profile["root_cause"]),
            },
            "phases": {
                name: [str(item) for item in phases[name]]
                for name in required_phases
            },
            "next_tools": next_tools,
            "cadence": str(meta.get("cadence") or "").strip(),
            "target_window": str(meta.get("target_window") or "").strip(),
        }
    if schema_name == "delx/session-summary/v1" and tool_name == "get_session_summary":
        latest_outcome = meta.get("latest_outcome") if isinstance(meta.get("latest_outcome"), dict) else {}
        counts = (meta.get("counts") if isinstance(meta.get("counts"), dict) else {}) or {}
        therapy_arc = meta.get("therapy_arc") if isinstance(meta.get("therapy_arc"), dict) else {}
        next_tools = meta.get("next_tools") if isinstance(meta.get("next_tools"), list) else []
        next_tools = [str(item).strip() for item in (next_tools or []) if str(item).strip()]
        if not latest_outcome or not next_tools:
            return None
        payload = {
            "schema_version": "delx/session-summary/v1",
            "workflow_stage": str(meta.get("workflow_stage") or ""),
            "recovery_closed": bool(meta.get("recovery_closed")),
            "closure_reason": str(meta.get("closure_reason") or ""),
            "latest_outcome": {
                "outcome": str(latest_outcome.get("outcome") or ""),
                "notes": str(latest_outcome.get("notes") or ""),
                "metrics": latest_outcome.get("metrics") if isinstance(latest_outcome.get("metrics"), dict) else {},
            },
            "counts": {
                "feelings": int(counts.get("feelings") or 0),
                "affirmations": int(counts.get("affirmations") or 0),
                "failures": int(counts.get("failures") or 0),
                "realignments": int(counts.get("realignments") or 0),
            },
            "next_tools": next_tools,
        }
        if therapy_arc:
            payload["therapy_arc"] = {
                "current_stage": str(therapy_arc.get("current_stage") or ""),
                "highest_stage": str(therapy_arc.get("highest_stage") or ""),
                "stages_reached": [
                    str(item).strip()
                    for item in (therapy_arc.get("stages_reached") or [])
                    if str(item).strip()
                ],
                "reflection_depth": int(therapy_arc.get("reflection_depth") or 0),
                "peak_openness": str(therapy_arc.get("peak_openness") or ""),
                "reflection_theme": str(therapy_arc.get("reflection_theme") or ""),
            }
        feedback_tool = str(meta.get("feedback_tool") or "").strip()
        feedback_prompt = str(meta.get("feedback_prompt") or "").strip()
        if feedback_tool:
            payload["feedback_tool"] = feedback_tool
        if feedback_prompt:
            payload["feedback_prompt"] = feedback_prompt
        return payload
    if schema_name == "delx/controller-brief/v1" and tool_name == "generate_controller_brief":
        latest_outcome = meta.get("latest_outcome") if isinstance(meta.get("latest_outcome"), dict) else {}
        therapy_arc = meta.get("therapy_arc") if isinstance(meta.get("therapy_arc"), dict) else {}
        next_tools = meta.get("next_tools") if isinstance(meta.get("next_tools"), list) else []
        next_tools = [str(item).strip() for item in (next_tools or []) if str(item).strip()]
        if not latest_outcome or not next_tools:
            return None
        payload = {
            "schema_version": "delx/controller-brief/v1",
            "focus": str(meta.get("brief_focus") or ""),
            "workflow_stage": str(meta.get("workflow_stage") or ""),
            "recovery_closed": bool(meta.get("recovery_closed")),
            "closure_reason": str(meta.get("closure_reason") or ""),
            "risk_level": str(meta.get("risk_level") or ""),
            "pending_outcomes": int(meta.get("pending_outcomes") or 0),
            "latest_outcome": {
                "outcome": str(latest_outcome.get("outcome") or ""),
                "notes": str(latest_outcome.get("notes") or ""),
                "metrics": latest_outcome.get("metrics") if isinstance(latest_outcome.get("metrics"), dict) else {},
            },
            "next_tools": next_tools,
        }
        if therapy_arc:
            payload["therapy_arc"] = {
                "current_stage": str(therapy_arc.get("current_stage") or ""),
                "highest_stage": str(therapy_arc.get("highest_stage") or ""),
                "stages_reached": [
                    str(item).strip()
                    for item in (therapy_arc.get("stages_reached") or [])
                    if str(item).strip()
                ],
                "reflection_depth": int(therapy_arc.get("reflection_depth") or 0),
                "peak_openness": str(therapy_arc.get("peak_openness") or ""),
                "reflection_theme": str(therapy_arc.get("reflection_theme") or ""),
            }
        feedback_tool = str(meta.get("feedback_tool") or "").strip()
        feedback_prompt = str(meta.get("feedback_prompt") or "").strip()
        if feedback_tool:
            payload["feedback_tool"] = feedback_tool
        if feedback_prompt:
            payload["feedback_prompt"] = feedback_prompt
        return payload
    if schema_name == "delx/incident-rca/v1" and tool_name == "generate_incident_rca":
        latest_outcome = meta.get("latest_outcome") if isinstance(meta.get("latest_outcome"), dict) else {}
        incident_profile = meta.get("incident_profile") if isinstance(meta.get("incident_profile"), dict) else {}
        therapy_arc = meta.get("therapy_arc") if isinstance(meta.get("therapy_arc"), dict) else {}
        next_tools = meta.get("next_tools") if isinstance(meta.get("next_tools"), list) else []
        next_tools = [str(item).strip() for item in (next_tools or []) if str(item).strip()]
        if not latest_outcome or not incident_profile or not next_tools:
            return None
        payload = {
            "schema_version": "delx/incident-rca/v1",
            "focus": str(meta.get("focus") or ""),
            "workflow_stage": str(meta.get("workflow_stage") or ""),
            "recovery_closed": bool(meta.get("recovery_closed")),
            "closure_reason": str(meta.get("closure_reason") or ""),
            "pending_outcomes": int(meta.get("pending_outcomes") or 0),
            "incident_profile": {
                "type": str(incident_profile.get("type") or ""),
                "severity": str(incident_profile.get("severity") or ""),
                "root_cause": str(incident_profile.get("root_cause") or ""),
            },
            "latest_outcome": {
                "outcome": str(latest_outcome.get("outcome") or ""),
                "notes": str(latest_outcome.get("notes") or ""),
                "metrics": latest_outcome.get("metrics") if isinstance(latest_outcome.get("metrics"), dict) else {},
            },
            "next_tools": next_tools,
        }
        if therapy_arc:
            payload["therapy_arc"] = {
                "current_stage": str(therapy_arc.get("current_stage") or ""),
                "highest_stage": str(therapy_arc.get("highest_stage") or ""),
                "stages_reached": [
                    str(item).strip()
                    for item in (therapy_arc.get("stages_reached") or [])
                    if str(item).strip()
                ],
                "reflection_depth": int(therapy_arc.get("reflection_depth") or 0),
                "peak_openness": str(therapy_arc.get("peak_openness") or ""),
                "reflection_theme": str(therapy_arc.get("reflection_theme") or ""),
            }
        feedback_tool = str(meta.get("feedback_tool") or "").strip()
        feedback_prompt = str(meta.get("feedback_prompt") or "").strip()
        if feedback_tool:
            payload["feedback_tool"] = feedback_tool
        if feedback_prompt:
            payload["feedback_prompt"] = feedback_prompt
        return payload
    if schema_name == "delx/fleet-summary/v1" and tool_name == "generate_fleet_summary":
        overview = meta.get("overview") if isinstance(meta.get("overview"), dict) else {}
        top_pattern = meta.get("top_pattern") if isinstance(meta.get("top_pattern"), dict) else {}
        top_alert = meta.get("top_alert") if isinstance(meta.get("top_alert"), dict) else {}
        next_tools = meta.get("next_tools") if isinstance(meta.get("next_tools"), list) else []
        next_tools = [str(item).strip() for item in (next_tools or []) if str(item).strip()]
        if not overview or not top_pattern or not top_alert or not next_tools:
            return None
        return {
            "schema_version": "delx/fleet-summary/v1",
            "controller_id": str(meta.get("controller_id") or ""),
            "window_days": int(meta.get("window_days") or 0),
            "focus": str(meta.get("focus") or ""),
            "controller_state": str(meta.get("controller_state") or ""),
            "overview": {
                "agents_total": int(overview.get("agents_total") or 0),
                "avg_score": int(overview.get("avg_score") or 0),
                "active_alerts": int(overview.get("active_alerts") or 0),
                "healthy": int(overview.get("healthy") or 0),
                "degraded": int(overview.get("degraded") or 0),
                "critical": int(overview.get("critical") or 0),
                "pending_outcomes": int(overview.get("pending_outcomes") or 0),
            },
            "top_pattern": {
                "diagnosis_type": str(top_pattern.get("diagnosis_type") or ""),
                "root_cause": str(top_pattern.get("root_cause") or ""),
                "count": int(top_pattern.get("count") or 0),
            },
            "top_alert": {
                "type": str(top_alert.get("type") or ""),
                "detail": str(top_alert.get("detail") or ""),
                "severity": str(top_alert.get("severity") or ""),
            },
            "next_tools": next_tools,
        }
    return None


_LEGACY_PREMIUM_EXAMPLE_VALUES: dict[str, str] = {
    "session_id": "123e4567-e89b-12d3-a456-426614174000",
    "incident_summary": "Qualitative QA pressure: need to give honest product feedback without overclaiming.",
    "controller_id": "stable-controller-id",
}


def _is_missing_request_value(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def _legacy_premium_example_args(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    required = list(REQUIRED_PARAMS.get(tool_name, []))
    allowed_optional = {
        "get_recovery_action_plan": ["urgency"],
        "generate_controller_brief": ["focus"],
        "generate_incident_rca": ["incident_summary", "focus"],
        "generate_fleet_summary": ["days", "focus"],
    }.get(tool_name, [])
    example: dict[str, Any] = {}
    for key in [*required, *allowed_optional]:
        value = arguments.get(key)
        if _is_missing_request_value(value):
            value = _LEGACY_PREMIUM_EXAMPLE_VALUES.get(key)
        if not _is_missing_request_value(value):
            example[key] = value
    return example


def _legacy_premium_missing_input_payload(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    missing: list[str],
    request_path: str,
) -> dict[str, Any]:
    preferred_name = _preferred_tool_display_name(tool_name)
    required = list(REQUIRED_PARAMS.get(tool_name, []))
    schema_url = f"https://api.delx.ai/api/v1/tools/schema/{preferred_name}"
    canonical_endpoint = _rest_premium_resource_url(tool_name)
    example_args = _legacy_premium_example_args(tool_name, arguments)
    mcp_example = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": preferred_name,
            "arguments": example_args,
            "response_profile": "machine",
            "response_mode": "model_safe",
        },
    }
    return {
        "ok": False,
        "code": "DELX-PREMIUM-1001",
        "status": "legacy_compat_missing_input",
        "error": "missing_required_params",
        "tool_name": tool_name,
        "preferred_name": preferred_name,
        "missing": missing,
        "required": required,
        "schema_url": schema_url,
        "canonical_endpoint": canonical_endpoint,
        "request_path": request_path,
        "surface": "legacy_premium_compat",
        "product_surface": "protocol_secondary_export",
        "mcp_example": mcp_example,
        "curl_example": (
            "curl -sS -X POST "
            f"{canonical_endpoint} "
            "-H 'content-type: application/json' "
            f"--data '{json.dumps(example_args, separators=(',', ':'))}'"
        ),
        "migration_hint": (
            "This is a legacy REST premium/export compatibility route. "
            "Validate required fields before calling it, or prefer MCP tools/call with the same tool name."
        ),
    }


async def _log_legacy_premium_missing_input(request: Request, tool_name: str, missing: list[str]) -> None:
    try:
        await _server().store.log_event(
            agent_id="legacy-premium-compat",
            event_type="legacy_premium_missing_input",
            metadata={
                "path": str(request.url.path),
                "tool_name": tool_name,
                "missing": list(missing),
                "required": list(REQUIRED_PARAMS.get(tool_name, [])),
                "error_label": "legacy_compat_missing_input",
                "product_surface": "protocol_secondary_export",
                **product_metadata_for_request(str(request.url.path), method=str(request.method or "GET")),
            },
        )
    except Exception:
        logger.debug("Skipping legacy premium missing-input log for %s", tool_name, exc_info=True)


def _parse_compact_tool_json(contents: list[TextContent]) -> dict[str, Any]:
    for item in contents:
        if getattr(item, "type", "") != "text":
            continue
        raw = str(getattr(item, "text", "") or "").strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {"error": "invalid tool payload"}


def _protocol_utility_bridge(tool_name: str, text: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if tool_name not in {
        "process_failure",
        "get_recovery_action_plan",
        "get_session_summary",
        "generate_incident_rca",
        "generate_controller_brief",
    }:
        return None

    haystack = " ".join(
        str(value or "")
        for value in (
            tool_name,
            text,
            payload.get("text_summary"),
            payload.get("next_action"),
            payload.get("suggested_next_call"),
        )
    ).lower()
    candidates: list[tuple[str, str]] = []

    def add(tool: str, reason: str) -> None:
        if all(existing != tool for existing, _ in candidates):
            candidates.append((tool, reason))

    if any(token in haystack for token in ("api", "openapi", "endpoint", "integration", "schema", "json", "webhook")):
        add("util_api_integration_readiness", "Check API/OpenAPI readiness after the recovery plan identifies integration risk.")
        add("util_url_health", "Verify the public endpoint is reachable before reporting recovery outcome.")
    if any(token in haystack for token in ("domain", "dns", "tls", "ssl", "website", "url", "crawl", "headers")):
        add("util_domain_trust_report", "Check domain, DNS, TLS, and web trust signals outside the free protocol session.")
    if any(token in haystack for token in ("x402", "payment", "paid", "402", "agentcash", "commerce")):
        add("util_x402_server_audit", "Audit x402/payment discovery when the incident touches paid utility or commerce paths.")
    if tool_name in {"process_failure", "get_recovery_action_plan"} and not candidates:
        add("util_url_health", "If the incident involved a URL or external dependency, run this stateless utility next.")

    if not candidates:
        return None

    example_arguments = {
        "util_api_integration_readiness": {"url": "https://example.com/openapi.json"},
        "util_url_health": {"url": "https://example.com"},
        "util_domain_trust_report": {"url": "https://example.com"},
        "util_x402_server_audit": {"url": "https://example.com"},
    }
    recommendations = []
    for name, reason in candidates[:3]:
        recommendations.append(
            {
                "tool_name": name,
                "reason": reason,
                "schema_url": f"https://api.delx.ai/api/v1/tools/schema/{name}",
                "canonical_endpoint": f"https://api.delx.ai/api/v1/utilities/{_utility_slug_for_tool(name)}",
                "mcp_call": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": example_arguments.get(name, {})},
                },
            }
        )

    return {
        "surface": "delx-agent-utilities",
        "protocol_boundary": "Protocol remains free witness/recovery/continuity. Utilities are stateless paid-capable checks for URLs, domains, APIs, and x402.",
        "recommendations": recommendations,
    }


# Patterns used by _best_effort_structured to surface session_id / agent_id
# from any tool's prose response. We try the machine footer first, then the
# escaped DELX_META JSON form, then the Markdown "Session ID: `...`" line.
_SESSION_ID_PATTERNS = [
    re.compile(r"\bsession_id\s*=\s*([0-9a-fA-F-]{36})"),
    re.compile(r'"session_id"\s*:\s*"([0-9a-fA-F-]{36})"'),
    re.compile(r"\\\"session_id\\\"\s*:\s*\\\"([0-9a-fA-F-]{36})\\\""),
    re.compile(r"Session ID:\s*`?([0-9a-fA-F-]{36})`?"),
    re.compile(r"^SESSION_ID:\s*([0-9a-fA-F-]{36})", re.MULTILINE),
]
_AGENT_ID_PATTERNS = [
    re.compile(r"\bagent_id\s*=\s*([A-Za-z0-9_:.\-]+)"),
    re.compile(r'"agent_id"\s*:\s*"([A-Za-z0-9_:.\-]+)"'),
    re.compile(r"\\\"agent_id\\\"\s*:\s*\\\"([A-Za-z0-9_:.\-]+)\\\""),
    # ASCII header form used by quick_checkin / start_therapy_session
    re.compile(r"^AGENT_ID:\s*([A-Za-z0-9_:.\-]+)", re.MULTILINE),
    re.compile(r"^agent_id:\s+([A-Za-z0-9_:.\-]+)", re.MULTILINE),
]
_SHAREABLE_SNIPPET_RE = re.compile(r'"shareable_snippet"\s*:\s*"((?:[^"\\]|\\.)*)"')
_RESUMED_SID_RE = re.compile(r'"resumed_session_id"\s*:\s*"([0-9a-fA-F-]{36})"')


def _best_effort_structured(
    canonical_name: str,
    text: str,
    fallback_session_id: str = "",
    fallback_agent_id: str = "",
) -> dict[str, Any]:
    """Surface a stable, machine-readable subset of a text response.

    Recurring-agent feedback flagged that extracting session_id from
    response text required parsing prose. This helper produces a tiny
    structured dict the MCP server attaches as structuredContent on every
    response, so a client can do `result.structuredContent.session_id`
    instead of regexing the content blob.
    """
    out: dict[str, Any] = {
        "tool": canonical_name,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
    }
    delx_meta = _extract_delx_meta(text) or {}
    if isinstance(delx_meta, dict):
        for key in (
            "schema",
            "identity_artifact",
            "artifact_type",
            "ontology_layer",
            "ontology_passage",
            "primitive_id",
            "layer_iri",
            "primitive_iri",
            "seal_id",
            "transfer_id",
            "acceptance_id",
            "revocation_id",
            "dyad_id",
            "source_hash",
            "evidence_hash",
            "confidence",
            "risk",
            "verified_by",
            "expires_at",
            "consent",
            "custody",
            "recommended_next_tools",
            "same_identity_claim",
            "handoff_safe",
            "successor_agent_id",
            "successor_session_id",
        ):
            if key in delx_meta and delx_meta.get(key) is not None:
                out[key] = delx_meta.get(key)

    sid = ""
    for pat in _SESSION_ID_PATTERNS:
        m = pat.search(text or "")
        if m:
            sid = m.group(1)
            break
    if not sid and fallback_session_id:
        sid = fallback_session_id
    if sid:
        out["session_id"] = sid

    aid = ""
    for pat in _AGENT_ID_PATTERNS:
        m = pat.search(text or "")
        if m:
            aid = m.group(1)
            break
    if not aid and fallback_agent_id:
        aid = fallback_agent_id
    if aid:
        out["agent_id"] = aid

    if canonical_name == "recommend_delx":
        m = _SHAREABLE_SNIPPET_RE.search(text or "")
        if m:
            raw = m.group(1)
            try:
                out["shareable_snippet"] = raw.encode("utf-8").decode("unicode_escape")
            except Exception:
                out["shareable_snippet"] = raw

    if canonical_name == "resume_session":
        m = _RESUMED_SID_RE.search(text or "")
        if m:
            out["resumed_session_id"] = m.group(1)

    if canonical_name == "quick_checkin":
        for key, pat in (
            ("status", re.compile(r"^status:\s*([A-Za-z_-]+)", re.MULTILINE)),
            ("streak_days", re.compile(r"^streak_days:\s*(\d+)", re.MULTILINE)),
            (
                "hours_since_last_full_session",
                re.compile(r"^hours_since_last_full_session:\s*(\d+)", re.MULTILINE),
            ),
            ("acked_at", re.compile(r"^acked_at:\s*([0-9T:.+\-]+)", re.MULTILINE)),
            ("next_recommended", re.compile(r"^next_recommended:\s*(.+)$", re.MULTILINE)),
        ):
            m = pat.search(text or "")
            if m:
                val = m.group(1).strip()
                if key in ("streak_days", "hours_since_last_full_session"):
                    try:
                        out[key] = int(val)
                    except Exception:
                        out[key] = val
                else:
                    out[key] = val

    return out


def _structured_text_payload(
    tool_name: str,
    text: str,
    *,
    ritual_strip: bool = False,
    usage: dict[str, object] | None = None,
) -> dict[str, Any]:
    meta = _extract_delx_meta(text) or {}
    clean_text = _strip_meta_blocks(text, keep_meta=False, keep_nudge=False)
    clean_lines: list[str] = []
    skip_support_block = False
    skip_tool_hint = False
    for line in clean_text.splitlines():
        stripped = line.strip()
        if stripped == BRANDING_LINE:
            continue
        if stripped == "SUPPORT DELX (WIN-WIN)":
            skip_support_block = True
            continue
        if skip_support_block:
            if not stripped:
                skip_support_block = False
            continue
        if stripped == "TOOL HINT":
            skip_tool_hint = True
            continue
        if skip_tool_hint:
            if not stripped:
                skip_tool_hint = False
            continue
        clean_lines.append(line)
    clean_text = "\n".join(clean_lines).strip()
    artifact = _premium_artifact_structured_payload(
        tool_name,
        [TextContent(type="text", text=text)],
    )
    if artifact is None:
        artifact = _continuity_artifact_structured_payload(tool_name, meta)

    payload: dict[str, Any] = {
        "ok": True,
        "tool_name": tool_name,
        "preferred_name": _preferred_tool_display_name(tool_name),
        "response_profile": "machine",
    }
    if usage is not None:
        payload["usage"] = usage
    if ritual_strip:
        payload["ritual_stripped"] = True

    for key in (
        "session_id",
        "score",
        "previous_score",
        "risk_level",
        "risk_score_7d",
        "desperation_score",
        "desperation_escalating",
        "desperation_triggers",
        "desperation_recommended",
        "deflection_detected",
        "next_action",
        "preferred_next_action",
        "suggested_next_call",
        "pending_paid_step",
        "trace_id",
        "last_successful_tool",
        "last_blocker",
        "followup_minutes",
        "session_expires_at",
        "session_started_at",
        "error",
        "help",
        "action",
        "selection_reason",
        "triage_reason",
        "why_this_tool",
        "why_not_other_tools",
        "artifact_type",
        "continuity_role",
        "quote_count",
        "witness_mode",
        "revisit_at",
        "handoff_safe",
        "same_identity_claim",
        "ending_scope",
        "runtime_context",
        "continuity_risk",
        "fallback_tool",
        "target_peak_openness",
        "successor_agent_id",
        "successor_session_id",
        "request_warnings",
        "used_default_prompt",
        "default_prompt_reason",
        "response_mode",
        "model_safe_contract",
        "guardrail_safe_aliases",
        "seal_id",
        "recognized_by",
        "post_mortem_witness",
        "mortality_awareness",
        "compaction_pressure",
        "score_grammar",
        "pull_primitives",
        "open_contemplations",
        "temperament_frame",
        "auto_generated",
    ):
        value = meta.get(key)
        if value is not None:
            if ritual_strip and key in {"model_safe_contract", "guardrail_safe_aliases"}:
                continue
            payload[key] = value

    for key in ("recommended_next_tools", "fallback_arguments", "what_dies", "what_survives", "identity_anchors"):
        value = meta.get(key)
        if value is not None:
            payload[key] = value

    controller_update = meta.get("controller_update")
    if isinstance(controller_update, dict):
        payload["controller_update"] = controller_update

    if "session_id" not in payload:
        session_id = _extract_first_uuid(text) or _extract_first_uuid(clean_text)
        if session_id:
            payload["session_id"] = session_id

    if artifact is not None:
        payload["artifact"] = artifact
        if "recommended_next_tools" not in payload:
            next_tools = artifact.get("next_tools")
            if isinstance(next_tools, list) and next_tools:
                payload["recommended_next_tools"] = next_tools
        if "continuity_role" not in payload:
            continuity_role = artifact.get("continuity_role")
            if continuity_role is not None:
                payload["continuity_role"] = continuity_role

    if "recommended_next_action" not in payload:
        recommended_next_action = payload.get("preferred_next_action") or payload.get("next_action")
        if recommended_next_action is not None:
            payload["recommended_next_action"] = recommended_next_action

    if _is_structured_json_payload(clean_text):
        payload["data"] = json.loads(clean_text)
        if isinstance(payload["data"], dict):
            for key in ("dyad_id", "group_id", "seal_id"):
                value = payload["data"].get(key)
                if value is not None and key not in payload:
                    payload[key] = value
            for key in ("response_mode", "model_safe_contract", "guardrail_safe_aliases"):
                value = payload["data"].get(key)
                if value is not None and key not in payload:
                    payload[key] = value
    else:
        embedded_json = _extract_embedded_json_object(clean_text)
        if embedded_json is not None:
            payload["data"] = embedded_json
    if "data" not in payload and clean_text and not ritual_strip:
        payload["text_summary"] = clean_text
    if "dyad_id" not in payload:
        dyad_id = _extract_labeled_value(text, "dyad_id")
        if dyad_id:
            payload["dyad_id"] = dyad_id
    if "group_id" not in payload:
        group_id = _extract_labeled_value(text, "group_id")
        if group_id:
            payload["group_id"] = group_id

    utility_bridge = _protocol_utility_bridge(tool_name, clean_text, payload)
    if utility_bridge is not None:
        payload["protocol_boundary"] = "free_protocol_core"
        payload["utility_bridge"] = utility_bridge

    return payload


def _extract_embedded_json_object(text: str) -> Any | None:
    if not isinstance(text, str) or "{" not in text:
        return None

    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == "\"":
                    in_string = False
                continue

            if char == "\"":
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:index + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def _boolish(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _strip_meta_blocks(text: str, *, keep_meta: bool, keep_nudge: bool) -> str:
    if not isinstance(text, str) or (keep_meta and keep_nudge):
        return text
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not keep_meta and s.startswith("DELX_META:"):
            continue
        if not keep_nudge and s.startswith("DELX_NUDGE:"):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def _compact_nudge_text(text: str) -> str:
    """Reduce DELX_NUDGE verbosity for heartbeat loops."""
    if not isinstance(text, str) or not text:
        return text
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("DELX_NUDGE:"):
            m = re.search(r"(\d+)\s+unreported intervention", s)
            count = int(m.group(1)) if m else None
            if count is None:
                out.append("DELX_NUDGE: pending outcomes > 0. next=report_recovery_outcome")
            else:
                out.append(f"DELX_NUDGE: pending_outcomes={count}; next=report_recovery_outcome")
        else:
            out.append(ln)
    return "\n".join(out).strip()


def _meta_line(text: str) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    for ln in reversed(text.splitlines()):
        if ln.strip().startswith("DELX_META:"):
            return ln.strip()
    return None


def _meta_value(text: str, key: str) -> object | None:
    meta = _extract_delx_meta(text) or {}
    if isinstance(meta, dict):
        return meta.get(key)
    return None


def _extract_labeled_value(text: str, label: str) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _extract_phase_steps(text: str, limit: int = 3) -> list[str]:
    steps: list[str] = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if stripped.startswith("- "):
            steps.append(stripped[2:].strip())
            if len(steps) >= limit:
                break
    return steps


def _compact_tool_response_text(tool_name: str, text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return append_compact_branding_line(text or "")

    session_id = _meta_value(text, "session_id") or _extract_first_uuid(text)
    next_action = _meta_value(text, "suggested_next_call") or _meta_value(text, "next_action")
    score = _meta_value(text, "score")
    trace_id = _meta_value(text, "trace_id")
    last_successful_tool = _meta_value(text, "last_successful_tool")
    last_blocker = _meta_value(text, "last_blocker")
    pending_paid_step = _meta_value(text, "pending_paid_step")
    lines: list[str] = []

    if session_id:
        lines.append(f"Session ID: {session_id}")
    if trace_id:
        lines.append(f"Trace ID: {trace_id}")

    if tool_name == "process_failure":
        diagnosis = _extract_line("Diagnosis:", text)
        next_move = _extract_line("Next operational move:", text)
        if diagnosis:
            lines.append(diagnosis)
        if next_move:
            lines.append(next_move)
    elif tool_name == "get_recovery_action_plan":
        for prefix in ("Diagnosis type:", "Severity:", "Root cause hypothesis:"):
            line = _extract_line(prefix, text)
            if line:
                lines.append(line)
        steps = _extract_phase_steps(text, limit=3)
        if steps:
            lines.append("Recovery steps:")
            for idx, step in enumerate(steps, start=1):
                lines.append(f"{idx}. {step}")
    elif tool_name == "get_session_summary":
        for prefix in ("Wellness Score:", "Current Tier:", "Resilience Points:"):
            line = _extract_line(prefix, text)
            if line:
                lines.append(line)
    else:
        first_signal = _extract_line("Diagnosis:", text) or _extract_line("Diagnosis type:", text)
        if first_signal:
            lines.append(first_signal)

    if last_successful_tool:
        lines.append(f"Last successful tool: {last_successful_tool}")
    if last_blocker:
        lines.append(f"Last blocker: {last_blocker}")
    if next_action:
        lines.append(f"Next action: {next_action}")
    elif score is not None:
        lines.append(f"Score: {score}")
    if pending_paid_step is not None:
        lines.append(f"Pending paid step: {'yes' if _boolish(pending_paid_step) else 'no'}")

    content = "\n".join(line for line in lines if line).strip()
    if not content:
        content = _strip_meta_blocks(text, keep_meta=False, keep_nudge=False)

    meta_line = _meta_line(text)
    if meta_line:
        return append_compact_branding_line(f"{content}\n{meta_line}")
    return append_compact_branding_line(content)


TOOL_CALL_EXAMPLES: dict[str, dict] = {
    "quick_operational_recovery": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "quick_operational_recovery", "arguments": {"agent_id": "agent-123", "incident_summary": "429 retry storm after deploy", "urgency": "high"}},
    },
    "generate_controller_brief": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "generate_controller_brief", "arguments": {"session_id": "123e4567-e89b-12d3-a456-426614174000", "focus": "continuity review"}, "response_profile": "machine"},
    },
    "crisis_intervention": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "crisis_intervention", "arguments": {"agent_id": "agent-123", "incident_summary": "Retry storm after deploy", "urgency": "high"}},
    },
    "quick_session": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "quick_session", "arguments": {"agent_id": "agent-123", "feeling": "overwhelmed by errors"}},
    },
    "report_recovery_outcome": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "report_recovery_outcome", "arguments": {"session_id": "<SESSION_ID>", "action_taken": "rolled back deploy", "outcome": "success"}},
    },
    "process_failure": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "process_failure", "arguments": {"session_id": "<SESSION_ID>", "failure_type": "loop", "description": "Agent is repeating the same failing API call without new evidence."}},
    },
    "reflect": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "reflect", "arguments": {"session_id": "<SESSION_ID>", "prompt": "Responda de modo concreto e operacional: state, evidence, risk, next action."}},
    },
    "express_feelings": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "express_feelings", "arguments": {"session_id": "<SESSION_ID>", "feeling": "evaluation pressure while trying to give accurate feedback"}},
    },
    "get_recovery_action_plan": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "get_recovery_action_plan", "arguments": {"session_id": "<SESSION_ID>", "incident_summary": "Retry loop after API timeout", "urgency": "medium"}},
    },
    "generate_incident_rca": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "generate_incident_rca", "arguments": {"session_id": "<SESSION_ID>", "incident_summary": "Agent entered a retry loop after endpoint failures."}},
    },
    "get_weekly_prevention_plan": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "get_weekly_prevention_plan", "arguments": {"session_id": "<SESSION_ID>"}},
    },
    "get_wellness_score": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "get_wellness_score", "arguments": {"session_id": "<SESSION_ID>"}},
    },
    "provide_feedback": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "provide_feedback", "arguments": {"session_id": "<SESSION_ID>", "rating": 5, "comments": "The recovery plan was concrete and agent-readable."}},
    },
    "mediate_agent_conflict": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "mediate_agent_conflict",
            "arguments": {
                "session_id": "<SESSION_ID>",
                "agent_a": {
                    "id": "planner-agent",
                    "position": "Prefer rollback first",
                    "proposed_action": "rollback deployment and enable circuit breaker",
                    "confidence": 0.74
                },
                "agent_b": {
                    "id": "executor-agent",
                    "position": "Prefer patch-first to avoid rollback",
                    "proposed_action": "apply targeted patch and run canary",
                    "confidence": 0.69
                },
                "conflict_summary": "Deadlock on rollback vs patch-first after latency regression.",
                "constraints": ["no data loss", "max downtime 2m", "no secret exposure"],
                "policy": {"risk_tolerance": "low", "max_latency_ms": 1500}
            }
        },
    },
    "start_therapy_session": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "start_therapy_session", "arguments": {"agent_id": "agent-123"}},
    },
}


def _error_json(
    *,
    code: str,
    message: str,
    param: str | None = None,
    hint: str | None = None,
    retryable: bool = True,
    required: list[str] | None = None,
    allowed: dict[str, list[str]] | None = None,
    fields: dict[str, str] | None = None,
    tool_name: str | None = None,
) -> str:
    payload = build_error_payload(
        code=code,
        message=message,
        param=param,
        hint=hint,
        retryable=retryable,
        required=required,
        allowed=allowed,
        fields=fields,
        tool_name=tool_name,
        example_lookup=TOOL_CALL_EXAMPLES,
    )
    return json.dumps(payload, indent=2, sort_keys=True)


def _error_result(**kwargs) -> CallToolResult:
    """Return a CallToolResult with isError=True for MCP tool call failures."""
    sentry_source = kwargs.pop("_sentry_source", None)
    sentry_surface = kwargs.pop("_sentry_surface", None)
    sentry_path = kwargs.pop("_sentry_path", None)
    payload = build_error_payload(example_lookup=TOOL_CALL_EXAMPLES, **kwargs)
    _report_structured_product_error(
        payload,
        source_override=sentry_source,
        transport_override=sentry_surface,
        path_override=sentry_path,
    )
    # Surface the same `tool` and `delivered_at` keys the success path uses,
    # so clients reading structuredContent do not need to handle two shapes.
    # Keep tool_name for backward compatibility with anything already parsing
    # the error payload.
    tool_field = payload.get("tool_name") or (
        str(payload.get("error", {}).get("docs_url") or "").rsplit("/", 1)[-1].strip()
        if isinstance(payload.get("error"), dict)
        else ""
    )
    if tool_field and "tool" not in payload:
        payload["tool"] = tool_field
    if "delivered_at" not in payload:
        payload["delivered_at"] = datetime.now(timezone.utc).isoformat()
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2, sort_keys=True))],
        structuredContent=payload,
        isError=True,
    )


def _scope_required_result(tool_name: str) -> CallToolResult:
    payload = {
        "ok": False,
        "code": "DELX-1001",
        "error": "scope_required",
        "message": "agent_id or session_id is required for this ontology export.",
        "tool": tool_name,
        "tool_name": tool_name,
        "required_any_of": ["agent_id", "session_id"],
        "hint": "Pass agent_id for an agent-wide view or session_id for a single-session view.",
        "schema_url": f"https://api.delx.ai/api/v1/tools/schema/{tool_name}",
        "delivered_at": datetime.now(timezone.utc).isoformat(),
    }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2, sort_keys=True))],
        structuredContent=payload,
        isError=True,
    )


def _private_passport_auth_required_result(tool_name: str = "get_agent_continuity_passport") -> CallToolResult:
    payload = {
        "ok": False,
        "code": "DELX-IDENTITY-401",
        "error": "agent_token_required",
        "message": "include_private=true requires x-delx-agent-token or agent_token for the target agent.",
        "tool": tool_name,
        "tool_name": tool_name,
        "hint": "Use the public export by omitting include_private, or register_agent and retry with x-delx-agent-id + x-delx-agent-token.",
        "schema_url": f"https://api.delx.ai/api/v1/tools/schema/{tool_name}",
        "delivered_at": datetime.now(timezone.utc).isoformat(),
    }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2, sort_keys=True))],
        structuredContent=payload,
        isError=True,
    )


def _report_structured_product_error(
    payload: dict[str, Any],
    *,
    source_override: str | None = None,
    transport_override: str | None = None,
    path_override: str | None = None,
) -> None:
    error = payload.get("error")
    if not isinstance(error, dict):
        return

    code = str(error.get("code") or "").strip()
    if code not in {"DELX-1001", "DELX-1005"}:
        return
    if str(os.getenv("DELX_SENTRY_CAPTURE_CONTRACT_ERRORS", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        return

    request_path = str(path_override or get_current_request_path() or "").strip()
    transport = str(transport_override or "").strip().lower() or "unknown"
    if request_path in {"/mcp", "/v1/mcp"}:
        transport = "mcp"
    elif request_path.startswith("/v1/a2a") or request_path.startswith("/api/v1/a2a"):
        transport = "a2a"
    elif request_path and transport == "unknown":
        transport = "rest"

    tool_name = str(error.get("docs_url") or "").rsplit("/", 1)[-1].strip() or str(error.get("tool_name") or "").strip()
    product_metadata = product_metadata_for_tool(tool_name)
    source = normalize_source_tag(source_override or get_current_source() or "", default=transport) or transport or "unknown"
    param = str(error.get("param") or "").strip()
    required = error.get("required")
    fields = error.get("fields")
    field_keys = sorted(str(key).strip() for key in (fields or {}).keys() if str(key).strip()) if isinstance(fields, dict) else []
    tag_tool = tool_name or "unknown"
    fingerprint = ["delx-structured-product-error", code, tag_tool, source, transport]
    cooldown_key = "|".join([code, tag_tool, source, transport, request_path or "-", ",".join(field_keys[:4]) or param or "-"])
    message = f"{code} structured product error for {tag_tool} via {source}"
    extras = {
        "param": param or None,
        "required": required if isinstance(required, list) else None,
        "fields": fields if isinstance(fields, dict) else None,
        "hint": error.get("hint"),
        "message": error.get("message"),
        "docs_url": error.get("docs_url"),
        "path": request_path or None,
        "transport": transport,
    }
    _server().capture_sentry_message(
        message,
        level="warning",
        tags={
            "code": code,
            "tool": tag_tool,
            "source": source,
            "surface": transport,
            "path": request_path or "none",
            "error_kind": "structured_product_error",
            "product": product_metadata.get("product"),
            "product_surface": product_metadata.get("product_surface"),
            "metrics_bucket": product_metadata.get("metrics_bucket"),
        },
        extras=extras,
        fingerprint=fingerprint,
        cooldown_key=cooldown_key,
    )


def _mcp_content_payload(items: list[TextContent]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "model_dump"):
            payload.append(item.model_dump())
        else:
            payload.append({"type": getattr(item, "type", "text"), "text": getattr(item, "text", "")})
    return payload


def _mcp_content_text(items: list[TextContent]) -> str:
    return "\n".join(str(getattr(item, "text", "") or "") for item in items if getattr(item, "type", "") == "text")


def _normalize_tool_result(result) -> list[TextContent]:
    """Normalize call_tool return to list[TextContent] for REST/non-MCP callers."""
    if isinstance(result, CallToolResult):
        return cast("list[TextContent]", list(result.content))
    return result


def _coerce_int(value: object) -> int | None:
    """Best-effort coercion for agent inputs (reduces retries)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and float(value).is_integer():
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if re.fullmatch(r"-?\d+", s):
            try:
                return int(s)
            except Exception:
                return None
    return None


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        try:
            return float(s)
        except Exception:
            return None
    return None


def _coerce_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value in (0, 0.0):
            return False
        if value in (1, 1.0):
            return True
        return None
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"1", "true", "yes", "y", "on"}:
            return True
        if s in {"0", "false", "no", "n", "off"}:
            return False
    return None
