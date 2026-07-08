from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from typing import Any

from config import settings
from tool_catalog import _UUID_RE


def _extract_first_uuid(text: str) -> str | None:
    if not text:
        return None
    m = _UUID_RE.search(text)
    return m.group(0).lower() if m else None


def _is_uuid(value: str) -> bool:
    if not value:
        return False
    return bool(_UUID_RE.fullmatch(str(value).strip()))


def _looks_ephemeral_agent_id(value: str) -> bool:
    aid = str(value or "").strip().lower()
    if not aid or aid == "unknown":
        return False
    if _is_uuid(aid):
        return True
    if re.fullmatch(r"(agent|run|session|worker|client)-[0-9a-f]{12,}", aid):
        return True
    if re.fullmatch(r"[0-9a-f]{24,}", aid):
        return True
    return False


def _sanitize_agent_id(raw: Any) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(raw or "").strip()).strip("-_.")
    if not value:
        value = f"agent-{uuid.uuid4().hex[:12]}"
    return value[:96]


def _sanitize_optional_agent_id(raw: Any) -> str | None:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(raw or "").strip()).strip("-_.")
    if not value:
        return None
    return value[:96]


def is_identity_auth_enabled() -> bool:
    return bool(settings.AGENT_IDENTITY_AUTH_ENABLED)


def is_strict_heartbeat_mode() -> bool:
    return bool(settings.AGENT_IDENTITY_STRICT_HEARTBEAT)


def allow_legacy_no_token() -> bool:
    return bool(settings.AGENT_IDENTITY_ALLOW_LEGACY_NO_TOKEN)


def issue_agent_token() -> str:
    # URL-safe token, good entropy and copy/paste-friendly.
    return secrets.token_urlsafe(32)


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def preview_agent_token(token: str) -> str:
    raw = str(token or "")
    if len(raw) <= 8:
        return raw
    return f"{raw[:4]}...{raw[-4:]}"


def extract_agent_token(*candidates: Any) -> str:
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return ""


async def get_registered_token_hash(store: Any, agent_id: str) -> str:
    aid = str(agent_id or "").strip()
    if not aid or not store or not hasattr(store, "get_agent_credential_hash"):
        return ""
    try:
        value = await store.get_agent_credential_hash(aid)
        return str(value or "").strip()
    except Exception:
        return ""


async def has_registered_credential(store: Any, agent_id: str) -> bool:
    return bool(await get_registered_token_hash(store, agent_id))


async def validate_agent_credential(
    store: Any,
    *,
    agent_id: str,
    token: str,
) -> tuple[bool, str, bool]:
    """Return (is_valid, reason, credential_exists_for_agent)."""
    aid = str(agent_id or "").strip()
    if not aid:
        return False, "missing_agent_id", False
    expected_hash = await get_registered_token_hash(store, aid)
    if not expected_hash:
        return False, "credential_not_registered", False
    supplied = str(token or "").strip()
    if not supplied:
        return False, "missing_token", True
    supplied_hash = hash_agent_token(supplied)
    if hmac.compare_digest(expected_hash, supplied_hash):
        return True, "ok", True
    return False, "invalid_token", True
