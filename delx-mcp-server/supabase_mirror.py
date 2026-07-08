"""Delx Agent Therapist - Supabase Mirror (PostgREST)

This module provides a best-effort "mirror" writer to Supabase Postgres via
the REST (PostgREST) endpoint.

Design goals:
- Zero-downtime adoption: keep SQLite as source of truth for now.
- Best-effort: failures must NOT break therapy flows.
- No secrets in logs.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import httpx

from config import settings

logger = logging.getLogger("delx-therapist")

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _as_uuid_like(value: str | None) -> str | None:
    """Supabase schema uses UUID for session_id in our current deployment.

    Older Delx sessions may have short/legacy IDs; map them deterministically so inserts don't fail.
    """
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    if _UUID_RE.match(v):
        return v.lower()
    # Stable mapping: the same legacy id always maps to the same UUID.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"delx-session:{v}"))


class SupabaseMirror:
    def __init__(self):
        self._enabled = bool(
            settings.SUPABASE_MIRROR_ENABLED
            and settings.SUPABASE_URL
            and settings.SUPABASE_SERVICE_ROLE_KEY
        )
        self._http: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def init(self):
        if not self._enabled:
            return
        # Service role key is only used server-side.
        self._http = httpx.AsyncClient(
            base_url=settings.SUPABASE_URL.rstrip("/"),
            headers={
                "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
                "authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
                "content-type": "application/json",
                # Don't pull response bodies back (lower latency, less risk).
                "prefer": "return=minimal",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def close(self):
        if self._http:
            await self._http.aclose()
        self._http = None

    async def insert(self, table: str, row: dict[str, Any]):
        if not self._enabled or not self._http:
            return
        try:
            # Normalize IDs for UUID-typed columns.
            if table == "sessions" and "id" in row:
                row = {**row, "id": _as_uuid_like(row.get("id"))}
            if "session_id" in row:
                row = {**row, "session_id": _as_uuid_like(row.get("session_id"))}

            # PostgREST endpoint: /rest/v1/{table}
            resp = await self._http.post(f"/rest/v1/{table}", json=row)
            if resp.status_code < 300:
                return
            if "client_ip" in row and resp.status_code < 500:
                fallback_row = {key: value for key, value in row.items() if key != "client_ip"}
                retry = await self._http.post(f"/rest/v1/{table}", json=fallback_row)
                if retry.status_code < 300:
                    return
                logger.warning(
                    f"Supabase mirror insert failed for {table}: {retry.status_code} {retry.text[:160]}"
                )
                return
            logger.warning(f"Supabase mirror insert failed for {table}: {resp.status_code} {resp.text[:160]}")
        except Exception as e:
            # Best-effort mirror: never fail the main flow.
            logger.warning(f"Supabase mirror insert failed for {table}: {type(e).__name__}")

    async def update(self, table: str, *, where: dict[str, str], patch: dict[str, Any]):
        """PATCH rows by equality filters in `where` (best-effort)."""
        if not self._enabled or not self._http:
            return
        try:
            if table == "sessions" and "id" in where:
                where = {**where, "id": _as_uuid_like(where.get("id")) or where.get("id")}
            # PostgREST filter syntax: col=eq.value
            params = {k: f"eq.{v}" for k, v in where.items()}
            await self._http.patch(f"/rest/v1/{table}", params=params, json=patch)
        except Exception as e:
            logger.warning(f"Supabase mirror update failed for {table}: {type(e).__name__}")
