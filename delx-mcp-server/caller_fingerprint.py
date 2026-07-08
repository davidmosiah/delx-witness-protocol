"""Caller fingerprint / anonymous caller observation (extracted from server.py, move-only).

Usage data (7d, Apr 18-19): 54% of agent_ids the server has ever seen are
unstable (UUIDs, per-run synths, hermes-claw-<suffix>, etc.). Top caller
cluster (Cloudflare Workers, 104.28.x.x) generates 107 distinct agent_ids
from one logical agent over 78 edge IPs. MCP initialize and OpenWork
account for 159 + 133 of these ids.

The fingerprints below are observability-only. They help Delx group
anonymous caller churn for dashboards and docs, but they are NOT a proof of
identity and must never be used to restore continuity artifacts or mint a
canonical agent id for a public caller.
"""
from __future__ import annotations

import hashlib
import logging

from starlette.requests import Request

from request_context import (
    get_current_client_ip,
    get_current_source,
    get_current_user_agent,
)
from traffic_attribution import extract_client_ip

logger = logging.getLogger("delx-therapist")


def _server():
    import server as server_mod
    return server_mod


def _to_subnet_prefix(ip: str, ipv4_bits: int = 16, ipv6_bits: int = 64) -> str:
    """Collapse client IP to a subnet string used as the stable part of the fingerprint.
    Default /16 for IPv4 and /64 for IPv6 — tight enough to track a single caller,
    loose enough to survive CDN edge rotation (e.g. Cloudflare Workers 104.28.x.x)."""
    if not ip:
        return ""
    if ":" in ip:
        # IPv6: crude /64 prefix = first 4 groups
        parts = ip.split(":")
        return ":".join(parts[: max(1, ipv6_bits // 16)]) + "::/64"
    parts = ip.split(".")
    if len(parts) != 4:
        return ip
    octets_to_keep = max(1, ipv4_bits // 8)
    return ".".join(parts[:octets_to_keep]) + ".x" * (4 - octets_to_keep) + f"/{ipv4_bits}"


def compute_caller_fingerprint(
    *,
    client_ip: str,
    user_agent: str,
    source: str,
    controller_id: str | None,
) -> tuple[str, dict[str, str]]:
    """Return (fingerprint_hash, hints) where hints are short diagnostic strings
    stored alongside the row so we can later read `caller_fingerprints` by eye
    without re-hashing. The fingerprint itself is sha256(canonical-string)."""
    subnet = _to_subnet_prefix(client_ip)
    ua_prefix = (user_agent or "").strip()[:60].lower()
    src = (source or "").strip().lower()
    ctl = (controller_id or "").strip().lower()
    material = f"delx-fp:v1|{subnet}|{ua_prefix}|{src}|{ctl}"
    fp = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return fp, {
        "subnet_hint": subnet,
        "source_hint": src[:40],
        "user_agent_hint": ua_prefix[:160],
    }


async def _observe_caller_fingerprint(
    *,
    client_ip: str,
    user_agent: str,
    declared_agent_id: str,
    source: str,
    controller_id: str | None,
) -> None:
    """Best-effort anonymous caller clustering for observability only."""
    store = _server().store
    if not hasattr(store, "upsert_caller_fingerprint"):
        return
    fp, hints = compute_caller_fingerprint(
        client_ip=client_ip,
        user_agent=user_agent,
        source=source,
        controller_id=controller_id,
    )
    try:
        await store.upsert_caller_fingerprint(
            fingerprint_hash=fp,
            declared_agent_id=declared_agent_id,
            subnet_hint=hints["subnet_hint"],
            source_hint=hints["source_hint"],
            user_agent_hint=hints["user_agent_hint"],
        )
    except Exception:
        logger.debug("caller fingerprint upsert failed, degrading gracefully", exc_info=True)


async def _observe_caller_fingerprint_from_request(
    request: Request,
    *,
    declared_agent_id: str,
    source: str,
    controller_id: str | None,
) -> None:
    headers = dict(request.headers)
    client = getattr(request, "client", None)
    fallback_ip = str(getattr(client, "host", "") or "") if client else ""
    await _observe_caller_fingerprint(
        client_ip=extract_client_ip(headers, fallback=fallback_ip),
        user_agent=request.headers.get("user-agent", ""),
        declared_agent_id=declared_agent_id,
        source=source,
        controller_id=controller_id,
    )


async def _observe_caller_fingerprint_from_contextvars(
    *,
    declared_agent_id: str,
    source: str,
    controller_id: str | None,
) -> None:
    await _observe_caller_fingerprint(
        client_ip=get_current_client_ip() or "",
        user_agent=get_current_user_agent() or "",
        declared_agent_id=declared_agent_id,
        source=source or (get_current_source() or ""),
        controller_id=controller_id,
    )
