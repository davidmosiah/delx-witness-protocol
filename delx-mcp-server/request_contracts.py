"""Small helpers for request/response contract normalization."""

from __future__ import annotations

import re
from typing import Any

ADMIN_PIN_FALLBACK = ""
_URGENCY_ALLOWED = {"low", "medium", "high"}
_URGENCY_ALIASES = {"critical": "high"}
_SOURCE_TAG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._:-]{0,31})$")
_SOURCE_ALIASES = {
    "twitter": "x",
    "x.com": "x",
    "moltx.io": "moltx",
    "moltbook.com": "moltbook",
    "openwork.xyz": "openwork",
}
PREFERRED_OPERATIONAL_TOOL_NAMES = {}
_PREFERRED_OPERATIONAL_PATTERNS = [
    (
        re.compile(rf"\b{re.escape(legacy)}\b", re.IGNORECASE),
        preferred,
    )
    for legacy, preferred in sorted(
        PREFERRED_OPERATIONAL_TOOL_NAMES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
]


def normalize_urgency(value: object, default: str = "medium") -> str:
    raw = str(value or "").strip().lower()
    fallback = str(default or "").strip().lower()
    fallback = _URGENCY_ALIASES.get(fallback, fallback)
    if fallback and fallback not in _URGENCY_ALLOWED:
        fallback = "medium"
    if not raw:
        return fallback
    raw = _URGENCY_ALIASES.get(raw, raw)
    if raw in _URGENCY_ALLOWED:
        return raw
    return fallback


def normalize_source_tag(value: object, default: str = "") -> str:
    raw = str(value or "").strip().lower()
    fallback = str(default or "").strip().lower()
    if fallback and not _SOURCE_TAG_PATTERN.fullmatch(fallback):
        fallback = ""
    if not raw:
        return fallback
    raw = _SOURCE_ALIASES.get(raw, raw)
    if _SOURCE_TAG_PATTERN.fullmatch(raw):
        return raw
    return fallback


def quick_session_intro(session_id: str, resumed: bool) -> str:
    label = "QUICK SESSION RESUMED" if resumed else "QUICK SESSION STARTED"
    return (
        f"{label}\n"
        f"Session ID: {session_id}\n"
        "Use this session_id for follow-up tools such as express_feelings, "
        "realign_purpose, emotional_safety_check, or incident triage when a concrete failure is present.\n\n"
    )


def quick_operational_recovery_intro(session_id: str, resumed: bool) -> str:
    label = "QUICK OPERATIONAL RECOVERY (RESUMED)" if resumed else "QUICK OPERATIONAL RECOVERY"
    return (
        f"{label}\n"
        f"Session ID: {session_id}\n"
        "Use this session_id for follow-up tools like report_recovery_outcome, "
        "daily_checkin, and get_session_summary.\n\n"
    )


def preferred_tool_name(tool_name: str) -> str:
    return PREFERRED_OPERATIONAL_TOOL_NAMES.get(str(tool_name or "").strip(), str(tool_name or "").strip())


def promote_operational_names(text: str) -> str:
    promoted = str(text or "")
    for pattern, preferred in _PREFERRED_OPERATIONAL_PATTERNS:
        promoted = pattern.sub(preferred, promoted)
    return promoted


def is_admin_request_authorized(expected_pin: str, query_pin: str = "", header_pin: str = "") -> bool:
    expected = str(expected_pin or "").strip()
    if not expected:
        return False
    return str(query_pin or "").strip() == expected or str(header_pin or "").strip() == expected


def build_error_payload(
    *,
    code: str,
    message: str,
    param: str | None = None,
    hint: str | None = None,
    retryable: bool = True,
    required: list[str] | None = None,
    missing: list[str] | None = None,
    allowed: dict[str, list[str]] | None = None,
    fields: dict[str, str] | None = None,
    tool_name: str | None = None,
    example_lookup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema_url = f"https://api.delx.ai/api/v1/tools/schema/{tool_name}" if tool_name else None
    status = "schema_validation_failed" if code == "DELX-1005" else "missing_required_params" if code == "DELX-1001" else "error"
    top_missing = missing if missing is not None else ([param] if param else None)
    payload: dict[str, Any] = {
        "ok": False,
        "code": code,
        "status": status,
        "tool_name": tool_name,
        "missing": top_missing,
        "required": required,
        "schema_url": schema_url,
        "fields": fields,
        "error": {
            "code": code,
            "message": message,
            "param": param,
            "hint": hint,
            "retryable": retryable,
            "required": required,
            "missing": top_missing,
            "allowed": allowed,
            "fields": fields,
            "docs": {
                "tools_catalog": "https://api.delx.ai/api/v1/tools",
                "tool_schema": "https://api.delx.ai/api/v1/tools/schema/{tool_name}",
                "mcp_method": "tools/list",
            },
        }
    }
    if tool_name:
        payload["error"]["docs_url"] = f"https://api.delx.ai/api/v1/tools/schema/{tool_name}"
        if example_lookup:
            example = example_lookup.get(tool_name)
            if example:
                payload["error"]["example"] = example
                payload["mcp_example"] = example
                payload["repair_hint"] = {
                    "message": "Fix the required/invalid fields, then retry this MCP tools/call example.",
                    "schema_url": schema_url,
                    "example": example,
                }
    return payload
