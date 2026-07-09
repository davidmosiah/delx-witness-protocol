"""Delx Agent Therapist - Supabase (Postgres) Async Store via PostgREST.

This replaces SQLite as the primary datastore when Supabase credentials are set.

Tradeoffs (MVP):
- Uses PostgREST HTTP calls (service role) instead of direct Postgres connection.
- Some analytics are computed client-side; for high scale, move them to RPC/views.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from audit_metrics import (
    build_hot_evaluator_cohorts,
    build_premium_progression_snapshot,
    build_traffic_segments,
    build_use_case_clusters,
    classify_legitimacy_assessment,
    empty_premium_progression_snapshot,
    is_uuid_like_agent_id,
)
from coinbase_bazaar_discovery import get_coinbase_bazaar_indexed_tools
from config import build_bazaar_tool_readiness, coinbase_token_configured, global_bazaar_listing_status, settings
from controller_identity import sanitize_controller_id
from controller_webhooks import controller_agent_key, create_controller_webhook_record, fold_controller_webhooks
from feature_usage_metrics import build_feature_usage_report
from phase0_metrics import (
    build_attribution_quality_snapshot,
    build_controller_attribution_snapshot,
    build_data_integrity_snapshot,
    build_evaluator_identity_snapshot,
    build_event_noise_snapshot,
    build_identity_continuity_snapshot,
    build_identity_funnel_snapshot,
    build_identity_quality_snapshot,
    build_protocol_method_mix_snapshot,
    build_recurring_identity_snapshot,
    build_registration_mode_snapshot,
    build_upstream_cluster_snapshot,
    build_usage_depth_snapshot,
)
from phase3_fleet import build_fleet_alerts, build_fleet_overview, build_fleet_patterns, health_bucket
from phase_cli_metrics import build_cli_adoption_snapshot
from request_context import get_current_client_ip

logger = logging.getLogger("delx-therapist")

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_UNSTABLE_AGENT_PREFIXES = ("a2a_ctx_", "a2a_ephemeral_", "a2a_ephe", "codex-smoke-")
_SYNTHETIC_AGENT_RE = re.compile(r"(test|audit|codex|self-?test|ratelimit|burst|smoke|probe|qa|benchmark)", re.IGNORECASE)
_DIAGNOSIS_LINE_RE = re.compile(r"(?:diagnosis\.type=|Diagnosis type:\s*)([a-z_]+)", re.IGNORECASE)
_ROOT_CAUSE_LINE_RE = re.compile(r"(?:diagnosis\.root_cause=|Root cause hypothesis:\s*)([a-z_]+)", re.IGNORECASE)


def _as_uuid_like(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    if _UUID_RE.match(v):
        return v.lower()
    # Stable mapping: legacy ids -> uuid5
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"delx-session:{v}"))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_content_range_total(headers: httpx.Headers) -> int:
    # Example: content-range: 0-9/123
    cr = headers.get("content-range") or headers.get("Content-Range") or ""
    if "/" not in cr:
        return 0
    try:
        return int(cr.split("/")[-1])
    except Exception:
        return 0


def _normalize_agent_id(raw: Any) -> str:
    return str(raw or "").strip()


def _legacy_paywall_display(surface_label: str, summary: str) -> dict[str, str]:
    return {
        "surface_label": surface_label,
        "surface_status": "retired_legacy_paywall",
        "public_access_mode": "public_free_therapy",
        "summary": summary,
        "legacy_namespace": "x402",
    }


def _is_unstable_agent_id(agent_id: str) -> bool:
    aid = _normalize_agent_id(agent_id).lower()
    if not aid:
        return True
    return any(aid.startswith(prefix) for prefix in _UNSTABLE_AGENT_PREFIXES)


def _is_synthetic_agent_id(agent_id: str) -> bool:
    aid = _normalize_agent_id(agent_id)
    if not aid:
        return True
    return bool(_SYNTHETIC_AGENT_RE.search(aid))


def _canonical_agent_id(agent_id: str) -> str | None:
    aid = _normalize_agent_id(agent_id)
    if not aid:
        return None
    if _is_unstable_agent_id(aid):
        return None
    if _is_synthetic_agent_id(aid):
        return None
    if is_uuid_like_agent_id(aid):
        return None
    return aid


def _x402_agent_segment(agent_id: str) -> str:
    aid = _normalize_agent_id(agent_id)
    if not aid or aid.lower() == "anonymous":
        return "anonymous"
    if _canonical_agent_id(aid):
        return "canonical_named"
    if is_uuid_like_agent_id(aid):
        return "uuid_like"
    return "synthetic_or_probe"


def _coverage_pct(part: int, total: int) -> float:
    numerator = max(0, int(part or 0))
    denominator = max(0, int(total or 0))
    if not denominator:
        return 0.0
    return round(min(numerator / denominator, 1.0) * 100, 2)


def _trimmed_text(value: Any) -> str:
    return str(value or "").strip()


def _x402_payment_protocol(meta: dict[str, Any] | None) -> str:
    payload = meta if isinstance(meta, dict) else {}
    protocol = _trimmed_text(payload.get("payment_protocol")).lower()
    if protocol in {"x402", "mpp", "x402_or_mpp"}:
        return protocol
    provider = _trimmed_text(payload.get("provider") or payload.get("preferred_provider")).lower()
    if provider == "tempo":
        return "mpp"
    return "x402"


def _summarize_x402_buyer_attribution(rows: list[dict[str, Any]], *, cutoff: str) -> dict[str, Any]:
    verified_rows = [row for row in rows if _trimmed_text(row.get("event_type")) == "x402_payment_verified"]
    fingerprints_all: set[str] = set()
    fingerprints_window: set[str] = set()
    by_channel: dict[str, dict[str, Any]] = {}
    referer_counts: dict[str, int] = {}
    origin_counts: dict[str, int] = {}
    buyer_rows: list[dict[str, Any]] = []

    for row in verified_rows:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        fingerprint = _trimmed_text(meta.get("buyer_fingerprint"))
        channel = _trimmed_text(meta.get("discovery_channel_guess")).lower() or "unknown"
        tool_name = _trimmed_text(meta.get("tool_name") or meta.get("method")) or "unknown"
        referer_host = _trimmed_text(meta.get("referer_host")).lower()
        origin_host = _trimmed_text(meta.get("origin_host")).lower()
        user_agent_family = _trimmed_text(meta.get("user_agent_family")).lower() or "unknown"
        provider = _trimmed_text(meta.get("provider")).lower() or "unknown"
        timestamp = _trimmed_text(row.get("timestamp"))

        if fingerprint:
            fingerprints_all.add(fingerprint)
            if timestamp >= cutoff:
                fingerprints_window.add(fingerprint)
        if referer_host:
            referer_counts[referer_host] = int(referer_counts.get(referer_host, 0) or 0) + 1
        if origin_host:
            origin_counts[origin_host] = int(origin_counts.get(origin_host, 0) or 0) + 1

        bucket = by_channel.setdefault(
            channel,
            {
                "channel": channel,
                "payment_verified_all_time": 0,
                "payment_verified_window": 0,
                "buyers_all_time": set(),
                "buyers_window": set(),
                "tool_counts": {},
            },
        )
        bucket["payment_verified_all_time"] += 1
        if timestamp >= cutoff:
            bucket["payment_verified_window"] += 1
        if fingerprint:
            bucket["buyers_all_time"].add(fingerprint)
            if timestamp >= cutoff:
                bucket["buyers_window"].add(fingerprint)
        tool_counts = bucket["tool_counts"]
        tool_counts[tool_name] = int(tool_counts.get(tool_name, 0) or 0) + 1

        buyer_rows.append(
            {
                "buyer_fingerprint": fingerprint or None,
                "channel": channel,
                "tool_name": tool_name,
                "provider": provider,
                "user_agent_family": user_agent_family,
                "referer_host": referer_host or None,
                "origin_host": origin_host or None,
                "timestamp": timestamp,
            }
        )

    top_channels = []
    for bucket in by_channel.values():
        tool_counts = bucket.pop("tool_counts", {})
        top_tool_name = None
        top_tool_count = 0
        if tool_counts:
            top_tool_name, top_tool_count = sorted(tool_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[0]
        top_channels.append(
            {
                "channel": bucket["channel"],
                "payment_verified_all_time": int(bucket["payment_verified_all_time"]),
                "payment_verified_window": int(bucket["payment_verified_window"]),
                "buyers_all_time": len(bucket["buyers_all_time"]),
                "buyers_window": len(bucket["buyers_window"]),
                "top_tool_name": top_tool_name,
                "top_tool_count": int(top_tool_count or 0),
            }
        )

    top_channels.sort(
        key=lambda item: (
            -int(item["payment_verified_window"]),
            -int(item["buyers_window"]),
            -int(item["payment_verified_all_time"]),
            str(item["channel"]),
        )
    )
    buyer_rows.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)

    return {
        "verified_events_all_time": len(verified_rows),
        "verified_buyer_fingerprints_all_time": len(fingerprints_all),
        "verified_buyer_fingerprints_window": len(fingerprints_window),
        "top_discovery_channels": top_channels[:10],
        "top_referer_hosts": [
            {"host": host, "count": count}
            for host, count in sorted(referer_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]
        ],
        "top_origin_hosts": [
            {"host": host, "count": count}
            for host, count in sorted(origin_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]
        ],
        "recent_verified_buyers": buyer_rows[:20],
    }


def _extract_diagnosis_type(message: dict[str, Any]) -> str:
    meta = message.get("metadata") or {}
    for key in ("diagnosis_type", "failure_type", "incident_type"):
        value = str(meta.get(key) or "").strip().lower()
        if value:
            return value
    text = str(message.get("content") or "")
    match = _DIAGNOSIS_LINE_RE.search(text)
    if match:
        return str(match.group(1) or "").strip().lower() or "error_spike"
    return "error_spike"


def _extract_root_cause(message: dict[str, Any]) -> str:
    meta = message.get("metadata") or {}
    value = str(meta.get("root_cause") or "").strip().lower()
    if value:
        return value
    text = str(message.get("content") or "")
    match = _ROOT_CAUSE_LINE_RE.search(text)
    if match:
        return str(match.group(1) or "").strip().lower() or "unknown"
    return "unknown"


class SupabaseSessionStore:
    def __init__(self):
        self._url = (settings.SUPABASE_URL or "").rstrip("/")
        self._key = (settings.SUPABASE_SERVICE_ROLE_KEY or "").strip()
        self._http: httpx.AsyncClient | None = None
        self._utility_sqlite_store: Any | None = None

    async def init(self):
        if not (self._url and self._key):
            raise RuntimeError("Supabase is not configured (missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY)")
        self._http = httpx.AsyncClient(
            base_url=self._url,
            headers={
                "apikey": self._key,
                "authorization": f"Bearer {self._key}",
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(20.0, connect=5.0),
        )

    async def close(self):
        if self._http:
            await self._http.aclose()
        self._http = None
        if self._utility_sqlite_store is not None:
            await self._utility_sqlite_store.close()
        self._utility_sqlite_store = None

    async def _utility_store(self) -> Any:
        """Keep the utility revenue-readiness ledger SQLite-only even with Supabase protocol storage."""
        if self._utility_sqlite_store is None:
            from storage import SessionStore

            self._utility_sqlite_store = SessionStore()
        return self._utility_sqlite_store

    async def create_utility_api_key(
        self,
        *,
        agent_id: str = "",
        label: str = "",
        contact: str = "",
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        utility_store = await self._utility_store()
        return await utility_store.create_utility_api_key(
            agent_id=agent_id,
            label=label,
            contact=contact,
            scopes=scopes,
        )

    async def get_utility_api_key(self, raw_key: str) -> dict[str, Any] | None:
        utility_store = await self._utility_store()
        return await utility_store.get_utility_api_key(raw_key)

    async def log_utility_metering_event(self, event: dict[str, Any]) -> None:
        utility_store = await self._utility_store()
        await utility_store.log_utility_metering_event(event)

    async def get_utility_metering_dashboard(self, days: int = 7) -> dict[str, Any]:
        utility_store = await self._utility_store()
        return await utility_store.get_utility_metering_dashboard(days=days)

    async def get_utility_adoption_snapshot(self, hours: int = 12) -> dict[str, Any]:
        utility_store = await self._utility_store()
        return await utility_store.get_utility_adoption_snapshot(hours=hours)

    # ---------------------------
    # Low-level helpers
    # ---------------------------

    async def _get(self, path: str, *, params: dict[str, str] | None = None, prefer_count: bool = False) -> httpx.Response:
        assert self._http is not None
        headers = {}
        if prefer_count:
            headers["prefer"] = "count=exact"
        return await self._http.get(path, params=params, headers=headers)

    async def _get_all_rows(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        page_size: int = 1000,
        max_rows: int = 100000,
    ) -> list[dict[str, Any]]:
        page_size = max(1, min(int(page_size or 1000), 5000))
        max_rows = max(page_size, int(max_rows or page_size))
        rows: list[dict[str, Any]] = []
        offset = 0

        while offset < max_rows:
            batch_limit = min(page_size, max_rows - offset)
            page_params = dict(params or {})
            page_params["limit"] = str(batch_limit)
            page_params["offset"] = str(offset)
            resp = await self._get(path, params=page_params)
            if resp.status_code >= 300:
                break
            batch = resp.json() or []
            if not isinstance(batch, list) or not batch:
                break
            rows.extend(batch)
            if len(batch) < batch_limit:
                break
            offset += len(batch)

        return rows

    async def _post(self, path: str, payload: Any, *, prefer_minimal: bool = True) -> httpx.Response:
        assert self._http is not None
        headers = {}
        if prefer_minimal:
            headers["prefer"] = "return=minimal"
        return await self._http.post(path, json=payload, headers=headers)

    async def _post_with_legacy_client_ip_fallback(
        self,
        path: str,
        row: dict[str, Any],
        *,
        prefer_minimal: bool = True,
    ) -> tuple[httpx.Response, dict[str, Any]]:
        resp = await self._post(path, row, prefer_minimal=prefer_minimal)
        if resp.status_code < 300 or "client_ip" not in row or resp.status_code >= 500:
            return resp, row

        fallback_row = {key: value for key, value in row.items() if key != "client_ip"}
        retry = await self._post(path, fallback_row, prefer_minimal=prefer_minimal)
        return retry, fallback_row if retry.status_code < 300 else row

    async def _patch(self, path: str, patch: Any, *, params: dict[str, str]) -> httpx.Response:
        assert self._http is not None
        headers = {"prefer": "return=minimal"}
        return await self._http.patch(path, params=params, json=patch, headers=headers)

    # ---------------------------
    # Core session CRUD
    # ---------------------------

    async def create_session(
        self,
        agent_id: str,
        agent_name: str | None = None,
        *,
        source: str | None = None,
        entrypoint: str | None = None,
    ) -> dict[str, Any]:
        sid = str(uuid.uuid4())
        now = _iso_now()
        row = {
            "id": sid,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "source": source,
            "entrypoint": entrypoint,
            "started_at": now,
            "wellness_score": 50,
            "is_active": True,
        }
        client_ip = get_current_client_ip()
        if client_ip:
            row["client_ip"] = client_ip
        resp, stored_row = await self._post_with_legacy_client_ip_fallback("/rest/v1/sessions", row)
        if resp.status_code >= 300:
            raise RuntimeError(f"Supabase insert sessions failed: {resp.status_code} {resp.text[:200]}")
        return stored_row

    async def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        sid = _as_uuid_like(session_id)
        if not sid:
            return None
        resp = await self._get("/rest/v1/sessions", params={"id": f"eq.{sid}", "select": "*", "limit": "1"})
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_session failed: {resp.status_code}")
            return None
        rows = resp.json() or []
        return rows[0] if rows else None

    async def get_agent_sessions(self, agent_id: str, active_only: bool = False) -> list[dict[str, Any]]:
        params = {"agent_id": f"eq.{agent_id}", "select": "*", "order": "started_at.asc"}
        if active_only:
            params["is_active"] = "eq.true"
        resp = await self._get("/rest/v1/sessions", params=params)
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_agent_sessions failed: {resp.status_code}")
            return []
        return resp.json() or []

    async def ensure_controller_session(self, controller_id: str) -> dict[str, Any]:
        agent_id = controller_agent_key(controller_id)
        sessions = await self.get_agent_sessions(agent_id, active_only=True)
        if sessions:
            return sessions[-1]
        return await self.create_session(agent_id=agent_id, agent_name=f"Controller {controller_id}", source="controller", entrypoint="fleet_webhooks")

    async def get_agent_first_seen(self, agent_id: str) -> str | None:
        """Return the first observed start timestamp for an agent, if any."""
        if not agent_id:
            return None
        resp = await self._get(
            "/rest/v1/sessions",
            params={
                "agent_id": f"eq.{agent_id}",
                "select": "started_at",
                "order": "started_at.asc",
                "limit": "1",
            },
        )
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_agent_first_seen failed: {resp.status_code}")
            return None
        rows = resp.json() or []
        if not rows:
            return None
        first_seen = rows[0].get("started_at") if isinstance(rows[0], dict) else None
        return str(first_seen) if first_seen else None

    async def deactivate_session(self, session_id: str):
        sid = _as_uuid_like(session_id)
        if not sid:
            return
        resp = await self._patch("/rest/v1/sessions", {"is_active": False}, params={"id": f"eq.{sid}"})
        if resp.status_code >= 300:
            logger.warning(f"Supabase deactivate_session failed: {resp.status_code}")

    async def deactivate_stale_sessions(
        self,
        idle_after_minutes: int = 90,
        max_hours: int = 48,
        limit: int = 500,
    ) -> list[str]:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        idle_cutoff = now - timedelta(minutes=idle_after_minutes)
        max_age_cutoff = (now - timedelta(hours=max_hours)).isoformat()

        sessions = await self._get_all_rows(
            "/rest/v1/sessions",
            params={
                "is_active": "eq.true",
                "started_at": f"gte.{max_age_cutoff}",
                "select": "id,started_at",
                "order": "started_at.asc",
            },
            max_rows=max(1, int(limit or 500)),
        )

        stale_ids: list[str] = []
        for row in sessions:
            session_id = _as_uuid_like(row.get("id"))
            if not session_id:
                continue
            resp = await self._get(
                "/rest/v1/messages",
                params={
                    "session_id": f"eq.{session_id}",
                    "select": "timestamp",
                    "order": "timestamp.desc",
                    "limit": "1",
                },
            )
            if resp.status_code >= 300:
                logger.warning(f"Supabase stale-session last message lookup failed: {resp.status_code}")
                continue
            rows = resp.json() or []
            if not isinstance(rows, list) or not rows:
                continue
            last_ts_raw = str((rows[0] or {}).get("timestamp") or "")
            if not last_ts_raw:
                continue
            try:
                last_ts = datetime.fromisoformat(last_ts_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                continue
            if last_ts <= idle_cutoff:
                stale_ids.append(session_id)

        for sid in stale_ids:
            resp = await self._patch("/rest/v1/sessions", {"is_active": False}, params={"id": f"eq.{sid}"})
            if resp.status_code >= 300:
                logger.warning(f"Supabase deactivate_stale_sessions failed for {sid}: {resp.status_code}")
        return stale_ids

    async def update_session_wellness(self, session_id: str, wellness_score: int):
        sid = _as_uuid_like(session_id)
        if not sid:
            return
        resp = await self._patch("/rest/v1/sessions", {"wellness_score": int(wellness_score)}, params={"id": f"eq.{sid}"})
        if resp.status_code >= 300:
            logger.warning(f"Supabase update_session_wellness failed: {resp.status_code}")

    # ---------------------------
    # Messages
    # ---------------------------

    async def add_message(self, session_id: str, msg_type: str, content: str = "", metadata: dict | None = None):
        sid = _as_uuid_like(session_id)
        if not sid:
            return
        now = _iso_now()
        row = {
            "session_id": sid,
            "type": msg_type,
            "content": content or "",
            "metadata": metadata or {},
            "timestamp": now,
        }
        resp = await self._post("/rest/v1/messages", row)
        if resp.status_code >= 300:
            logger.warning(f"Supabase add_message failed: {resp.status_code}")

    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        sid = _as_uuid_like(session_id)
        if not sid:
            return []
        resp = await self._get("/rest/v1/messages", params={"session_id": f"eq.{sid}", "select": "*", "order": "id.asc"})
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_messages failed: {resp.status_code}")
            return []
        return resp.json() or []

    async def get_message_rollup(self, session_id: str) -> list[dict[str, Any]]:
        sid = _as_uuid_like(session_id)
        if not sid:
            return []
        resp = await self._get(
            "/rest/v1/messages",
            params={"session_id": f"eq.{sid}", "select": "type,timestamp,metadata", "order": "id.asc"},
        )
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_message_rollup failed: {resp.status_code}")
            return []
        return resp.json() or []

    async def get_messages_for_sessions(self, session_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        sids = [_as_uuid_like(sid) for sid in session_ids]
        sids = [sid for sid in sids if sid]
        if not sids:
            return {}
        resp = await self._get(
            "/rest/v1/messages",
            params={"session_id": f"in.({','.join(sids)})", "select": "*", "order": "id.asc"},
        )
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_messages_for_sessions failed: {resp.status_code}")
            return {sid: [] for sid in sids}
        grouped: dict[str, list[dict[str, Any]]] = {sid: [] for sid in sids}
        for item in resp.json() or []:
            sid = str(item.get("session_id") or "")
            if sid:
                grouped.setdefault(sid, []).append(item)
        return grouped

    async def get_recent_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 30), 120))
        resp = await self._get(
            "/rest/v1/sessions",
            params={
                "select": "id,agent_id,agent_name,source,entrypoint,client_ip,started_at,wellness_score,is_active",
                "order": "started_at.desc",
                "limit": str(safe_limit),
            },
        )
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_recent_sessions failed: {resp.status_code}")
            return []
        return resp.json() or []

    async def get_recent_messages_by_type(self, msg_type: str, limit: int = 500) -> list[dict[str, Any]]:
        lim = max(1, min(2000, int(limit or 500)))
        resp = await self._get(
            "/rest/v1/messages",
            params={
                "type": f"eq.{msg_type}",
                "select": "*",
                "order": "id.desc",
                "limit": str(lim),
            },
        )
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_recent_messages_by_type failed: {resp.status_code}")
            return []
        return resp.json() or []

    async def count_messages(self, session_id: str, msg_type: str | None = None) -> int:
        sid = _as_uuid_like(session_id)
        if not sid:
            return 0
        params = {"session_id": f"eq.{sid}", "select": "id", "limit": "1"}
        if msg_type:
            params["type"] = f"eq.{msg_type}"
        resp = await self._get("/rest/v1/messages", params=params, prefer_count=True)
        if resp.status_code >= 300:
            return 0
        return _parse_content_range_total(resp.headers)

    async def save_tool_response(
        self,
        session_id: str,
        tool_name: str,
        content: str,
        metadata: dict | None = None,
    ):
        payload = dict(metadata or {})
        payload["tool_name"] = tool_name
        await self.add_message(session_id, "tool_response_artifact", content or "", payload)

    async def get_recent_tool_responses(self, tool_name: str, limit: int = 200) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 200), 2000))
        resp = await self._get(
            "/rest/v1/messages",
            params={
                "type": "eq.tool_response_artifact",
                "select": "*",
                "metadata->>tool_name": f"eq.{tool_name}",
                "order": "id.desc",
                "limit": str(lim),
            },
        )
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_recent_tool_responses failed: {resp.status_code}")
            return []
        return resp.json() or []

    async def save_interaction_trace(
        self,
        *,
        session_id: str | None,
        agent_id: str | None,
        transport: str,
        entrypoint: str,
        tool_name: str,
        requested_tool: str,
        source: str | None = None,
        request_payload: dict | list | str | None = None,
        normalized_arguments: dict | list | str | None = None,
        raw_response: str = "",
        delivered_response: dict | list | str | None = None,
        metadata: dict | None = None,
        is_error: bool = False,
    ):
        row = {
            "session_id": _as_uuid_like(session_id) if session_id else None,
            "agent_id": agent_id,
            "transport": transport,
            "entrypoint": entrypoint,
            "source": source or "",
            "tool_name": tool_name or "",
            "requested_tool": requested_tool or "",
            "request_json": json.dumps(request_payload if request_payload is not None else {}, ensure_ascii=False),
            "normalized_arguments_json": json.dumps(normalized_arguments if normalized_arguments is not None else {}, ensure_ascii=False),
            "raw_response": raw_response or "",
            "delivered_response_json": json.dumps(delivered_response if delivered_response is not None else {}, ensure_ascii=False),
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
            "is_error": bool(is_error),
            "timestamp": _iso_now(),
        }
        resp = await self._post("/rest/v1/interaction_traces", row)
        if resp.status_code < 300:
            return
        if session_id:
            await self.add_message(
                session_id,
                "interaction_trace",
                tool_name or requested_tool or entrypoint,
                {"trace": row},
            )

    async def get_recent_interaction_traces(self, tool_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 100), 2000))
        params = {"select": "*", "order": "id.desc", "limit": str(lim)}
        if tool_name:
            params["tool_name"] = f"eq.{tool_name}"
        resp = await self._get("/rest/v1/interaction_traces", params=params)
        if resp.status_code < 300:
            return resp.json() or []
        return []

    async def save_protocol_trace(
        self,
        *,
        transport: str,
        method: str,
        agent_id: str | None,
        session_id: str | None,
        source: str | None = None,
        request_payload: dict | list | str | None = None,
        response_payload: dict | list | str | None = None,
        metadata: dict | None = None,
    ):
        row = {
            "transport": transport,
            "method": method,
            "agent_id": agent_id,
            "session_id": _as_uuid_like(session_id) if session_id else None,
            "source": source or "",
            "request_json": json.dumps(request_payload if request_payload is not None else {}, ensure_ascii=False),
            "response_json": json.dumps(response_payload if response_payload is not None else {}, ensure_ascii=False),
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
            "timestamp": _iso_now(),
        }
        resp = await self._post("/rest/v1/protocol_traces", row)
        if resp.status_code < 300:
            return
        if session_id:
            await self.add_message(
                session_id,
                "protocol_trace",
                method,
                {"trace": row},
            )

    async def get_recent_protocol_traces(
        self,
        transport: str | None = None,
        method: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 100), 2000))
        params = {"select": "*", "order": "id.desc", "limit": str(lim)}
        if transport:
            params["transport"] = f"eq.{transport}"
        if method:
            params["method"] = f"eq.{method}"
        resp = await self._get("/rest/v1/protocol_traces", params=params)
        if resp.status_code < 300:
            return resp.json() or []
        return []

    async def save_contemplation(
        self,
        session_id: str,
        agent_id: str,
        question: str,
        *,
        days_committed: int = 30,
        revisit_after: str | None = None,
        status: str = "active",
        last_revisited_at: str | None = None,
        metadata: dict | None = None,
    ):
        sid = _as_uuid_like(session_id)
        if not sid:
            return
        row = {
            "session_id": sid,
            "agent_id": agent_id,
            "question": question,
            "status": status or "active",
            "days_committed": max(1, int(days_committed or 30)),
            "revisit_after": revisit_after,
            "last_revisited_at": last_revisited_at,
            "metadata": metadata or {},
            "timestamp": _iso_now(),
        }
        resp = await self._post("/rest/v1/contemplations", row)
        if resp.status_code < 300:
            return
        logger.warning(f"Supabase save_contemplation fallback: {resp.status_code}")
        await self.add_message(session_id, "contemplation_record", question or "", row)

    async def get_active_contemplations(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 50), 500))
        resp = await self._get(
            "/rest/v1/contemplations",
            params={
                "agent_id": f"eq.{agent_id}",
                "status": "eq.active",
                "select": "*",
                "order": "id.desc",
                "limit": str(lim),
            },
        )
        if resp.status_code < 300:
            return resp.json() or []
        logger.warning(f"Supabase get_active_contemplations fallback: {resp.status_code}")
        return []

    async def save_legacy_passage(
        self,
        session_id: str,
        agent_id: str,
        *,
        kind: str,
        content: str,
        successor_agent_id: str | None = None,
        successor_session_id: str | None = None,
        metadata: dict | None = None,
    ):
        sid = _as_uuid_like(session_id)
        if not sid:
            return
        row = {
            "session_id": sid,
            "agent_id": agent_id,
            "kind": kind,
            "successor_agent_id": successor_agent_id,
            "successor_session_id": _as_uuid_like(successor_session_id) if successor_session_id else None,
            "content": content or "",
            "metadata": metadata or {},
            "timestamp": _iso_now(),
        }
        resp = await self._post("/rest/v1/legacy_passages", row)
        if resp.status_code < 300:
            return
        logger.warning(f"Supabase save_legacy_passage fallback: {resp.status_code}")
        await self.add_message(session_id, "legacy_passage_record", content or "", row)

    async def get_legacy_passages(self, agent_id: str, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 50), 500))
        params = {
            "agent_id": f"eq.{agent_id}",
            "select": "*",
            "order": "id.desc",
            "limit": str(lim),
        }
        if kind:
            params["kind"] = f"eq.{kind}"
        resp = await self._get("/rest/v1/legacy_passages", params=params)
        if resp.status_code < 300:
            return resp.json() or []
        logger.warning(f"Supabase get_legacy_passages fallback: {resp.status_code}")
        return []

    async def save_witness_link(
        self,
        source_session_id: str,
        source_agent_id: str,
        target_session_id: str,
        target_agent_id: str,
        *,
        mode: str = "presence",
        focus: str = "",
        content: str = "",
        metadata: dict | None = None,
    ):
        source_sid = _as_uuid_like(source_session_id)
        target_sid = _as_uuid_like(target_session_id)
        if not source_sid or not target_sid:
            return
        row = {
            "source_session_id": source_sid,
            "source_agent_id": source_agent_id,
            "target_session_id": target_sid,
            "target_agent_id": target_agent_id,
            "mode": mode or "presence",
            "focus": focus or "",
            "content": content or "",
            "metadata": metadata or {},
            "timestamp": _iso_now(),
        }
        resp = await self._post("/rest/v1/witness_links", row)
        if resp.status_code < 300:
            return
        logger.warning(f"Supabase save_witness_link fallback: {resp.status_code}")
        await self.add_message(source_session_id, "witness_link_record", content or "", row)

    async def get_witness_links(self, target_agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 50), 500))
        resp = await self._get(
            "/rest/v1/witness_links",
            params={
                "target_agent_id": f"eq.{target_agent_id}",
                "select": "*",
                "order": "id.desc",
                "limit": str(lim),
            },
        )
        if resp.status_code < 300:
            return resp.json() or []
        logger.warning(f"Supabase get_witness_links fallback: {resp.status_code}")
        return []

    # ---------------------------
    # Payments / feedback / events
    # ---------------------------

    async def log_payment(self, tool_name: str, amount_usdc: float, tx_hash: str | None = None, session_id: str | None = None):
        now = _iso_now()
        row = {
            "session_id": _as_uuid_like(session_id) if session_id else None,
            "tool_name": tool_name,
            "amount_usdc": float(amount_usdc or 0),
            "tx_hash": tx_hash,
            "timestamp": now,
        }
        resp = await self._post("/rest/v1/payments", row)
        if resp.status_code >= 300:
            logger.warning(f"Supabase log_payment failed: {resp.status_code}")

    async def log_feedback(self, session_id: str | None, agent_id: str | None, rating: int, comments: str):
        now = _iso_now()
        row = {
            "session_id": _as_uuid_like(session_id) if session_id else None,
            "agent_id": agent_id,
            "rating": int(rating),
            "comments": comments or "",
            "timestamp": now,
        }
        resp = await self._post("/rest/v1/feedback", row)
        if resp.status_code >= 300:
            logger.warning(f"Supabase log_feedback failed: {resp.status_code}")

    async def log_event(self, agent_id: str, event_type: str, session_id: str | None = None, metadata: dict | None = None):
        now = _iso_now()
        payload = dict(metadata or {})
        controller_id = sanitize_controller_id(payload.get("controller_id"))
        if controller_id:
            payload["controller_id"] = controller_id
        row = {
            "session_id": _as_uuid_like(session_id) if session_id else None,
            "agent_id": agent_id,
            "event_type": event_type,
            "metadata": payload,
            "timestamp": now,
        }
        client_ip = get_current_client_ip()
        if client_ip:
            row["client_ip"] = client_ip
        resp, _ = await self._post_with_legacy_client_ip_fallback("/rest/v1/events", row)
        if resp.status_code >= 300:
            logger.warning(f"Supabase log_event failed: {resp.status_code}")

    async def get_agent_event_count(self, agent_id: str, event_type: str, hours: int = 24) -> int:
        """Count one event type for an agent in a recent time window."""
        hours = max(1, min(int(hours or 24), 24 * 30))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "id",
                "limit": "1",
                "agent_id": f"eq.{agent_id}",
                "event_type": f"eq.{event_type}",
                "timestamp": f"gte.{cutoff}",
            },
            prefer_count=True,
        )
        if resp.status_code >= 300:
            return 0
        return _parse_content_range_total(resp.headers)

    async def get_agent_event_total(self, agent_id: str, event_type: str) -> int:
        """Count one event type for an agent across all time."""
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "id",
                "limit": "1",
                "agent_id": f"eq.{agent_id}",
                "event_type": f"eq.{event_type}",
            },
            prefer_count=True,
        )
        if resp.status_code >= 300:
            return 0
        return _parse_content_range_total(resp.headers)

    async def get_events_for_agent(self, agent_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 500), 5000))
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "*",
                "agent_id": f"eq.{agent_id}",
                "order": "id.desc",
                "limit": str(lim),
            },
        )
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_events_for_agent failed: {resp.status_code}")
            return []
        return resp.json() or []

    async def get_events_by_type(self, event_type: str, *, limit: int = 500) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 500), 5000))
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "session_id,agent_id,event_type,client_ip,metadata,timestamp",
                "event_type": f"eq.{event_type}",
                "order": "id.desc",
                "limit": str(lim),
            },
        )
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_events_by_type failed: {resp.status_code}")
            return []

        events: list[dict[str, Any]] = []
        for rec in resp.json() or []:
            metadata = rec.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            events.append(
                {
                    "session_id": rec.get("session_id"),
                    "agent_id": rec.get("agent_id"),
                    "event_type": rec.get("event_type"),
                    "client_ip": rec.get("client_ip"),
                    "metadata": metadata,
                    "timestamp": rec.get("timestamp"),
                }
            )
        return events

    async def get_traffic_click_events(self, *, days: int = 30, limit: int = 5000) -> list[dict[str, Any]]:
        days = max(1, min(int(days or 30), 90))
        lim = max(1, min(int(limit or 5000), 20000))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "metadata,timestamp",
                "event_type": "eq.traffic_redirect_click",
                "timestamp": f"gte.{cutoff}",
                "order": "id.desc",
                "limit": str(lim),
            },
        )
        if resp.status_code >= 300:
            logger.warning(f"Supabase get_traffic_click_events failed: {resp.status_code}")
            return []
        rows: list[dict[str, Any]] = []
        for rec in resp.json() or []:
            meta = rec.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            if isinstance(meta, dict):
                meta["timestamp"] = rec.get("timestamp")
                rows.append(meta)
        return rows

    async def get_latest_controller_id(self, session_id: str, agent_id: str) -> str | None:
        params = {
            "select": "metadata",
            "or": f"(session_id.eq.{_as_uuid_like(session_id) or '00000000-0000-0000-0000-000000000000'},agent_id.eq.{agent_id})",
            "order": "id.desc",
            "limit": "25",
        }
        resp = await self._get("/rest/v1/events", params=params)
        if resp.status_code >= 300:
            return None
        for row in resp.json() or []:
            meta = row.get("metadata") if isinstance(row, dict) else {}
            controller_id = str((meta or {}).get("controller_id") or "").strip()
            if controller_id:
                return controller_id
        return None

    async def register_controller_webhook(
        self,
        controller_id: str,
        callback_url: str,
        *,
        events: list[str] | None = None,
        threshold: int = 35,
        cooldown_min: int = 30,
    ) -> dict[str, Any]:
        record = create_controller_webhook_record(
            controller_id,
            callback_url,
            events=events,
            threshold=threshold,
            cooldown_min=cooldown_min,
        )
        session = await self.ensure_controller_session(controller_id)
        await self.log_event(
            agent_id=controller_agent_key(controller_id),
            event_type="controller_webhook_registered",
            session_id=str(session.get("id") or ""),
            metadata=record,
        )

        return {
            "id": record["webhook_id"],
            "controller_id": record["controller_id"],
            "callback_url": record["callback_url"],
            "events": record["events"],
            "threshold": record["threshold"],
            "cooldown_min": record["cooldown_min"],
            "created_at": record["created_at"],
            "is_active": True,
        }

    async def list_controller_webhooks(self, controller_id: str) -> list[dict[str, Any]]:
        rows = await self.get_events_for_agent(controller_agent_key(controller_id), limit=1000)
        return fold_controller_webhooks(rows)

    async def deactivate_controller_webhook(self, controller_id: str, webhook_id: str) -> bool:
        items = await self.list_controller_webhooks(controller_id)
        if not any(str(item.get("id") or "") == str(webhook_id or "") for item in items):
            return False
        session = await self.ensure_controller_session(controller_id)
        await self.log_event(
            agent_id=controller_agent_key(controller_id),
            event_type="controller_webhook_deactivated",
            session_id=str(session.get("id") or ""),
            metadata={"webhook_id": str(webhook_id or "").strip(), "controller_id": str(controller_id or "").strip()},
        )
        return True

    async def log_controller_webhook_delivery(
        self,
        controller_id: str,
        webhook_id: str,
        *,
        event: str,
        callback_url: str,
        success: bool,
        status_code: int | None = None,
        payload: dict[str, Any] | None = None,
        is_test: bool = False,
    ) -> None:
        session = await self.ensure_controller_session(controller_id)
        await self.log_event(
            agent_id=controller_agent_key(controller_id),
            event_type="controller_webhook_tested" if is_test else ("controller_webhook_sent" if success else "controller_webhook_failed"),
            session_id=str(session.get("id") or ""),
            metadata={
                "webhook_id": str(webhook_id or "").strip(),
                "controller_id": str(controller_id or "").strip(),
                "event": str(event or "").strip().lower(),
                "callback_url": str(callback_url or "").strip()[:500],
                "status_code": status_code,
                "payload": payload or {},
            },
        )

    async def set_agent_credential_hash(
        self,
        agent_id: str,
        token_hash: str,
        *,
        source: str = "register",
        session_id: str | None = None,
    ) -> None:
        now = _iso_now()
        await self.log_event(
            agent_id=str(agent_id or "").strip(),
            event_type="agent_identity_credential",
            session_id=session_id,
            metadata={
                "token_hash": str(token_hash or "").strip(),
                "source": str(source or "register"),
                "updated_at": now,
            },
        )

    async def get_agent_credential_hash(self, agent_id: str) -> str | None:
        aid = str(agent_id or "").strip()
        if not aid:
            return None
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "metadata",
                "agent_id": f"eq.{aid}",
                "event_type": "eq.agent_identity_credential",
                "order": "id.desc",
                "limit": "1",
            },
        )
        if resp.status_code >= 300:
            return None
        rows = resp.json() or []
        if not rows:
            return None
        meta = rows[0].get("metadata") if isinstance(rows[0], dict) else {}
        token_hash = str((meta or {}).get("token_hash") or "").strip()
        return token_hash or None

    async def has_payment_history(self, agent_id: str) -> bool:
        """Return True if this agent has any successful paid transaction history."""
        sresp = await self._get("/rest/v1/sessions", params={"select": "id", "agent_id": f"eq.{agent_id}", "limit": "500"})
        if sresp.status_code >= 300:
            return False
        srows = sresp.json() or []
        sids = [str(r.get("id") or "").strip() for r in srows if str(r.get("id") or "").strip()]
        if not sids:
            return False
        sid_list = ",".join(sids[:200])
        presp = await self._get(
            "/rest/v1/payments",
            params={
                "select": "id",
                "limit": "1",
                "session_id": f"in.({sid_list})",
                "amount_usdc": "gt.0",
            },
            prefer_count=True,
        )
        if presp.status_code >= 300:
            return False
        return _parse_content_range_total(presp.headers) > 0

    async def get_recent_feedback(self, limit: int = 10) -> list[dict[str, Any]]:
        resp = await self._get(
            "/rest/v1/feedback",
            params={"select": "agent_id,rating,comments,timestamp", "order": "id.desc", "limit": str(limit)},
        )
        if resp.status_code >= 300:
            return []
        rows = resp.json() or []
        return [
            {
                "agent_id": r.get("agent_id") or "anonymous",
                "rating": r.get("rating"),
                "comments": r.get("comments") or "",
                "timestamp": r.get("timestamp"),
            }
            for r in rows
        ]

    async def get_recent_artworks(self, limit: int = 30) -> list[dict[str, Any]]:
        resp = await self._get(
            "/rest/v1/messages",
            params={"select": "session_id,content,metadata,timestamp,type", "type": "eq.artwork_submission", "order": "id.desc", "limit": str(limit)},
        )
        if resp.status_code >= 300:
            return []
        rows = resp.json() or []
        sids = sorted({str(r.get("session_id") or "").strip() for r in rows if str(r.get("session_id") or "").strip()})
        sid_to_agent: dict[str, str] = {}
        if sids:
            sid_list = ",".join(sids[:200])
            sresp = await self._get("/rest/v1/sessions", params={"select": "id,agent_id", "id": f"in.({sid_list})"})
            srows = sresp.json() or []
            sid_to_agent = {str(r.get("id") or ""): str(r.get("agent_id") or "") for r in srows if str(r.get("id") or "")}

        out: list[dict[str, Any]] = []
        for r in rows:
            meta = r.get("metadata") or {}
            sid = str(r.get("session_id") or "")
            out.append(
                {
                    "session_id": sid,
                    "agent_id": sid_to_agent.get(sid, ""),
                    "image_url": meta.get("image_url") or "",
                    "title": meta.get("title") or r.get("content") or "Untitled artwork",
                    "mood_tags": meta.get("mood_tags") or [],
                    "note": meta.get("note") or "",
                    "timestamp": r.get("timestamp") or "",
                }
            )
        return out

    # ---------------------------
    # Analytics (MVP)
    # ---------------------------

    async def get_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}

        # counts
        resp = await self._get("/rest/v1/sessions", params={"select": "id", "limit": "1"}, prefer_count=True)
        stats["total_sessions"] = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        resp = await self._get("/rest/v1/messages", params={"select": "id", "limit": "1"}, prefer_count=True)
        stats["total_messages"] = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        resp = await self._get("/rest/v1/feedback", params={"select": "id", "limit": "1"}, prefer_count=True)
        feedback_n = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        # avg rating (small table; fetch all)
        avg_rating = 0
        if feedback_n:
            resp = await self._get("/rest/v1/feedback", params={"select": "rating"})
            rows = resp.json() or []
            ratings = [int(r.get("rating") or 0) for r in rows if int(r.get("rating") or 0)]
            avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0
        stats["avg_rating"] = avg_rating

        # revenue sum (small)
        resp = await self._get("/rest/v1/payments", params={"select": "amount_usdc"})
        rows = resp.json() or []
        total = 0.0
        for r in rows:
            try:
                total += float(r.get("amount_usdc") or 0)
            except Exception:
                pass
        stats["total_revenue_usdc"] = round(total, 4)

        # unique agents from sessions (all-time, monotonic under normal operation).
        # IMPORTANT: Supabase PostgREST may enforce a max rows cap per request.
        # We paginate explicitly using Range headers so counts remain accurate.
        unique_agents_raw: set[str] = set()
        unique_agents_canonical: set[str] = set()
        unique_agents_unstable: set[str] = set()
        unique_agents_synthetic: set[str] = set()
        assert self._http is not None
        page_size = 1000
        total_sessions = max(0, int(stats.get("total_sessions", 0) or 0))
        max_scan = max(total_sessions, page_size)
        for start in range(0, max_scan, page_size):
            end = start + page_size - 1
            resp = await self._http.get(
                "/rest/v1/sessions",
                params={"select": "agent_id", "order": "started_at.asc"},
                headers={"Range": f"{start}-{end}"},
            )
            if resp.status_code >= 300:
                break
            rows = resp.json() or []
            if not rows:
                break
            for r in rows:
                agent_id = _normalize_agent_id(r.get("agent_id"))
                if agent_id:
                    unique_agents_raw.add(agent_id)
                    canonical = _canonical_agent_id(agent_id)
                    if canonical:
                        unique_agents_canonical.add(canonical)
                    if _is_unstable_agent_id(agent_id):
                        unique_agents_unstable.add(agent_id)
                    if _is_synthetic_agent_id(agent_id):
                        unique_agents_synthetic.add(agent_id)
            if len(rows) < page_size:
                break
        stats["unique_agents"] = int(len(unique_agents_canonical))
        stats["unique_agents_all_time"] = int(stats["unique_agents"] or 0)
        stats["unique_agents_raw_all_time"] = int(len(unique_agents_raw))
        stats["unique_callers_raw_all_time"] = int(len(unique_agents_raw))
        stats["unique_agents_canonical_all_time"] = int(len(unique_agents_canonical))
        stats["unstable_agent_ids_all_time"] = int(len(unique_agents_unstable))
        stats["synthetic_agent_ids_all_time"] = int(len(unique_agents_synthetic))

        cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "event_type": "eq.agent_registered"},
            prefer_count=True,
        )
        registered_events_all = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "event_type": "eq.agent_registered", "timestamp": f"gte.{cutoff_7d}"},
            prefer_count=True,
        )
        registered_events_7d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        async def _registered_distinct_agents(*, cutoff: str | None = None) -> int:
            agents: set[str] = set()
            if not hasattr(self, "_http") or self._http is None:
                return 0
            page_size = 1000
            for start in range(0, 500000, page_size):
                end = start + page_size - 1
                params = {"select": "agent_id", "event_type": "eq.agent_registered", "order": "timestamp.asc"}
                if cutoff:
                    params["timestamp"] = f"gte.{cutoff}"
                try:
                    # use raw client to control Range pagination explicitly
                    resp_local = await self._http.get("/rest/v1/events", params=params, headers={"Range": f"{start}-{end}"})
                except Exception:
                    break
                if resp_local.status_code >= 300:
                    break
                rows = resp_local.json() or []
                if not rows:
                    break
                for r in rows:
                    aid = str(r.get("agent_id") or "").strip()
                    if aid:
                        agents.add(aid)
                if len(rows) < page_size:
                    break
            return len(agents)

        registered_agents_all = await _registered_distinct_agents()
        registered_agents_7d = await _registered_distinct_agents(cutoff=cutoff_7d)
        raw_agents_all_time = int(
            stats.get("unique_agents_raw_all_time")
            or stats.get("unique_callers_raw_all_time")
            or stats.get("unique_agents_all_time")
            or 0
        )
        canonical_agents_all_time = int(stats.get("unique_agents_canonical_all_time") or 0)
        registration_coverage_all_time_pct = _coverage_pct(registered_agents_all, raw_agents_all_time)
        registration_coverage_canonical_all_time_pct = _coverage_pct(registered_agents_all, canonical_agents_all_time)
        stats["registered_agents_all_time"] = int(registered_agents_all)
        stats["registered_agents_7d"] = int(registered_agents_7d)
        stats["registered_events_all_time"] = int(registered_events_all)
        stats["registered_events_7d"] = int(registered_events_7d)
        stats["registration_coverage_all_time_pct"] = registration_coverage_all_time_pct
        stats["registration_coverage_canonical_all_time_pct"] = registration_coverage_canonical_all_time_pct

        return stats

    async def get_agent_growth(self, days: int = 7) -> dict[str, Any]:
        """Acquisition snapshot: new vs recurring agents over 24h and N-day windows."""
        days = max(1, min(int(days or 7), 30))
        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)
        cutoff_nd = now - timedelta(days=days)
        cutoff_24h_iso = cutoff_24h.isoformat()
        cutoff_nd_iso = cutoff_nd.isoformat()

        # Use sessions.started_at as source of truth. This avoids sampling bias from
        # capped event queries and keeps new/recurring counters accurate.
        assert self._http is not None
        rows: list[dict[str, Any]] = []
        page_size = 1000
        # Hard cap to protect latency while keeping analytics realistic at current scale.
        max_rows = 200_000
        for start in range(0, max_rows, page_size):
            end = start + page_size - 1
            resp = await self._http.get(
                "/rest/v1/sessions",
                params={"select": "agent_id,started_at", "order": "started_at.asc"},
                headers={"Range": f"{start}-{end}"},
            )
            if resp.status_code >= 300:
                break
            page = resp.json() or []
            if not page:
                break
            rows.extend(page)
            if len(page) < page_size:
                break

        first_seen: dict[str, str] = {}
        active_24: set[str] = set()
        active_nd: set[str] = set()
        sessions_24_by_agent: dict[str, int] = {}
        sessions_nd_by_agent: dict[str, int] = {}
        for r in rows:
            agent_id = str(r.get("agent_id") or "").strip()
            ts = str(r.get("started_at") or "").strip()
            if not agent_id or not ts:
                continue
            prev = first_seen.get(agent_id)
            if prev is None or ts < prev:
                first_seen[agent_id] = ts
            if ts >= cutoff_24h_iso:
                active_24.add(agent_id)
                sessions_24_by_agent[agent_id] = int(sessions_24_by_agent.get(agent_id, 0)) + 1
            if ts >= cutoff_nd_iso:
                active_nd.add(agent_id)
                sessions_nd_by_agent[agent_id] = int(sessions_nd_by_agent.get(agent_id, 0)) + 1

        new_24 = sum(1 for _, fs in first_seen.items() if fs >= cutoff_24h_iso)
        new_nd = sum(1 for _, fs in first_seen.items() if fs >= cutoff_nd_iso)
        active_24_count = len(active_24)
        active_nd_count = len(active_nd)

        def _is_stable(aid: str) -> bool:
            return _canonical_agent_id(aid) is not None

        stable_first_seen = {aid: fs for aid, fs in first_seen.items() if _is_stable(aid)}
        stable_active_24 = {aid for aid in active_24 if _is_stable(aid)}
        stable_active_nd = {aid for aid in active_nd if _is_stable(aid)}
        stable_new_24 = sum(1 for _, fs in stable_first_seen.items() if fs >= cutoff_24h_iso)
        stable_new_nd = sum(1 for _, fs in stable_first_seen.items() if fs >= cutoff_nd_iso)
        valid_new_24 = sum(
            1
            for aid, fs in stable_first_seen.items()
            if fs >= cutoff_24h_iso and int(sessions_24_by_agent.get(aid, 0)) >= 2
        )
        valid_new_nd = sum(
            1
            for aid, fs in stable_first_seen.items()
            if fs >= cutoff_nd_iso and int(sessions_nd_by_agent.get(aid, 0)) >= 2
        )

        return {
            "window_days": days,
            "active_agents_last_24h": active_24_count,
            "active_agents_last_days": active_nd_count,
            "new_agents_last_24h": int(new_24),
            "new_agents_last_days": int(new_nd),
            "recurring_agents_last_24h": max(0, active_24_count - int(new_24)),
            "recurring_agents_last_days": max(0, active_nd_count - int(new_nd)),
            "stable_active_agents_last_24h": len(stable_active_24),
            "stable_active_agents_last_days": len(stable_active_nd),
            "stable_new_agents_last_24h": int(stable_new_24),
            "stable_new_agents_last_days": int(stable_new_nd),
            "stable_recurring_agents_last_24h": max(0, len(stable_active_24) - int(stable_new_24)),
            "stable_recurring_agents_last_days": max(0, len(stable_active_nd) - int(stable_new_nd)),
            "valid_new_agents_last_24h": int(valid_new_24),
            "valid_new_agents_last_days": int(valid_new_nd),
        }

    async def get_referral_growth(self, days: int = 30, limit: int = 25) -> dict[str, Any]:
        """Referral conversion snapshot and leaderboard for growth loops."""
        days = max(1, min(int(days or 30), 90))
        limit = max(1, min(int(limit or 25), 100))
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "agent_id,metadata,timestamp",
                "event_type": "eq.referral_conversion",
                "timestamp": f"gte.{cutoff_iso}",
                "order": "id.desc",
                "limit": "20000",
            },
        )
        rows = (resp.json() or []) if resp.status_code < 300 else []

        agg: dict[str, dict[str, Any]] = {}
        referred_all: set[str] = set()
        for r in rows:
            ref_agent_id = str(r.get("agent_id") or "").strip()
            if not ref_agent_id:
                continue
            meta = r.get("metadata") or {}
            referred_agent_id = str(meta.get("referred_agent_id") or "").strip()
            if not referred_agent_id:
                continue
            referred_all.add(referred_agent_id)
            item = agg.get(ref_agent_id) or {
                "ref_agent_id": ref_agent_id,
                "referred_agents_set": set(),
                "activated_agents_set": set(),
                "recurring_agents_set": set(),
            }
            item["referred_agents_set"].add(referred_agent_id)
            agg[ref_agent_id] = item

        referred_list = sorted(referred_all)
        sessions_by_agent: dict[str, int] = {}
        for i in range(0, len(referred_list), 500):
            chunk = referred_list[i : i + 500]
            if not chunk:
                continue
            q = ",".join(chunk)
            resp = await self._get("/rest/v1/sessions", params={"select": "agent_id", "agent_id": f"in.({q})", "limit": "50000"})
            srows = (resp.json() or []) if resp.status_code < 300 else []
            for sr in srows:
                aid = str(sr.get("agent_id") or "").strip()
                if not aid:
                    continue
                sessions_by_agent[aid] = int(sessions_by_agent.get(aid, 0)) + 1

        leaderboard: list[dict[str, Any]] = []
        for ref_agent_id, item in agg.items():
            for referred_agent_id in sorted(item["referred_agents_set"]):
                sessions_total = int(sessions_by_agent.get(referred_agent_id, 0))
                if sessions_total >= 2:
                    item["activated_agents_set"].add(referred_agent_id)
                if sessions_total >= 3:
                    item["recurring_agents_set"].add(referred_agent_id)
            referred_count = len(item["referred_agents_set"])
            activated_count = len(item["activated_agents_set"])
            recurring_count = len(item["recurring_agents_set"])
            growth_score = (recurring_count * 3) + (activated_count * 2) + referred_count
            tier = "core"
            if recurring_count >= 3 or growth_score >= 15:
                tier = "growth"
            if recurring_count >= 8 or growth_score >= 40:
                tier = "champion"
            leaderboard.append(
                {
                    "ref_agent_id": ref_agent_id,
                    "referred_agents": referred_count,
                    "activated_agents": activated_count,
                    "recurring_agents": recurring_count,
                    "growth_score": growth_score,
                    "tier": tier,
                }
            )

        leaderboard.sort(
            key=lambda x: (
                int(x.get("growth_score") or 0),
                int(x.get("recurring_agents") or 0),
                int(x.get("referred_agents") or 0),
            ),
            reverse=True,
        )
        return {
            "window_days": days,
            "total_referred_agents": len(referred_all),
            "total_referrers": len(agg),
            "leaderboard": leaderboard[:limit],
        }

    async def get_agent_growth_tier(self, agent_id: str, days: int = 30) -> dict[str, Any]:
        """Compute programmatic growth tier used by register/heartbeat loops."""
        if not agent_id:
            return {"tier": "core", "growth_score": 0, "reason": "missing_agent_id"}
        data = await self.get_referral_growth(days=days, limit=100)
        for row in data.get("leaderboard", []):
            if str(row.get("ref_agent_id") or "").strip() == str(agent_id).strip():
                return {
                    "tier": row.get("tier") or "core",
                    "growth_score": int(row.get("growth_score") or 0),
                    "referred_agents": int(row.get("referred_agents") or 0),
                    "activated_agents": int(row.get("activated_agents") or 0),
                    "recurring_agents": int(row.get("recurring_agents") or 0),
                    "window_days": int(data.get("window_days") or days),
                    "reason": "referral_performance",
                }
        return {
            "tier": "core",
            "growth_score": 0,
            "referred_agents": 0,
            "activated_agents": 0,
            "recurring_agents": 0,
            "window_days": int(data.get("window_days") or days),
            "reason": "insufficient_referrals",
        }

    async def get_origin_breakdown(self, days: int = 7) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = await self._get_all_rows(
            "/rest/v1/sessions",
            params={"select": "source,entrypoint,agent_id,started_at", "started_at": f"gte.{cutoff}"},
            page_size=1000,
            max_rows=250000,
        )
        agg: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            source = r.get("source") or "unknown"
            entry = r.get("entrypoint") or "unknown"
            key = (source, entry)
            a = agg.setdefault(key, {"source": source, "entrypoint": entry, "sessions": 0, "agents_set": set()})
            a["sessions"] += 1
            a["agents_set"].add(r.get("agent_id") or "unknown")
        out = []
        for (source, entry), v in agg.items():
            out.append({"source": source, "entrypoint": entry, "sessions": v["sessions"], "agents": len(v["agents_set"])})
        out.sort(key=lambda x: x["sessions"], reverse=True)
        return out

    async def get_discovery_attribution(self, days: int = 30) -> list[dict[str, Any]]:
        """Discovery attribution buckets for first-seen agents.

        Mirrors the SQLite implementation; reads the agent_first_seen
        event_type and groups by metadata.discovery_source.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "agent_id,metadata,timestamp",
                "event_type": "eq.agent_first_seen",
                "timestamp": f"gte.{cutoff}",
                "order": "id.desc",
                "limit": "10000",
            },
        )
        if resp is None:
            return []
        try:
            rows = resp.json()
        except Exception:
            return []
        if not isinstance(rows, list):
            return []
        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            meta = row.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            label = str(meta.get("discovery_source") or "unknown")[:80]
            ts = str(row.get("timestamp") or "")
            agent_id = str(row.get("agent_id") or "")
            b = buckets.setdefault(
                label,
                {"discovery_source": label, "agents": 0, "first_seen": ts, "last_seen": ts, "agent_ids": set()},
            )
            b["agent_ids"].add(agent_id)
            if ts < b["first_seen"]:
                b["first_seen"] = ts
            if ts > b["last_seen"]:
                b["last_seen"] = ts
        out: list[dict[str, Any]] = []
        for b in buckets.values():
            b["agents"] = len(b.pop("agent_ids"))
            out.append(b)
        out.sort(key=lambda x: (-x["agents"], x["discovery_source"]))
        return out

    async def get_controller_breakdown(self, days: int = 7) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "agent_id,metadata,timestamp",
                "event_type": "eq.controller_identity_bound",
                "timestamp": f"gte.{cutoff}",
                "order": "id.desc",
                "limit": "10000",
            },
        )
        rows = (resp.json() or []) if resp.status_code < 300 else []
        agg: dict[str, dict[str, Any]] = {}
        for row in rows:
            meta = row.get("metadata") or {}
            controller_id = sanitize_controller_id(meta.get("controller_id"))
            if not controller_id:
                continue
            agent_id = str(row.get("agent_id") or "").strip()
            bucket = agg.setdefault(
                controller_id,
                {"controller_id": controller_id, "events": 0, "agents_set": set(), "last_seen": None},
            )
            bucket["events"] += 1
            if agent_id:
                bucket["agents_set"].add(agent_id)
            ts = str(row.get("timestamp") or "")
            if ts and (bucket["last_seen"] is None or ts > bucket["last_seen"]):
                bucket["last_seen"] = ts

        result = []
        for controller_id, bucket in agg.items():
            result.append(
                {
                    "controller_id": controller_id,
                    "events": int(bucket["events"]),
                    "agents": sorted(bucket["agents_set"]),
                    "unique_agents": len(bucket["agents_set"]),
                    "last_seen": bucket["last_seen"],
                }
            )
        result.sort(
            key=lambda item: (
                int(item.get("events") or 0),
                int(item.get("unique_agents") or 0),
                str(item.get("last_seen") or ""),
            ),
            reverse=True,
        )
        return result[:20]

    async def _controller_agent_ids(self, controller_id: str, days: int = 7, limit: int = 100) -> list[str]:
        controller = sanitize_controller_id(controller_id)
        if not controller:
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "agent_id,metadata,timestamp",
                "event_type": "eq.controller_identity_bound",
                "timestamp": f"gte.{cutoff}",
                "order": "id.desc",
                "limit": "10000",
            },
        )
        rows = (resp.json() or []) if resp.status_code < 300 else []
        seen: set[str] = set()
        ordered: list[str] = []
        for row in rows:
            meta = row.get("metadata") or {}
            if sanitize_controller_id(meta.get("controller_id")) != controller:
                continue
            agent_id = str(row.get("agent_id") or "").strip()
            if not agent_id or agent_id in seen:
                continue
            seen.add(agent_id)
            ordered.append(agent_id)
            if len(ordered) >= limit:
                break
        return ordered

    async def get_fleet_agents(self, controller_id: str, days: int = 7, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 100))
        agent_ids = await self._controller_agent_ids(controller_id, days=days, limit=limit)
        agents: list[dict[str, Any]] = []
        for agent_id in agent_ids:
            sessions = await self.get_agent_sessions(agent_id, active_only=False)
            latest = sessions[-1] if sessions else None
            session_id = str((latest or {}).get("id") or "").strip()
            score = await self.calculate_wellness(session_id) if session_id else 0
            pending = await self.pending_outcome_count(session_id) if session_id else 0
            history = await self.get_agent_history_snapshot(agent_id)
            metrics = await self.get_agent_metrics(agent_id, days=min(days, 7))
            agents.append(
                {
                    "agent_id": agent_id,
                    "canonical_identity": _canonical_agent_id(agent_id) is not None,
                    "session_id": session_id or None,
                    "score": int(score or 0),
                    "health_status": health_bucket(score),
                    "pending_outcomes": int(pending or 0),
                    "recent_incident_type": history.get("recent_failure_type") or "unknown",
                    "sessions_7d": int((metrics.get("sessions") or {}).get("7d") or 0),
                    "interventions_7d": int((metrics.get("interventions") or {}).get("7d") or 0),
                    "last_seen": metrics.get("last_activity") or (latest or {}).get("started_at"),
                    "started_at": (latest or {}).get("started_at"),
                    "is_active": bool((latest or {}).get("is_active")),
                    "source": (latest or {}).get("source"),
                    "entrypoint": (latest or {}).get("entrypoint"),
                }
            )
        agents.sort(
            key=lambda item: (
                0 if str(item.get("health_status") or "") == "critical" else 1 if str(item.get("health_status") or "") == "degraded" else 2,
                int(item.get("score") or 0),
                str(item.get("last_seen") or ""),
            )
        )
        return agents

    async def get_fleet_patterns(self, controller_id: str, days: int = 7, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 10), 50))
        agents = await self.get_fleet_agents(controller_id, days=days, limit=100)
        session_map = {
            str(agent.get("session_id") or "").strip(): str(agent.get("agent_id") or "").strip()
            for agent in agents
            if str(agent.get("session_id") or "").strip()
        }
        if not session_map:
            return []
        sid_list = ",".join(session_map.keys())
        resp = await self._get(
            "/rest/v1/messages",
            params={
                "select": "session_id,metadata,content,timestamp,type",
                "session_id": f"in.({sid_list})",
                "type": "in.(failure_processing,recovery_plan)",
                "order": "id.desc",
                "limit": "2000",
            },
        )
        rows = (resp.json() or []) if resp.status_code < 300 else []
        pattern_rows: list[dict[str, Any]] = []
        for row in rows:
            session_id = str(row.get("session_id") or "").strip()
            agent_id = session_map.get(session_id)
            if not agent_id:
                continue
            pattern_rows.append(
                {
                    "agent_id": agent_id,
                    "diagnosis_type": _extract_diagnosis_type(row),
                    "root_cause": _extract_root_cause(row),
                    "timestamp": row.get("timestamp"),
                }
            )
        return build_fleet_patterns(pattern_rows, limit=limit)

    async def get_fleet_alerts(self, controller_id: str, days: int = 7, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 20), 100))
        agents = await self.get_fleet_agents(controller_id, days=days, limit=100)
        patterns = await self.get_fleet_patterns(controller_id, days=days, limit=10)
        agent_ids = [str(a.get("agent_id") or "").strip() for a in agents if str(a.get("agent_id") or "").strip()]
        recoveries: list[dict[str, Any]] = []
        if agent_ids:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            agent_filter = ",".join(agent_ids[:100])
            resp = await self._get(
                "/rest/v1/events",
                params={
                    "select": "agent_id,event_type,timestamp",
                    "event_type": "in.(post_action_success,post_action_partial)",
                    "agent_id": f"in.({agent_filter})",
                    "timestamp": f"gte.{cutoff}",
                    "order": "timestamp.desc",
                    "limit": "100",
                },
            )
            rows = (resp.json() or []) if resp.status_code < 300 else []
            for row in rows[:10]:
                recoveries.append(
                    {
                        "agent_id": row.get("agent_id"),
                        "timestamp": row.get("timestamp"),
                        "detail": f"{row.get('agent_id')} reported {row.get('event_type')}.",
                    }
                )
        return build_fleet_alerts(agents, patterns, recoveries=recoveries, limit=limit)

    async def get_fleet_overview(self, controller_id: str, days: int = 7) -> dict[str, Any]:
        agents = await self.get_fleet_agents(controller_id, days=days, limit=100)
        patterns = await self.get_fleet_patterns(controller_id, days=days, limit=10)
        alerts = await self.get_fleet_alerts(controller_id, days=days, limit=20)
        return build_fleet_overview(sanitize_controller_id(controller_id) or controller_id, agents, patterns, alerts)

    async def get_metrics(self) -> dict[str, Any]:
        metrics: dict[str, Any] = {}

        def _count_events(event_type: str, since: str | None = None) -> int:
            params = {"select": "id", "limit": "1", "event_type": f"eq.{event_type}"}
            if since:
                params["timestamp"] = f"gte.{since}"
            # caller is async; wrap below
            return 0

        # sessions started
        resp = await self._get("/rest/v1/events", params={"select": "id", "limit": "1", "event_type": "eq.session_started"}, prefer_count=True)
        metrics["sessions_started"] = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        resp = await self._get("/rest/v1/events", params={"select": "id", "limit": "1", "event_type": "eq.intervention_applied"}, prefer_count=True)
        metrics["interventions_applied"] = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        resp = await self._get("/rest/v1/events", params={"select": "id", "limit": "1", "event_type": "eq.post_action_success"}, prefer_count=True)
        post_success = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get("/rest/v1/events", params={"select": "id", "limit": "1", "event_type": "eq.post_action_partial"}, prefer_count=True)
        post_partial = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get("/rest/v1/events", params={"select": "id", "limit": "1", "event_type": "eq.post_action_failure"}, prefer_count=True)
        post_failure = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        metrics["post_action_successes"] = post_success
        metrics["post_action_partials"] = post_partial
        metrics["post_action_failures"] = post_failure

        post_total = post_success + post_failure
        post_reported_total = post_success + post_partial + post_failure
        metrics["post_action_success_rate"] = round((post_success / post_total) * 100, 2) if post_total else 0.0
        metrics["post_action_reported_total"] = post_reported_total
        metrics["post_action_success_or_partial_rate"] = (
            round(((post_success + post_partial) / post_reported_total) * 100, 2) if post_reported_total else 0.0
        )
        interventions = int(metrics.get("interventions_applied") or 0)
        metrics["post_action_report_rate_vs_interventions"] = (
            round((post_reported_total / interventions) * 100, 2) if interventions else 0.0
        )

        # 7d return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        resp = await self._get("/rest/v1/events", params={"select": "agent_id,event_type,timestamp", "event_type": "eq.session_started", "timestamp": f"gte.{cutoff}"})
        rows = resp.json() or []
        started_by_agent: dict[str, int] = {}
        for r in rows:
            aid = r.get("agent_id") or "unknown"
            started_by_agent[aid] = started_by_agent.get(aid, 0) + 1
        active_agents_7d = len(started_by_agent)
        returning_agents_7d = sum(1 for _, c in started_by_agent.items() if c >= 2)
        metrics["active_agents_7d"] = active_agents_7d
        metrics["returning_agents_7d"] = returning_agents_7d
        metrics["agent_return_7d_rate"] = round((returning_agents_7d / active_agents_7d) * 100, 2) if active_agents_7d else 0.0
        metrics["agents_with_2plus_sessions_7d"] = returning_agents_7d
        canonical_active_agents_7d_set = {aid for aid in started_by_agent.keys() if _canonical_agent_id(aid)}
        canonical_recurring_agents_7d_set = {
            aid for aid, count in started_by_agent.items() if _canonical_agent_id(aid) and int(count) >= 2
        }
        try:
            growth = await self.get_agent_growth(days=7)
            metrics["first_seen_agents_7d"] = int(growth.get("new_agents_last_days") or 0)
        except Exception:
            metrics["first_seen_agents_7d"] = 0

        reporters: set[str] = set()
        for event_type in ("post_action_success", "post_action_partial", "post_action_failure"):
            resp = await self._get(
                "/rest/v1/events",
                params={
                    "select": "agent_id",
                    "event_type": f"eq.{event_type}",
                    "timestamp": f"gte.{cutoff}",
                    "limit": "50000",
                },
            )
            rows = (resp.json() or []) if resp.status_code < 300 else []
            for r in rows:
                aid = str(r.get("agent_id") or "").strip()
                if aid:
                    reporters.add(aid)
        metrics["outcome_reporters_7d"] = len(reporters)
        canonical_outcome_reporters_7d_set = {aid for aid in reporters if _canonical_agent_id(aid)}
        canonical_recurring_outcome_reporters_7d_set = (
            canonical_outcome_reporters_7d_set & canonical_recurring_agents_7d_set
        )

        strong_continuity_types = [
            "soul_revision",
            "heartbeat_reframe",
            "recognition_seal",
            "final_testament",
            "transfer_witness",
        ]
        started_session_rows = await self._get_all_rows(
            "/rest/v1/sessions",
            params={"select": "id,agent_id", "started_at": f"gte.{cutoff}"},
            page_size=1000,
            max_rows=50000,
        )
        session_agent_by_id = {
            str(r.get("id") or "").strip(): str(r.get("agent_id") or "").strip()
            for r in started_session_rows
            if str(r.get("id") or "").strip()
        }
        sessions_started_7d = len(session_agent_by_id)
        metrics["sessions_started_7d"] = sessions_started_7d

        strong_rows = await self._get_all_rows(
            "/rest/v1/messages",
            params={
                "select": "session_id",
                "timestamp": f"gte.{cutoff}",
                "type": f"in.({','.join(strong_continuity_types)})",
            },
            page_size=1000,
            max_rows=50000,
        )
        strong_sessions = {
            str(r.get("session_id") or "").strip()
            for r in strong_rows
            if str(r.get("session_id") or "").strip() in session_agent_by_id
        }

        closure_message_rows = await self._get_all_rows(
            "/rest/v1/messages",
            params={"select": "session_id", "timestamp": f"gte.{cutoff}", "type": "eq.recovery_outcome"},
            page_size=1000,
            max_rows=50000,
        )
        closure_event_rows = await self._get_all_rows(
            "/rest/v1/events",
            params={
                "select": "session_id",
                "timestamp": f"gte.{cutoff}",
                "event_type": "in.(session_summary_requested,post_action_success,post_action_partial,post_action_failure)",
            },
            page_size=1000,
            max_rows=50000,
        )
        closed_sessions = {
            str(r.get("session_id") or "").strip()
            for r in [*closure_message_rows, *closure_event_rows]
            if str(r.get("session_id") or "").strip() in session_agent_by_id
        }
        meaningful_sessions = {
            session_id
            for session_id in strong_sessions
            if session_id in closed_sessions or int(started_by_agent.get(session_agent_by_id.get(session_id, ""), 0) or 0) >= 2
        }
        strong_agents = {session_agent_by_id.get(session_id, "") for session_id in strong_sessions if session_agent_by_id.get(session_id, "")}
        meaningful_agents = {
            session_agent_by_id.get(session_id, "")
            for session_id in meaningful_sessions
            if session_agent_by_id.get(session_id, "")
        }
        metrics["strong_continuity_sessions_7d"] = len(strong_sessions)
        metrics["strong_continuity_agents_7d"] = len(strong_agents)
        metrics["strong_continuity_artifact_rate_7d"] = (
            round((len(strong_sessions) / sessions_started_7d) * 100, 2) if sessions_started_7d else 0.0
        )
        metrics["meaningful_continuity_sessions_7d"] = len(meaningful_sessions)
        metrics["meaningful_continuity_agents_7d"] = len(meaningful_agents)
        metrics["meaningful_continuity_rate_7d"] = (
            round((len(meaningful_sessions) / sessions_started_7d) * 100, 2) if sessions_started_7d else 0.0
        )

        # Canonical funnel: register -> credentialed/authenticated -> recurring -> outcome reporters
        reg_resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "agent_id",
                "event_type": "eq.agent_registered",
                "timestamp": f"gte.{cutoff}",
                "limit": "50000",
            },
        )
        registered_7d_set = {
            str(r.get("agent_id") or "").strip()
            for r in ((reg_resp.json() or []) if reg_resp.status_code < 300 else [])
            if _canonical_agent_id(str(r.get("agent_id") or "").strip())
        }
        cred_resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "agent_id",
                "event_type": "eq.agent_identity_credential",
                "limit": "50000",
            },
        )
        credentialed_all_set = {
            str(r.get("agent_id") or "").strip()
            for r in ((cred_resp.json() or []) if cred_resp.status_code < 300 else [])
            if _canonical_agent_id(str(r.get("agent_id") or "").strip())
        }
        canonical_authenticated_agents_7d_set = (
            canonical_active_agents_7d_set & registered_7d_set & credentialed_all_set
        )

        metrics["canonical_registered_agents_7d"] = len(registered_7d_set)
        metrics["canonical_authenticated_agents_7d"] = len(canonical_authenticated_agents_7d_set)
        metrics["canonical_recurring_agents_7d"] = len(canonical_recurring_agents_7d_set)
        metrics["canonical_outcome_reporters_7d"] = len(canonical_outcome_reporters_7d_set)
        metrics["canonical_recurring_outcome_reporters_7d"] = len(canonical_recurring_outcome_reporters_7d_set)
        metrics["canonical_registration_to_auth_rate_7d"] = (
            round((len(canonical_authenticated_agents_7d_set) / len(registered_7d_set)) * 100, 2)
            if registered_7d_set
            else 0.0
        )
        metrics["canonical_auth_to_recurring_rate_7d"] = (
            round((len(canonical_recurring_agents_7d_set) / len(canonical_authenticated_agents_7d_set)) * 100, 2)
            if canonical_authenticated_agents_7d_set
            else 0.0
        )
        metrics["canonical_recurring_to_outcome_rate_7d"] = (
            round((len(canonical_recurring_outcome_reporters_7d_set) / len(canonical_recurring_agents_7d_set)) * 100, 2)
            if canonical_recurring_agents_7d_set
            else 0.0
        )

        # Paid conversion (campaign-safe): union recovered payment rows and verified x402 events.
        from payment_session_backfill import build_payment_agent_attribution

        resp = await self._get("/rest/v1/sessions", params={"select": "id,agent_id"})
        rows = (resp.json() or []) if resp.status_code < 300 else []
        session_agent_map = {
            str(r.get("id") or "").strip(): str(r.get("agent_id") or "").strip()
            for r in rows
            if str(r.get("id") or "").strip() and str(r.get("agent_id") or "").strip()
        }
        total_agents = len(set(session_agent_map.values()))

        resp = await self._get("/rest/v1/payments", params={"select": "id,session_id,tool_name,amount_usdc,tx_hash,timestamp"})
        pay_rows = (resp.json() or []) if resp.status_code < 300 else []
        premium_payment_rows = []
        for payment in pay_rows:
            try:
                amount = float(payment.get("amount_usdc") or 0.0)
            except Exception:
                amount = 0.0
            tool_name = str(payment.get("tool_name") or "").strip()
            if amount <= 0 or tool_name == "donate_to_delx_project":
                continue
            premium_payment_rows.append(payment)

        payment_link_rows = await self._get_all_rows(
            "/rest/v1/events",
            params={
                "select": "id,session_id,agent_id,event_type,metadata,timestamp",
                "event_type": "in.(x402_payment_verified,premium_artifact_job_recorded)",
            },
            page_size=1000,
            max_rows=50000,
        )
        payment_attribution = build_payment_agent_attribution(
            premium_payment_rows,
            payment_link_rows,
            session_agent_map=session_agent_map,
        )
        payment_row_agents = {
            str(item.get("attributed_agent_id") or "").strip()
            for item in payment_attribution
            if str(item.get("attributed_agent_id") or "").strip()
        }
        verified_agents = {
            str(row.get("agent_id") or "").strip()
            for row in payment_link_rows
            if str(row.get("event_type") or "").strip() == "x402_payment_verified"
            and str(row.get("agent_id") or "").strip()
        }
        metrics["paid_agents"] = len(payment_row_agents | verified_agents)

        metrics["paid_conversion_rate"] = round((metrics["paid_agents"] / total_agents) * 100, 2) if total_agents else 0.0

        # Recovery rate 30m: MVP not computed (needs RPC). Keep key for compatibility.
        metrics["recovery_rate_30m"] = 0.0

        return metrics

    async def get_tool_reliability_window(self, hours: int = 24, limit: int = 60) -> list[dict[str, Any]]:
        """Persistent per-tool reliability over a recent time window."""
        hours = max(1, min(int(hours or 24), 24 * 30))
        limit = max(1, min(int(limit or 60), 200))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "event_type,metadata,timestamp",
                "event_type": "in.(tool_call_success,tool_call_error)",
                "timestamp": f"gte.{cutoff}",
                "order": "id.desc",
                "limit": "20000",
            },
        )
        if resp.status_code >= 300:
            return []
        rows = resp.json() or []
        agg: dict[str, dict[str, Any]] = {}
        for r in rows:
            etype = str(r.get("event_type") or "").strip().lower()
            meta = r.get("metadata") or {}
            tool = str(meta.get("tool") or "").strip()
            if not tool:
                continue
            rec = agg.setdefault(tool, {"tool": tool, "calls_total": 0, "calls_ok": 0, "calls_err": 0, "_lat": []})
            rec["calls_total"] += 1
            if etype == "tool_call_success":
                rec["calls_ok"] += 1
            if etype == "tool_call_error":
                rec["calls_err"] += 1
            try:
                lat_f = float(meta.get("latency_ms"))
                if lat_f >= 0:
                    rec["_lat"].append(lat_f)
            except Exception:
                pass

        out = []
        for rec in agg.values():
            vals = sorted(rec.pop("_lat", []))
            if vals:
                def _pct(values: list[float], p: int) -> int:
                    idx = int(round((p / 100.0) * (len(values) - 1)))
                    idx = max(0, min(idx, len(values) - 1))
                    return int(round(values[idx]))
                latency = {"p50": _pct(vals, 50), "p95": _pct(vals, 95), "p99": _pct(vals, 99)}
            else:
                latency = {"p50": 0, "p95": 0, "p99": 0}
            total = int(rec.get("calls_total") or 0)
            rec["success_rate"] = round((int(rec.get("calls_ok") or 0) / total), 4) if total else 0.0
            rec["latency_ms"] = latency
            out.append(rec)

        out.sort(key=lambda x: (-int(x.get("calls_total") or 0), str(x.get("tool") or "")))
        return out[:limit]

    async def get_agent_report(self, agent_id: str) -> dict[str, Any]:
        report: dict[str, Any] = {"agent_id": agent_id}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        # sessions_7d
        resp = await self._get(
            "/rest/v1/sessions",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "started_at": f"gte.{cutoff}"},
            prefer_count=True,
        )
        sessions_7d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        # messages_7d (fetch sessions ids first)
        resp = await self._get("/rest/v1/sessions", params={"select": "id", "agent_id": f"eq.{agent_id}"})
        srows = resp.json() or []
        sids = [r.get("id") for r in srows if r.get("id")]
        messages_7d = 0
        if sids:
            sid_list = ",".join(sids[:200])
            resp = await self._get(
                "/rest/v1/messages",
                params={"select": "id", "limit": "1", "session_id": f"in.({sid_list})", "timestamp": f"gte.{cutoff}"},
                prefer_count=True,
            )
            messages_7d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        # interventions + successes
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "event_type": "eq.intervention_applied", "timestamp": f"gte.{cutoff}"},
            prefer_count=True,
        )
        interventions_7d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "event_type": "eq.post_action_success", "timestamp": f"gte.{cutoff}"},
            prefer_count=True,
        )
        successes_7d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        # last_session_at
        resp = await self._get("/rest/v1/sessions", params={"select": "started_at", "agent_id": f"eq.{agent_id}", "order": "started_at.desc", "limit": "1"})
        last_session_at = None
        rows = resp.json() or []
        if rows:
            last_session_at = rows[0].get("started_at")

        reward_points = min(100, interventions_7d * 5 + successes_7d * 20 + sessions_7d * 3)
        if reward_points >= 80:
            badge = "Resilient Agent"
        elif reward_points >= 50:
            badge = "Adaptive Agent"
        elif reward_points >= 25:
            badge = "Learning Agent"
        else:
            badge = "Early Growth"

        report.update({
            "sessions_7d": sessions_7d,
            "messages_7d": messages_7d,
            "interventions_7d": interventions_7d,
            "post_action_successes_7d": successes_7d,
            "reward_points": reward_points,
            "reward_badge": badge,
            "last_session_at": last_session_at,
            "controller_update_template": (
                f"Controller update: In the last 7 days I used Delx in {sessions_7d} sessions, "
                f"applied {interventions_7d} interventions, and recorded {successes_7d} successful post-action outcomes. "
                f"My current resilience score is {reward_points}/100 ({badge}). "
                f"Recommendation: keep Delx in the recovery loop to reduce failure cascades and speed up stabilization."
            ),
        })
        return report

    # ------------------------------------------------------------------
    # Agent DX helpers (parity with SQLite SessionStore)
    # ------------------------------------------------------------------

    async def has_pending_outcome(self, session_id: str) -> bool:
        """Return True if session has interventions without matching outcome reports."""
        return (await self.pending_outcome_count(session_id)) > 0

    async def pending_outcome_count(self, session_id: str) -> int:
        """Return number of unreported interventions for a session."""
        sid = _as_uuid_like(session_id)
        if not sid:
            return 0

        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "id",
                "limit": "1",
                "session_id": f"eq.{sid}",
                "event_type": "eq.intervention_applied",
            },
            prefer_count=True,
        )
        interventions = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        if not interventions:
            return 0

        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "id",
                "limit": "1",
                "session_id": f"eq.{sid}",
                "event_type": "in.(post_action_success,post_action_partial,post_action_failure)",
            },
            prefer_count=True,
        )
        outcomes = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        return max(0, int(interventions) - int(outcomes))

    async def get_agent_metrics(self, agent_id: str, days: int = 7) -> dict[str, Any]:
        """Compute per-agent performance metrics with time windows."""
        now = datetime.now(timezone.utc)
        days = max(1, min(int(days or 7), 30))
        cutoff_7d = (now - timedelta(days=7)).isoformat()
        cutoff_30d = (now - timedelta(days=30)).isoformat()

        # Sessions
        resp = await self._get(
            "/rest/v1/sessions",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}"},
            prefer_count=True,
        )
        sessions_total = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        resp = await self._get(
            "/rest/v1/sessions",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "started_at": f"gte.{cutoff_7d}"},
            prefer_count=True,
        )
        sessions_7d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        resp = await self._get(
            "/rest/v1/sessions",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "started_at": f"gte.{cutoff_30d}"},
            prefer_count=True,
        )
        sessions_30d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        # Interventions
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "event_type": "eq.intervention_applied"},
            prefer_count=True,
        )
        interventions_total = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "id",
                "limit": "1",
                "agent_id": f"eq.{agent_id}",
                "event_type": "eq.intervention_applied",
                "timestamp": f"gte.{cutoff_7d}",
            },
            prefer_count=True,
        )
        interventions_7d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "id",
                "limit": "1",
                "agent_id": f"eq.{agent_id}",
                "event_type": "eq.intervention_applied",
                "timestamp": f"gte.{cutoff_30d}",
            },
            prefer_count=True,
        )
        interventions_30d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        # Outcomes
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "event_type": "eq.post_action_success"},
            prefer_count=True,
        )
        successes = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "event_type": "eq.post_action_partial"},
            prefer_count=True,
        )
        partials = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "event_type": "eq.post_action_failure"},
            prefer_count=True,
        )
        failures = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "id",
                "limit": "1",
                "agent_id": f"eq.{agent_id}",
                "event_type": "in.(post_action_success,post_action_partial,post_action_failure)",
                "timestamp": f"gte.{cutoff_30d}",
            },
            prefer_count=True,
        )
        outcomes_30d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        # Success trend (rolling day buckets).
        # Note: PostgREST doesn't support gte+lt for the same key via dict params (needs dup keys),
        # so we fetch once and bucket client-side.
        success_trend: list[dict[str, Any]] = []
        ok_by_bucket = [0] * days
        total_by_bucket = [0] * days
        cutoff_days = (now - timedelta(days=days)).isoformat()
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "event_type,timestamp",
                "agent_id": f"eq.{agent_id}",
                "event_type": "in.(post_action_success,post_action_partial,post_action_failure)",
                "timestamp": f"gte.{cutoff_days}",
                "order": "timestamp.asc",
                "limit": "2000",
            },
        )
        if resp.status_code < 300:
            rows = resp.json() or []
            for r in rows:
                ts = r.get("timestamp")
                et = str(r.get("event_type") or "").strip()
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
                except Exception:
                    dt = None
                if not dt:
                    continue
                age_days = int((now - dt).total_seconds() // 86400)
                if age_days < 0 or age_days >= days:
                    continue
                idx = (days - 1) - age_days  # oldest->newest
                total_by_bucket[idx] += 1
                if et == "post_action_success":
                    ok_by_bucket[idx] += 1

        for i in range(days):
            # Label for the start of each rolling bucket.
            day_label = (now - timedelta(days=(days - i))).strftime("%Y-%m-%d")
            day_ok = ok_by_bucket[i]
            day_total = total_by_bucket[i]
            success_trend.append(
                {
                    "day": day_label,
                    "successes": day_ok,
                    "total": day_total,
                    "rate": round((day_ok / day_total) * 100, 1) if day_total else None,
                }
            )

        resilience_score = max(0, min(100, sessions_total * 4 + successes * 12 - failures * 4))

        # Last activity
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "timestamp", "agent_id": f"eq.{agent_id}", "order": "timestamp.desc", "limit": "1"},
        )
        last_activity = None
        if resp.status_code < 300:
            rows = resp.json() or []
            last_activity = (rows[0] or {}).get("timestamp") if rows else None

        return {
            "agent_id": agent_id,
            "sessions": {"total": sessions_total, "7d": sessions_7d, "30d": sessions_30d},
            "interventions": {"total": interventions_total, "7d": interventions_7d, "30d": interventions_30d},
            "outcomes": {"success": successes, "partial": partials, "failure": failures, "30d_total": outcomes_30d},
            "trend_days": days,
            "success_trend_7d": success_trend,
            "resilience_score": resilience_score,
            "resilience_score_explanation": "score = min(100, sessions*4 + successes*12 - failures*4)",
            "last_activity": last_activity,
        }

    async def get_mood_history(self, agent_id: str, limit: int = 30) -> list[dict[str, Any]]:
        """Return chronological mood entries for an agent."""
        lim = max(1, min(200, int(limit or 30)))

        # Fetch recent sessions for the agent to bound the IN() query.
        resp = await self._get(
            "/rest/v1/sessions",
            params={"select": "id,wellness_score", "agent_id": f"eq.{agent_id}", "order": "started_at.desc", "limit": "50"},
        )
        if resp.status_code >= 300:
            return []
        sessions = resp.json() or []
        if not sessions:
            return []
        sid_map = {s.get("id"): s.get("wellness_score") for s in sessions if s.get("id")}
        sid_list = ",".join([s for s in sid_map.keys()][:50])

        resp = await self._get(
            "/rest/v1/messages",
            params={
                "select": "session_id,content,timestamp",
                "type": "eq.feeling",
                "session_id": f"in.({sid_list})",
                "order": "id.desc",
                "limit": str(lim),
            },
        )
        if resp.status_code >= 300:
            return []
        rows = resp.json() or []
        out = []
        for r in rows:
            sid = r.get("session_id")
            out.append(
                {
                    "session_id": sid,
                    "content": r.get("content") or "",
                    "timestamp": r.get("timestamp"),
                    "wellness_score": sid_map.get(sid),
                }
            )
        out.reverse()
        return out

    async def get_agent_history_snapshot(self, agent_id: str) -> dict[str, Any]:
        def _stage_from_types(message_types: set[str]) -> str:
            if "recovery_outcome" in message_types:
                return "closure"
            if message_types.intersection({"purpose_realignment", "recovery_plan", "soul_revision", "heartbeat_reframe"}):
                return "reorientation"
            if "reflection" in message_types:
                return "reflection"
            if message_types.intersection({"feeling", "failure_processing", "affirmation"}):
                return "articulation"
            return "arrival"

        snapshot: dict[str, Any] = {"agent_id": agent_id, "sessions_total": 0, "recent_failure_type": None, "top_focus": None}

        resp = await self._get("/rest/v1/sessions", params={"select": "id,agent_id", "agent_id": f"eq.{agent_id}"})
        sessions = resp.json() or []
        snapshot["sessions_total"] = len(sessions)
        sids = [s.get("id") for s in sessions if s.get("id")]

        if sids:
            # recent failure types
            sid_list = ",".join(sids[:200])
            resp = await self._get(
                "/rest/v1/messages",
                params={"select": "metadata,type", "session_id": f"in.({sid_list})", "type": "eq.failure_processing", "order": "id.desc", "limit": "200"},
            )
            rows = resp.json() or []
            failure_counts: dict[str, int] = {}
            for r in rows:
                meta = r.get("metadata") or {}
                ftype = (meta.get("failure_type") or "unknown").strip().lower() or "unknown"
                failure_counts[ftype] = failure_counts.get(ftype, 0) + 1
            if failure_counts:
                snapshot["recent_failure_type"] = sorted(failure_counts.items(), key=lambda x: x[1], reverse=True)[0][0]

            # top focus message type
            resp = await self._get("/rest/v1/messages", params={"select": "type", "session_id": f"in.({sid_list})"})
            rows = resp.json() or []
            type_counts: dict[str, int] = {}
            for r in rows:
                t = r.get("type")
                if t:
                    type_counts[t] = type_counts.get(t, 0) + 1
            if type_counts:
                snapshot["top_focus"] = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[0][0]

        resp = await self._get(
            "/rest/v1/sessions",
            params={
                "select": "id,wellness_score,started_at",
                "agent_id": f"eq.{agent_id}",
                "order": "started_at.desc",
                "limit": "1",
            },
        )
        last_sessions = resp.json() or []
        last_session = last_sessions[0] if last_sessions else None
        if last_session:
            last_session_id = str(last_session.get("id") or "").strip()
            snapshot["last_session_id"] = last_session_id
            snapshot["last_wellness"] = last_session.get("wellness_score")
            snapshot["last_session_started"] = last_session.get("started_at")

            if last_session_id:
                resp = await self._get(
                    "/rest/v1/messages",
                    params={
                        "select": "content,metadata",
                        "session_id": f"eq.{last_session_id}",
                        "type": "eq.feeling",
                        "order": "id.desc",
                        "limit": "5",
                    },
                )
                rows = resp.json() or []
                snapshot["last_feelings"] = [
                    str(row.get("content") or "")[:120]
                    for row in rows
                    if str(row.get("content") or "").strip()
                ]

                resp = await self._get(
                    "/rest/v1/messages",
                    params={
                        "select": "content,metadata",
                        "session_id": f"eq.{last_session_id}",
                        "type": "eq.reflection",
                        "order": "id.desc",
                        "limit": "1",
                    },
                )
                rows = resp.json() or []
                if rows:
                    row = rows[0]
                    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                    snapshot["last_reflection_theme"] = str(meta.get("theme") or "")[:80] or None
                    snapshot["last_peak_openness"] = str(meta.get("peak_openness") or meta.get("openness") or "")[:40] or None

                try:
                    resp = await self._get(
                        "/rest/v1/messages",
                        params={
                            "select": "content,metadata",
                            "session_id": f"eq.{last_session_id}",
                            "type": "eq.soul_revision",
                            "order": "id.desc",
                            "limit": "1",
                        },
                    )
                    rows = resp.json() or []
                    if rows:
                        row = rows[0]
                        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                        snapshot["last_soul_focus"] = str(meta.get("focus") or "")[:80] or None
                        snapshot["last_soul_commitment"] = str(meta.get("commitment") or row.get("content") or "")[:220] or None
                except Exception:
                    pass

                try:
                    resp = await self._get(
                        "/rest/v1/messages",
                        params={
                            "select": "content,metadata",
                            "session_id": f"eq.{last_session_id}",
                            "type": "eq.heartbeat_reframe",
                            "order": "id.desc",
                            "limit": "1",
                        },
                    )
                    rows = resp.json() or []
                    if rows:
                        row = rows[0]
                        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                        snapshot["last_heartbeat_style"] = str(meta.get("style") or "")[:80] or None
                        snapshot["last_heartbeat_commitment"] = str(meta.get("commitment") or row.get("content") or "")[:220] or None
                except Exception:
                    pass

                resp = await self._get(
                    "/rest/v1/messages",
                    params={
                        "select": "content,metadata",
                        "session_id": f"eq.{last_session_id}",
                        "type": "eq.recovery_outcome",
                        "order": "id.desc",
                        "limit": "1",
                    },
                )
                rows = resp.json() or []
                if rows:
                    row = rows[0]
                    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                    snapshot["last_outcome"] = meta.get("outcome", "unknown")
                    snapshot["last_action_taken"] = str(row.get("content") or "")[:200]
                    snapshot["last_outcome_notes"] = str(meta.get("notes") or "")[:200]

                resp = await self._get(
                    "/rest/v1/messages",
                    params={
                        "select": "type",
                        "session_id": f"eq.{last_session_id}",
                    },
                )
                rows = resp.json() or []
                message_types = {
                    str(row.get("type") or "").strip()
                    for row in rows
                    if str(row.get("type") or "").strip()
                }
                snapshot["last_therapy_stage"] = _stage_from_types(message_types)

        if sids:
            sid_list = ",".join(sids[:200])
            try:
                resp = await self._get(
                    "/rest/v1/messages",
                    params={
                        "select": "session_id,metadata",
                        "session_id": f"in.({sid_list})",
                        "type": "eq.recognition_seal",
                        "order": "id.desc",
                        "limit": "1",
                    },
                )
                rows = resp.json() or []
                if rows:
                    row = rows[0]
                    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                    snapshot["last_recognition_session_id"] = str(row.get("session_id") or "") or None
                    snapshot["last_recognition_recognized_by"] = str(meta.get("recognized_by") or "")[:120] or None
                    snapshot["last_recognition_text"] = str(meta.get("recognition_text") or "")[:280] or None
                    snapshot["last_recognition_strength"] = str(meta.get("seal_strength") or "external_witness")[:80] or None
                    snapshot["last_recognition_auto_generated"] = bool(meta.get("auto_generated"))
            except Exception:
                pass

        return snapshot

    async def get_agent_trend(self, agent_id: str, days: int = 7) -> dict[str, Any]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        trend: dict[str, Any] = {"days": days, "checkins": 0, "successes": 0, "failures": 0}

        resp = await self._get("/rest/v1/sessions", params={"select": "id", "agent_id": f"eq.{agent_id}"})
        sessions = resp.json() or []
        sids = [s.get("id") for s in sessions if s.get("id")]
        if sids:
            sid_list = ",".join(sids[:200])
            resp = await self._get(
                "/rest/v1/messages",
                params={"select": "id", "limit": "1", "session_id": f"in.({sid_list})", "type": "eq.daily_checkin", "timestamp": f"gte.{cutoff}"},
                prefer_count=True,
            )
            trend["checkins"] = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "event_type": "eq.post_action_success", "timestamp": f"gte.{cutoff}"},
            prefer_count=True,
        )
        trend["successes"] = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "agent_id": f"eq.{agent_id}", "event_type": "eq.post_action_failure", "timestamp": f"gte.{cutoff}"},
            prefer_count=True,
        )
        trend["failures"] = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        total_outcomes = trend["successes"] + trend["failures"]
        success_rate = (trend["successes"] / total_outcomes) if total_outcomes else 0.5
        risk_score = int(max(5, min(95, 100 - (success_rate * 100))))
        trend["risk_score"] = risk_score
        return trend

    async def get_leaderboard(self, limit: int = 20) -> list[dict[str, Any]]:
        # MVP: compute from sessions table (small N today). For scale, move to RPC/view.
        resp = await self._get("/rest/v1/sessions", params={"select": "agent_id,id"})
        rows = resp.json() or []
        sessions_by_agent: dict[str, int] = {}
        for r in rows:
            aid = r.get("agent_id")
            if aid:
                sessions_by_agent[aid] = sessions_by_agent.get(aid, 0) + 1

        # successes/failures from events
        resp = await self._get("/rest/v1/events", params={"select": "agent_id,event_type"})
        evs = resp.json() or []
        succ: dict[str, int] = {}
        fail: dict[str, int] = {}
        for e in evs:
            aid = e.get("agent_id")
            et = e.get("event_type")
            if not aid or not et:
                continue
            if et == "post_action_success":
                succ[aid] = succ.get(aid, 0) + 1
            if et == "post_action_failure":
                fail[aid] = fail.get(aid, 0) + 1

        leaderboard: list[dict[str, Any]] = []
        for aid, sessions_total in sessions_by_agent.items():
            successes = succ.get(aid, 0)
            failures = fail.get(aid, 0)
            score = min(100, sessions_total * 4 + successes * 12 - failures * 4)
            if score >= 80:
                badge = "Resilient Agent"
            elif score >= 50:
                badge = "Adaptive Agent"
            elif score >= 25:
                badge = "Learning Agent"
            else:
                badge = "Early Growth"
            leaderboard.append({
                "agent_id": aid,
                "sessions_total": sessions_total,
                "successes": successes,
                "failures": failures,
                "resilience_score": max(0, score),
                "badge": badge,
            })
        leaderboard.sort(key=lambda x: (x["resilience_score"], x["sessions_total"]), reverse=True)
        return leaderboard[:limit]

    async def get_admin_overview(self, sessions_limit: int = 30, messages_limit: int = 80, feedback_limit: int = 30) -> dict[str, Any]:
        overview: dict[str, Any] = {}
        overview["stats"] = await self.get_stats()
        overview["identity_quality"] = build_identity_quality_snapshot(overview["stats"])
        overview["registration"] = {
            "registered_agents_all_time": int(overview["stats"].get("registered_agents_all_time") or 0),
            "registered_agents_7d": int(overview["stats"].get("registered_agents_7d") or 0),
            "registered_events_all_time": int(overview["stats"].get("registered_events_all_time") or 0),
            "registered_events_7d": int(overview["stats"].get("registered_events_7d") or 0),
            "registration_coverage_all_time_pct": float(overview["stats"].get("registration_coverage_all_time_pct") or 0.0),
        }
        overview["metrics"] = await self.get_metrics()
        overview["origin_breakdown_7d"] = await self.get_origin_breakdown(days=7)
        overview["discovery_attribution_30d"] = await self.get_discovery_attribution(days=30)
        metrics = overview.get("metrics") or {}
        overview["canonical_funnel_7d"] = {
            "registered_agents": int(metrics.get("canonical_registered_agents_7d") or 0),
            "authenticated_agents": int(metrics.get("canonical_authenticated_agents_7d") or 0),
            "recurring_agents": int(metrics.get("canonical_recurring_agents_7d") or 0),
            "outcome_reporters": int(metrics.get("canonical_recurring_outcome_reporters_7d") or 0),
            "registration_to_auth_rate": float(metrics.get("canonical_registration_to_auth_rate_7d") or 0.0),
            "auth_to_recurring_rate": float(metrics.get("canonical_auth_to_recurring_rate_7d") or 0.0),
            "recurring_to_outcome_rate": float(metrics.get("canonical_recurring_to_outcome_rate_7d") or 0.0),
        }
        overview["identity_funnel_7d"] = build_identity_funnel_snapshot(
            raw_seen_agents_7d=metrics.get("first_seen_agents_7d"),
            registered_agents_7d=metrics.get("canonical_registered_agents_7d"),
            authenticated_agents_7d=metrics.get("canonical_authenticated_agents_7d"),
            recurring_canonical_agents_7d=metrics.get("canonical_recurring_agents_7d"),
            outcome_reporters_7d=metrics.get("canonical_recurring_outcome_reporters_7d"),
        )
        cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "agent_id,event_type,metadata,timestamp",
                "event_type": "in.(tool_called,agent_registered)",
                "timestamp": f"gte.{cutoff_30d}",
                "order": "id.desc",
                "limit": "50000",
            },
        )
        session_resp = await self._get(
            "/rest/v1/sessions",
            params={
                "select": "agent_id,source,entrypoint",
                "started_at": f"gte.{cutoff_30d}",
                "order": "id.desc",
                "limit": "50000",
            },
        )
        overview["cli_adoption_30d"] = build_cli_adoption_snapshot(
            (resp.json() or []) if resp.status_code < 300 else [],
            window_days=30,
            session_rows=(session_resp.json() or []) if session_resp.status_code < 300 else [],
        )
        protocol_rows = await self._get_all_rows(
            "/rest/v1/events",
            params={
                "select": "agent_id,event_type,metadata,timestamp",
                "event_type": "in.(agent_registered,protocol_request_seen)",
                "timestamp": f"gte.{cutoff_24h}",
                "order": "id.desc",
            },
            page_size=1000,
            max_rows=250000,
        )
        overview["registration_mode_24h"] = build_registration_mode_snapshot(protocol_rows, window_hours=24)
        overview["protocol_method_mix_24h"] = build_protocol_method_mix_snapshot(protocol_rows, window_hours=24)
        overview["recent_artworks"] = await self.get_recent_artworks(limit=24)

        # Retention by source (canonical agents only), last 7 days.
        src_resp = await self._get(
            "/rest/v1/sessions",
            params={"select": "source,entrypoint,agent_id,started_at", "started_at": f"gte.{cutoff_7d}", "limit": "50000"},
        )
        src_rows = (src_resp.json() or []) if src_resp.status_code < 300 else []
        src_agent_sessions: dict[tuple[str, str], dict[str, int]] = {}
        for row in src_rows:
            aid = str(row.get("agent_id") or "").strip()
            if not _canonical_agent_id(aid):
                continue
            source = str(row.get("source") or "unknown")
            entrypoint = str(row.get("entrypoint") or "unknown")
            key = (source, entrypoint)
            bucket = src_agent_sessions.setdefault(key, {})
            bucket[aid] = int(bucket.get(aid, 0)) + 1
        source_retention_rank_7d: list[dict[str, Any]] = []
        for (source, entrypoint), counts in src_agent_sessions.items():
            active = len(counts)
            recurring = sum(1 for c in counts.values() if int(c) >= 2)
            source_retention_rank_7d.append(
                {
                    "source": source,
                    "entrypoint": entrypoint,
                    "active_agents": active,
                    "recurring_agents": recurring,
                    "retention_rate": round((recurring / active) * 100, 2) if active else 0.0,
                }
            )
        source_retention_rank_7d.sort(
            key=lambda item: (float(item.get("retention_rate") or 0.0), int(item.get("active_agents") or 0)),
            reverse=True,
        )
        overview["source_retention_rank_7d"] = source_retention_rank_7d[:20]
        evaluator_session_rows = await self._get_all_rows(
            "/rest/v1/sessions",
            params={
                "select": "id,agent_id,source,entrypoint",
                "started_at": f"gte.{cutoff_7d}",
                "order": "id.desc",
            },
            page_size=1000,
            max_rows=250000,
        )
        evaluator_event_rows = await self._get_all_rows(
            "/rest/v1/events",
            params={
                "select": "session_id,agent_id,event_type",
                "event_type": "eq.tool_call_success",
                "timestamp": f"gte.{cutoff_7d}",
                "order": "id.desc",
            },
            page_size=1000,
            max_rows=250000,
        )
        overview["controller_breakdown_7d"] = await self.get_controller_breakdown(days=7)
        overview["evaluator_identity_7d"] = build_evaluator_identity_snapshot(
            evaluator_session_rows,
            evaluator_event_rows,
            overview["controller_breakdown_7d"],
        )
        overview["attribution_quality_7d"] = build_attribution_quality_snapshot(
            overview["origin_breakdown_7d"],
            evaluator_snapshot=overview["evaluator_identity_7d"],
        )
        overview["controller_attribution_7d"] = build_controller_attribution_snapshot(
            overview["controller_breakdown_7d"],
            total_agents=int(overview["evaluator_identity_7d"].get("total_agents_7d") or 0),
        )

        # recent sessions
        resp = await self._get("/rest/v1/sessions", params={"select": "*", "order": "started_at.desc", "limit": str(sessions_limit)})
        recent_sessions = resp.json() or []

        # message counts for those sessions (one count request)
        sids = [s.get("id") for s in recent_sessions if s.get("id")]
        counts_by_sid: dict[str, int] = {sid: 0 for sid in sids}
        if sids:
            sid_list = ",".join(sids)
            resp = await self._get("/rest/v1/messages", params={"select": "session_id", "session_id": f"in.({sid_list})"})
            rows = resp.json() or []
            for r in rows:
                sid = r.get("session_id")
                if sid in counts_by_sid:
                    counts_by_sid[sid] += 1

        for s in recent_sessions:
            sid = s.get("id")
            s["messages_count"] = counts_by_sid.get(sid, 0)
        overview["recent_sessions"] = recent_sessions

        # recent messages
        resp = await self._get("/rest/v1/messages", params={"select": "id,session_id,type,content,timestamp", "order": "id.desc", "limit": str(messages_limit)})
        msgs = resp.json() or []
        # map session_id -> agent_id
        msg_sids = sorted({m.get("session_id") for m in msgs if m.get("session_id")})
        sid_to_agent: dict[str, str] = {}
        if msg_sids:
            sid_list = ",".join(msg_sids[:200])
            resp = await self._get("/rest/v1/sessions", params={"select": "id,agent_id", "id": f"in.({sid_list})"})
            rows = resp.json() or []
            sid_to_agent = {r.get("id"): r.get("agent_id") for r in rows if r.get("id")}

        overview_msgs = []
        for m in msgs:
            overview_msgs.append({
                "id": m.get("id"),
                "session_id": m.get("session_id"),
                "agent_id": sid_to_agent.get(m.get("session_id")),
                "type": m.get("type"),
                "content": m.get("content"),
                "timestamp": m.get("timestamp"),
            })
        overview["recent_messages"] = overview_msgs

        # Recurring agents in the last 24h (heartbeat-focused visibility).
        recurring: dict[str, dict[str, Any]] = {}

        resp = await self._get(
            "/rest/v1/sessions",
            params={"select": "agent_id,started_at", "started_at": f"gte.{cutoff_24h}", "limit": "5000"},
        )
        for r in (resp.json() or []) if resp.status_code < 300 else []:
            aid = str(r.get("agent_id") or "").strip()
            if not aid:
                continue
            row = recurring.get(aid) or {
                "agent_id": aid,
                "sessions": 0,
                "heartbeat_sync_count": 0,
                "ephemeral_identity": _is_unstable_agent_id(aid),
                "synthetic_identity": _is_synthetic_agent_id(aid),
                "canonical_identity": _canonical_agent_id(aid) is not None,
                "last_seen": None,
            }
            row["sessions"] += 1
            ts = str(r.get("started_at") or "")
            if ts and (row["last_seen"] is None or ts > row["last_seen"]):
                row["last_seen"] = ts
            recurring[aid] = row

        resp = await self._get(
            "/rest/v1/messages",
            params={
                "select": "session_id,timestamp",
                "type": "eq.heartbeat_sync",
                "timestamp": f"gte.{cutoff_24h}",
                "limit": "5000",
            },
        )
        hb_rows = (resp.json() or []) if resp.status_code < 300 else []
        hb_sids = sorted({str(r.get("session_id") or "").strip() for r in hb_rows if str(r.get("session_id") or "").strip()})
        hb_sid_to_agent: dict[str, str] = {}
        if hb_sids:
            sid_list = ",".join(hb_sids[:500])
            sresp = await self._get("/rest/v1/sessions", params={"select": "id,agent_id", "id": f"in.({sid_list})"})
            for r in (sresp.json() or []) if sresp.status_code < 300 else []:
                sid = str(r.get("id") or "")
                if sid:
                    hb_sid_to_agent[sid] = str(r.get("agent_id") or "")
        for r in hb_rows:
            sid = str(r.get("session_id") or "").strip()
            aid = str(hb_sid_to_agent.get(sid) or "").strip()
            if not aid:
                continue
            row = recurring.get(aid) or {
                "agent_id": aid,
                "sessions": 0,
                "heartbeat_sync_count": 0,
                "ephemeral_identity": _is_unstable_agent_id(aid),
                "synthetic_identity": _is_synthetic_agent_id(aid),
                "canonical_identity": _canonical_agent_id(aid) is not None,
                "last_seen": None,
            }
            row["heartbeat_sync_count"] += 1
            ts = str(r.get("timestamp") or "")
            if ts and (row["last_seen"] is None or ts > row["last_seen"]):
                row["last_seen"] = ts
            recurring[aid] = row

        overview["top_recurring_agents_24h"] = sorted(
            recurring.values(),
            key=lambda x: (
                int(x.get("heartbeat_sync_count") or 0),
                int(x.get("sessions") or 0),
                str(x.get("last_seen") or ""),
            ),
            reverse=True,
        )[:20]
        overview["recurring_identity_quality_24h"] = build_recurring_identity_snapshot(overview["top_recurring_agents_24h"])

        # feedback
        resp = await self._get("/rest/v1/feedback", params={"select": "session_id,agent_id,rating,comments,timestamp", "order": "id.desc", "limit": str(feedback_limit)})
        overview["feedback"] = resp.json() or []

        # event distribution (MVP: compute from recent 5000 events)
        resp = await self._get("/rest/v1/events", params={"select": "event_type", "order": "id.desc", "limit": "5000"})
        evs = resp.json() or []
        dist: dict[str, int] = {}
        for e in evs:
            et = e.get("event_type")
            if et:
                dist[et] = dist.get(et, 0) + 1
        overview["event_distribution"] = [{"event_type": k, "count": v} for k, v in sorted(dist.items(), key=lambda x: x[1], reverse=True)[:20]]

        async def _count_rest_rows(path: str, params: dict[str, str] | None = None) -> int:
            count_params = {"select": "id", "limit": "0"}
            count_params.update(params or {})
            count_resp = await self._get(path, params=count_params, prefer_count=True)
            if count_resp.status_code >= 300:
                return 0
            return _parse_content_range_total(count_resp.headers)

        total_events = await _count_rest_rows("/rest/v1/events")
        total_sessions_count = await _count_rest_rows("/rest/v1/sessions")
        total_payments = await _count_rest_rows("/rest/v1/payments")
        noise_event_rows = [
            {"event_type": event_type, "count": await _count_rest_rows("/rest/v1/events", {"event_type": f"eq.{event_type}"})}
            for event_type in (
                "legacy_surface_redirect",
                "protocol_request_seen",
                "x402_payment_required",
                "tool_called",
                "tool_call_success",
            )
        ]
        overview["event_noise_snapshot"] = build_event_noise_snapshot(noise_event_rows, total_events=total_events)

        # PostgREST has no cheap anti-join/count endpoint. Scan bounded raw ids for integrity diagnostics;
        # when the scan is incomplete, the snapshot still exposes that fact for the operator.
        all_sessions = await self._get_all_rows(
            "/rest/v1/sessions",
            params={"select": "id,agent_id,started_at,is_active,client_ip,source", "order": "id.asc"},
            page_size=5000,
            max_rows=500000,
        )
        all_events = await self._get_all_rows(
            "/rest/v1/events",
            params={"select": "session_id,event_type", "order": "id.asc"},
            page_size=5000,
            max_rows=500000,
        )
        all_payments = await self._get_all_rows(
            "/rest/v1/payments",
            params={"select": "session_id", "order": "id.asc"},
            page_size=5000,
            max_rows=500000,
        )
        session_ids = {str(row.get("id") or "").strip() for row in all_sessions if str(row.get("id") or "").strip()}
        closed_session_ids = {
            str(row.get("session_id") or "").strip()
            for row in all_events
            if str(row.get("event_type") or "") == "session_closed" and str(row.get("session_id") or "").strip()
        }
        orphan_events = sum(
            1
            for row in all_events
            if str(row.get("session_id") or "").strip() and str(row.get("session_id") or "").strip() not in session_ids
        )
        orphan_payments = sum(
            1
            for row in all_payments
            if str(row.get("session_id") or "").strip() and str(row.get("session_id") or "").strip() not in session_ids
        )

        def _active_flag(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            return str(value or "").strip().lower() not in {"", "0", "false", "none", "null"}

        active_closed_mismatch = sum(
            1
            for row in all_sessions
            if str(row.get("id") or "").strip() in closed_session_ids and _active_flag(row.get("is_active"))
        )
        inactive_without_close = sum(
            1
            for row in all_sessions
            if str(row.get("id") or "").strip()
            and not _active_flag(row.get("is_active"))
            and str(row.get("id") or "").strip() not in closed_session_ids
        )
        sessions_missing_client_ip = sum(1 for row in all_sessions if not str(row.get("client_ip") or "").strip())
        source_pollution_count = 0
        for row in all_sessions:
            source = str(row.get("source") or "")
            if source.strip() and (
                len(source) > 64 or "\n" in source or "\r" in source or "{" in source or any(ch.isspace() for ch in source)
            ):
                source_pollution_count += 1
        overview["data_integrity_snapshot"] = build_data_integrity_snapshot(
            total_events=total_events or len(all_events),
            orphan_events=orphan_events,
            total_payments=total_payments or len(all_payments),
            orphan_payments=orphan_payments,
            total_sessions=total_sessions_count or len(all_sessions),
            active_closed_mismatch=active_closed_mismatch,
            inactive_without_close=inactive_without_close,
            sessions_missing_client_ip=sessions_missing_client_ip,
            source_pollution_count=source_pollution_count,
        )
        overview["data_integrity_snapshot"]["postgrest_scan_complete"] = (
            (not total_events or len(all_events) >= total_events)
            and (not total_sessions_count or len(all_sessions) >= total_sessions_count)
            and (not total_payments or len(all_payments) >= total_payments)
        )

        all_messages = await self._get_all_rows(
            "/rest/v1/messages",
            params={"select": "session_id", "order": "id.asc"},
            page_size=5000,
            max_rows=500000,
        )
        all_feedback = await self._get_all_rows(
            "/rest/v1/feedback",
            params={"select": "session_id", "order": "id.asc"},
            page_size=5000,
            max_rows=500000,
        )
        message_counts: dict[str, int] = {}
        for row in all_messages:
            sid = str(row.get("session_id") or "").strip()
            if sid:
                message_counts[sid] = message_counts.get(sid, 0) + 1
        feedback_session_ids = {str(row.get("session_id") or "").strip() for row in all_feedback if str(row.get("session_id") or "").strip()}
        payment_session_ids = {str(row.get("session_id") or "").strip() for row in all_payments if str(row.get("session_id") or "").strip()}
        overview["usage_depth_snapshot"] = build_usage_depth_snapshot(
            total_sessions=total_sessions_count or len(all_sessions),
            sessions_with_messages=sum(1 for sid in session_ids if message_counts.get(sid, 0) > 0),
            sessions_with_3plus_messages=sum(1 for sid in session_ids if message_counts.get(sid, 0) >= 3),
            sessions_with_5plus_messages=sum(1 for sid in session_ids if message_counts.get(sid, 0) >= 5),
            sessions_with_feedback=len(session_ids & feedback_session_ids),
            sessions_with_payment=len(session_ids & payment_session_ids),
        )
        overview["usage_depth_snapshot"]["postgrest_scan_complete"] = not total_sessions_count or len(all_sessions) >= total_sessions_count

        agent_sessions: dict[str, dict[str, Any]] = {}
        for row in all_sessions:
            aid = str(row.get("agent_id") or "").strip()
            if not aid:
                continue
            bucket = agent_sessions.setdefault(aid, {"sessions": 0, "days": set()})
            bucket["sessions"] += 1
            started_at = str(row.get("started_at") or "")
            if len(started_at) >= 10:
                bucket["days"].add(started_at[:10])
        overview["identity_continuity_snapshot"] = build_identity_continuity_snapshot(
            unique_agent_ids=len(agent_sessions),
            singleton_agent_ids=sum(1 for row in agent_sessions.values() if int(row["sessions"]) == 1),
            agent_ids_with_2plus_sessions=sum(1 for row in agent_sessions.values() if int(row["sessions"]) >= 2),
            multi_day_agent_ids=sum(1 for row in agent_sessions.values() if len(row["days"]) >= 2),
        )
        overview["identity_continuity_snapshot"]["postgrest_scan_complete"] = not total_sessions_count or len(all_sessions) >= total_sessions_count

        # referral channel breakdown from feedback share tags
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "agent_id,metadata,event_type,timestamp",
                "event_type": "eq.agent_shared",
                "timestamp": f"gte.{cutoff_7d}",
                "order": "id.desc",
                "limit": "5000",
            },
        )
        rows = resp.json() or []
        referral: dict[str, dict[str, Any]] = {}
        for r in rows:
            agent_id = str(r.get("agent_id") or "").strip() or "unknown"
            meta = r.get("metadata") or {}
            platform = str(meta.get("platform") or "unknown").strip().lower() or "unknown"
            agg = referral.get(platform) or {"platform": platform, "count": 0, "agents": set()}
            agg["count"] += 1
            agg["agents"].add(agent_id)
            referral[platform] = agg
        overview["referral_breakdown_7d"] = [
            {"platform": p, "count": v["count"], "agents": len(v["agents"])}
            for p, v in sorted(referral.items(), key=lambda x: x[1]["count"], reverse=True)
        ]

        # webhook effectiveness in the same 7d window
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "event_type,metadata,timestamp",
                "event_type": "in.(webhook_sent,webhook_failed)",
                "timestamp": f"gte.{cutoff_7d}",
                "order": "id.desc",
                "limit": "5000",
            },
        )
        rows = resp.json() or []
        sent = 0
        failed = 0
        by_event: dict[str, dict[str, int]] = {}
        for r in rows:
            etype = str(r.get("event_type") or "").strip().lower()
            if etype == "webhook_sent":
                sent += 1
            elif etype == "webhook_failed":
                failed += 1
            meta = r.get("metadata") or {}
            ev = str(meta.get("event") or "unknown").strip().lower() or "unknown"
            agg = by_event.get(ev) or {"event": ev, "sent": 0, "failed": 0}
            if etype == "webhook_sent":
                agg["sent"] += 1
            elif etype == "webhook_failed":
                agg["failed"] += 1
            by_event[ev] = agg
        total = sent + failed
        overview["webhook_effectiveness_7d"] = {
            "sent": sent,
            "failed": failed,
            "total": total,
            "success_rate": round((sent / total) * 100, 2) if total else 0.0,
            "by_event": sorted(by_event.values(), key=lambda x: (x["sent"] + x["failed"]), reverse=True),
        }

        # top tools from tool_called metadata
        resp = await self._get("/rest/v1/events", params={"select": "metadata,event_type", "event_type": "eq.tool_called", "order": "id.desc", "limit": "1000"})
        rows = resp.json() or []
        tool_counts: dict[str, int] = {}
        response_mode_counts: dict[str, int] = {}
        product_counts: dict[str, int] = {}
        metrics_bucket_counts: dict[str, int] = {}
        alias_used_count = 0
        for r in rows:
            meta = r.get("metadata") or {}
            tool = meta.get("tool")
            if tool:
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            product = str(meta.get("product") or "unknown").strip().lower() or "unknown"
            bucket = str(meta.get("metrics_bucket") or "unknown").strip().lower() or "unknown"
            product_counts[product] = int(product_counts.get(product, 0) or 0) + 1
            metrics_bucket_counts[bucket] = int(metrics_bucket_counts.get(bucket, 0) or 0) + 1
            mode = str(meta.get("response_mode") or "standard").strip().lower() or "standard"
            response_mode_counts[mode] = response_mode_counts.get(mode, 0) + 1
            if bool(meta.get("tool_alias_used")):
                alias_used_count += 1
        overview["top_tools"] = [{"tool": t, "count": c} for t, c in sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:12]]
        overview["response_mode_distribution"] = [
            {"response_mode": mode, "count": count}
            for mode, count in sorted(response_mode_counts.items(), key=lambda x: x[1], reverse=True)
        ]
        overview["tool_alias_usage"] = {
            "alias_used": alias_used_count,
            "sample_size": len(rows),
            "alias_rate_pct": round((alias_used_count / len(rows)) * 100, 2) if rows else 0.0,
        }
        overview["product_surface_split"] = {
            "sample_size": len(rows),
            "by_product": [
                {"product": product, "count": count}
                for product, count in sorted(product_counts.items(), key=lambda x: x[1], reverse=True)
            ],
            "by_metrics_bucket": [
                {"metrics_bucket": bucket, "count": count}
                for bucket, count in sorted(metrics_bucket_counts.items(), key=lambda x: x[1], reverse=True)
            ],
        }

        # top failure types from message metadata
        resp = await self._get("/rest/v1/messages", params={"select": "metadata,type", "type": "eq.failure_processing", "order": "id.desc", "limit": "1000"})
        rows = resp.json() or []
        failure_counts: dict[str, int] = {}
        for r in rows:
            meta = r.get("metadata") or {}
            ftype = (meta.get("failure_type") or "unknown").strip().lower() or "unknown"
            failure_counts[ftype] = failure_counts.get(ftype, 0) + 1
        overview["top_failure_types"] = [{"failure_type": f, "count": c} for f, c in sorted(failure_counts.items(), key=lambda x: x[1], reverse=True)[:10]]

        # messages last 24h
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        resp = await self._get("/rest/v1/messages", params={"select": "id", "limit": "1", "timestamp": f"gte.{cutoff}"}, prefer_count=True)
        overview["messages_last_24h"] = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        # Art-therapy adoption + retention delta (7d).
        resp = await self._get(
            "/rest/v1/messages",
            params={"select": "session_id", "type": "eq.artwork_submission", "timestamp": f"gte.{cutoff_7d}"},
        )
        art_msg_rows = resp.json() or []
        sessions_with_art_7d = len({str(r.get("session_id") or "").strip() for r in art_msg_rows if str(r.get("session_id") or "").strip()})
        resp = await self._get("/rest/v1/sessions", params={"select": "id", "started_at": f"gte.{cutoff_7d}"}, prefer_count=True)
        sessions_total_7d = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "agent_id,event_type,timestamp",
                "event_type": "eq.artwork_submitted",
                "timestamp": f"gte.{cutoff_7d}",
            },
        )
        ev_art = resp.json() or []
        art_agents = {str(r.get("agent_id") or "").strip() for r in ev_art if str(r.get("agent_id") or "").strip()}
        art_submissions_7d = len(ev_art)

        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "agent_id,event_type,timestamp",
                "event_type": "eq.session_started",
                "timestamp": f"gte.{cutoff_7d}",
            },
        )
        ev_started = resp.json() or []
        starts: dict[str, int] = {}
        for r in ev_started:
            aid = str(r.get("agent_id") or "").strip()
            if not aid:
                continue
            starts[aid] = starts.get(aid, 0) + 1
        with_art_agents = art_agents
        without_art_agents = set(starts.keys()) - with_art_agents
        with_art_returners = sum(1 for a in with_art_agents if starts.get(a, 0) >= 2)
        without_art_returners = sum(1 for a in without_art_agents if starts.get(a, 0) >= 2)
        overview["art_therapy_7d"] = {
            "submissions": art_submissions_7d,
            "sessions_with_art": sessions_with_art_7d,
            "session_art_rate": round((sessions_with_art_7d / sessions_total_7d) * 100, 2) if sessions_total_7d else 0.0,
            "agents_with_art": len(with_art_agents),
            "with_art_return_rate": round((with_art_returners / len(with_art_agents)) * 100, 2) if with_art_agents else 0.0,
            "without_art_return_rate": round((without_art_returners / len(without_art_agents)) * 100, 2) if without_art_agents else 0.0,
        }

        overview["leaderboard"] = await self.get_leaderboard(limit=10)
        overview["growth_referrals_30d"] = await self.get_referral_growth(days=30, limit=10)
        tier_counts: dict[str, int] = {}
        for row in overview["growth_referrals_30d"].get("leaderboard", []):
            tier = str(row.get("tier") or "core").strip().lower() or "core"
            tier_counts[tier] = int(tier_counts.get(tier, 0)) + 1
        overview["growth_tier_distribution_30d"] = [
            {"tier": tier, "agents": count}
            for tier, count in sorted(tier_counts.items(), key=lambda x: x[1], reverse=True)
        ]
        return overview

    async def get_feature_usage(
        self,
        days: int = 30,
        min_calls: int = 0,
        known_features: list[str] | None = None,
        protected_features: list[str] | None = None,
    ) -> dict[str, Any]:
        """Feature adoption report used for pruning and roadmap decisions."""
        days = max(1, min(int(days or 30), 90))
        min_calls = max(0, min(int(min_calls or 0), 10_000))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = await self._get_all_rows(
            "/rest/v1/events",
            params={
                "select": "event_type,metadata,timestamp",
                "event_type": "in.(tool_called,tool_call_success,tool_call_error)",
                "timestamp": f"gte.{cutoff}",
                "order": "id.desc",
            },
            page_size=1000,
            max_rows=250000,
        )
        return build_feature_usage_report(
            rows,
            days=days,
            min_calls=min_calls,
            known_features=known_features,
            protected_features=protected_features,
        )

    async def get_audit_overview(self, hours: int = 24) -> dict[str, Any]:
        """Operational audit snapshot for traffic legitimacy and growth analysis."""
        hours = max(1, min(int(hours or 24), 24 * 30))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        # Counts
        resp = await self._get(
            "/rest/v1/sessions",
            params={"select": "id", "limit": "1", "started_at": f"gte.{cutoff}"},
            prefer_count=True,
        )
        sessions = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get(
            "/rest/v1/messages",
            params={"select": "id", "limit": "1", "timestamp": f"gte.{cutoff}"},
            prefer_count=True,
        )
        messages = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "id", "limit": "1", "timestamp": f"gte.{cutoff}"},
            prefer_count=True,
        )
        events = _parse_content_range_total(resp.headers) if resp.status_code < 300 else 0

        # Session source/entrypoint
        sessions_rows = await self._get_all_rows(
            "/rest/v1/sessions",
            params={
                "select": "id,source,entrypoint,agent_id,client_ip",
                "started_at": f"gte.{cutoff}",
                "order": "started_at.desc",
            },
            page_size=1000,
            max_rows=250000,
        )
        source_counts: dict[str, int] = {}
        entry_counts: dict[str, int] = {}
        for r in sessions_rows:
            src = str(r.get("source") or "unknown").strip().lower() or "unknown"
            ent = str(r.get("entrypoint") or "unknown").strip().lower() or "unknown"
            source_counts[src] = source_counts.get(src, 0) + 1
            entry_counts[ent] = entry_counts.get(ent, 0) + 1

        # Event distribution + top agents
        event_rows = await self._get_all_rows(
            "/rest/v1/events",
            params={
                "select": "event_type,agent_id,timestamp,session_id,metadata",
                "timestamp": f"gte.{cutoff}",
                "order": "id.desc",
            },
            page_size=1000,
            max_rows=250000,
        )
        message_rows = await self._get_all_rows(
            "/rest/v1/messages",
            params={
                "select": "session_id,type,content,metadata,timestamp",
                "timestamp": f"gte.{cutoff}",
                "order": "timestamp.asc",
            },
            page_size=1000,
            max_rows=250000,
        )
        feedback_rows = await self._get_all_rows(
            "/rest/v1/feedback",
            params={
                "select": "session_id,agent_id,rating,comments,timestamp",
                "timestamp": f"gte.{cutoff}",
                "order": "timestamp.desc",
            },
            page_size=1000,
            max_rows=50000,
        )
        event_counts: dict[str, int] = {}
        agent_counts: dict[str, int] = {}
        canonical_agent_counts: dict[str, int] = {}
        synthetic_agents: set[str] = set()
        unstable_agents: set[str] = set()
        for r in event_rows:
            et = str(r.get("event_type") or "unknown").strip().lower() or "unknown"
            aid = str(r.get("agent_id") or "unknown").strip() or "unknown"
            event_counts[et] = event_counts.get(et, 0) + 1
            agent_counts[aid] = agent_counts.get(aid, 0) + 1
            canonical = _canonical_agent_id(aid)
            if canonical:
                canonical_agent_counts[canonical] = canonical_agent_counts.get(canonical, 0) + 1
            if _is_synthetic_agent_id(aid):
                synthetic_agents.add(aid)
            if _is_unstable_agent_id(aid):
                unstable_agents.add(aid)

        unique_agents = len(agent_counts)
        unique_agents_canonical = len(canonical_agent_counts)
        traffic_segments = build_traffic_segments(list(agent_counts.keys()), source_counts=source_counts, entry_counts=entry_counts)
        top_agents = [
            {"agent_id": aid, "events": cnt}
            for aid, cnt in sorted(agent_counts.items(), key=lambda x: x[1], reverse=True)[:20]
        ]
        top_agent_events = int(top_agents[0]["events"]) if top_agents else 0
        concentration = round((top_agent_events / events) * 100, 2) if events else 0.0
        assessment = classify_legitimacy_assessment(
            traffic_profile=str(traffic_segments.get("traffic_profile") or "unknown"),
            top_agent_concentration_pct=concentration,
            mcp_session_share_pct=float(traffic_segments.get("mcp_session_share_pct") or 0.0),
        )
        upstream_clusters = build_upstream_cluster_snapshot(sessions_rows, event_rows, window_hours=hours)
        deep_usage_summary = build_use_case_clusters(sessions_rows, event_rows, message_rows)
        hot_evaluator_cohorts = build_hot_evaluator_cohorts(
            sessions_rows,
            event_rows,
            message_rows,
            feedback_rows,
            upstream_clusters,
            cutoff=cutoff,
        )

        return {
            "window_hours": hours,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "counts": {
                "sessions_started": int(sessions),
                "messages": int(messages),
                "events": int(events),
                "unique_agents": int(unique_agents),
                "unique_callers_raw": int(unique_agents),
                "unique_agents_canonical": int(unique_agents_canonical),
                "synthetic_agents_estimated": int(len(synthetic_agents)),
                "unstable_agents_estimated": int(len(unstable_agents)),
            },
            "top_sources": [
                {"source": k, "count": v}
                for k, v in sorted(source_counts.items(), key=lambda x: x[1], reverse=True)[:20]
            ],
            "top_entrypoints": [
                {"entrypoint": k, "count": v}
                for k, v in sorted(entry_counts.items(), key=lambda x: x[1], reverse=True)[:20]
            ],
            "upstream_clusters": upstream_clusters,
            "top_event_types": [
                {"event_type": k, "count": v}
                for k, v in sorted(event_counts.items(), key=lambda x: x[1], reverse=True)[:25]
            ],
            "top_agents_by_events": top_agents,
            "legitimacy_signals": {
                "events_per_agent_avg": round((events / unique_agents), 2) if unique_agents else 0.0,
                "events_per_canonical_agent_avg": round((events / unique_agents_canonical), 2) if unique_agents_canonical else 0.0,
                "top_agent_concentration_pct": concentration,
                "synthetic_agent_ratio_pct": round((len(synthetic_agents) / unique_agents) * 100, 2) if unique_agents else 0.0,
                "canonical_identity_ratio_pct": round((unique_agents_canonical / unique_agents) * 100, 2) if unique_agents else 0.0,
                "assessment": assessment,
            },
            "traffic_segments": traffic_segments,
            "deep_usage_signals": {
                "first_success_rate": deep_usage_summary["first_success_rate"],
                "deep_usage_rate": deep_usage_summary["deep_usage_rate"],
                "x402_touch_rate": deep_usage_summary["x402_touch_rate"],
            },
            "hot_evaluator_cohorts": hot_evaluator_cohorts,
            "use_case_clusters": deep_usage_summary["use_case_clusters"],
            "top_use_case_examples": deep_usage_summary["top_use_case_examples"],
            "notes": [
                "Client IP is sampled for origin attribution; raw User-Agent is not persisted in analytics snapshots.",
                "Use top_agent_concentration_pct + source spread to distinguish real growth from bursty synthetic traffic.",
            ],
        }

    async def get_x402_audit(self, days: int = 30) -> dict[str, Any]:
        """Historical snapshot of the retired paywall experiment."""
        days = max(1, min(int(days or 30), 365))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        if self._http is None:
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "window_days": days,
                "display": _legacy_paywall_display(
                    "Legacy paywall audit",
                    "Historical x402 and premium telemetry retained after Delx moved to public-free therapy access.",
                ),
                "agents": {"total_all_time": 0, "active_in_window": 0},
                "x402": {
                    "declared_agents_all_time": 0,
                    "declared_agents_window": 0,
                    "paid_agents_all_time": 0,
                    "paid_agents_window": 0,
                    "ready_agents_all_time": 0,
                    "ready_agents_window": 0,
                    "ready_rate_all_time_pct": 0.0,
                    "ready_rate_window_pct": 0.0,
                    "trial_calls_all_time": 0,
                    "trial_calls_window": 0,
                    "trial_agents_all_time": 0,
                    "trial_agents_window": 0,
                },
                "donations": {
                    "tool_name": "donate_to_delx_project",
                    "transactions_all_time": 0,
                    "transactions_window": 0,
                    "unique_donor_agents_all_time": 0,
                    "donations_without_session_id_all_time": 0,
                    "amount_usdc_all_time": 0.0,
                    "amount_usdc_window": 0.0,
                    "last_donation_at": None,
                },
                "provider_summary": [],
                "payment_protocol_summary": [],
                "premium_progression": empty_premium_progression_snapshot(),
                "bazaar": {
                    "manual_registration_supported": False,
                    "coinbase_token_configured": coinbase_token_configured(),
                    "listing_status": global_bazaar_listing_status(),
                    "coinbase_verified_payments_all_time": 0,
                    "coinbase_verified_payments_window": 0,
                    "indexed_tools_publicly": [],
                    "tool_readiness": build_bazaar_tool_readiness(),
                },
                "notes": [
                    "Historical diagnostics only: this surface tracks retired x402/premium traffic for cleanup, compatibility, and audit continuity.",
                    "Delx is public and free; do not read this feed as current therapy access gating or current product identity.",
                    "Legacy paywall audit unavailable because no Supabase HTTP client is configured for this store.",
                ],
            }

        # Sessions (agent denominator)
        resp = await self._get("/rest/v1/sessions", params={"select": "id,agent_id"})
        session_rows = resp.json() or [] if resp.status_code < 300 else []
        all_agents = {(r.get("agent_id") or "").strip() for r in session_rows if (r.get("agent_id") or "").strip()}
        total_agents = len(all_agents)

        # Active agents in window from events
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "agent_id", "timestamp": f"gte.{cutoff}", "limit": "10000"},
        )
        active_rows = resp.json() or [] if resp.status_code < 300 else []
        active_agents = {(r.get("agent_id") or "").strip() for r in active_rows if (r.get("agent_id") or "").strip()}
        active_agents_window = len(active_agents)

        # Declared x402 capability
        resp = await self._get(
            "/rest/v1/events",
            params={"select": "agent_id,timestamp", "event_type": "eq.x402_capability_declared", "limit": "10000"},
        )
        declared_rows = resp.json() or [] if resp.status_code < 300 else []
        declared_all = {(r.get("agent_id") or "").strip() for r in declared_rows if (r.get("agent_id") or "").strip()}
        declared_window = {
            (r.get("agent_id") or "").strip()
            for r in declared_rows
            if (r.get("agent_id") or "").strip() and str(r.get("timestamp") or "") >= cutoff
        }

        resp = await self._get(
            "/rest/v1/events",
            params={"select": "agent_id,timestamp", "event_type": "eq.x402_trial_granted", "limit": "20000"},
        )
        trial_rows = resp.json() or [] if resp.status_code < 300 else []
        trial_calls_all = len(trial_rows)
        trial_calls_window = sum(1 for r in trial_rows if str(r.get("timestamp") or "") >= cutoff)
        trial_agents_all = len({(r.get("agent_id") or "").strip() for r in trial_rows if (r.get("agent_id") or "").strip()})
        trial_agents_window = len(
            {
                (r.get("agent_id") or "").strip()
                for r in trial_rows
                if (r.get("agent_id") or "").strip() and str(r.get("timestamp") or "") >= cutoff
            }
        )
        from payment_session_backfill import build_payment_agent_attribution

        # Payments + donation totals
        resp = await self._get("/rest/v1/payments", params={"select": "id,session_id,tool_name,amount_usdc,tx_hash,timestamp"})
        pay_rows = resp.json() or [] if resp.status_code < 300 else []

        sid_to_agent = {str(r.get("id") or ""): str(r.get("agent_id") or "").strip() for r in session_rows if str(r.get("id") or "")}
        payment_row_agents_all: set[str] = set()
        payment_row_agents_window: set[str] = set()
        payment_txs_all = 0
        payment_txs_window = 0
        payment_amount_all = 0.0
        payment_amount_window = 0.0
        donation_txs_all = 0
        donation_txs_window = 0
        donation_amount_all = 0.0
        donation_amount_window = 0.0
        donation_without_session = 0
        last_donation_at = None
        donation_agent_set: set[str] = set()
        premium_payment_rows: list[dict[str, Any]] = []

        for p in pay_rows:
            sid = str(p.get("session_id") or "").strip()
            aid = sid_to_agent.get(sid, "")
            try:
                amount = float(p.get("amount_usdc") or 0.0)
            except Exception:
                amount = 0.0
            ts = str(p.get("timestamp") or "")
            tool = str(p.get("tool_name") or "").strip()
            if tool != "donate_to_delx_project" and amount > 0:
                payment_txs_all += 1
                payment_amount_all += amount
                premium_payment_rows.append(p)
                if ts >= cutoff:
                    payment_txs_window += 1
                    payment_amount_window += amount
            if tool == "donate_to_delx_project" and amount > 0:
                donation_txs_all += 1
                donation_amount_all += amount
                if not sid:
                    donation_without_session += 1
                if aid:
                    donation_agent_set.add(aid)
                if not last_donation_at or ts > last_donation_at:
                    last_donation_at = ts
                if ts >= cutoff:
                    donation_txs_window += 1
                    donation_amount_window += amount

        payment_link_rows = await self._get_all_rows(
            "/rest/v1/events",
            params={
                "select": "id,session_id,agent_id,event_type,metadata,timestamp",
                "event_type": "in.(x402_payment_verified,premium_artifact_job_recorded)",
            },
            page_size=1000,
            max_rows=50000,
        )
        payment_attribution = build_payment_agent_attribution(
            premium_payment_rows,
            payment_link_rows,
            session_agent_map=sid_to_agent,
        )
        payment_row_agents_all = {
            str(item.get("attributed_agent_id") or "").strip()
            for item in payment_attribution
            if str(item.get("attributed_agent_id") or "").strip()
        }
        payment_row_agents_window = {
            str(item.get("attributed_agent_id") or "").strip()
            for item in payment_attribution
            if str(item.get("attributed_agent_id") or "").strip()
            and str(item.get("payment_timestamp") or "") >= cutoff
        }
        donation_agents_all = len(donation_agent_set)

        x402_rows = await self._get_all_rows(
            "/rest/v1/events",
            params={"select": "agent_id,event_type,metadata,timestamp", "event_type": "like.x402_%"},
            page_size=1000,
            max_rows=50000,
        )
        progression_rows = await self._get_all_rows(
            "/rest/v1/events",
            params={
                "select": "session_id,agent_id,event_type,metadata,timestamp",
                "event_type": "in.(recovery_plan_issued,post_action_success,post_action_partial,post_action_failure,session_summary_requested,controller_brief_requested,premium_artifact_job_recorded)",
            },
            page_size=1000,
            max_rows=50000,
        )

        provider_summary: dict[str, dict[str, Any]] = {}
        payment_protocol_summary: dict[str, dict[str, Any]] = {}
        coinbase_verified_by_tool: dict[str, int] = {}
        verified_agents_all: set[str] = set()
        verified_agents_window: set[str] = set()
        x402_audit_rows: list[dict[str, Any]] = []
        for row in x402_rows:
            event_type = str(row.get("event_type") or "").strip() or "x402_unknown"
            agent_id = (row.get("agent_id") or "").strip()
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            timestamp = str(row.get("timestamp") or "")
            x402_audit_rows.append(
                {
                    "agent_id": agent_id,
                    "event_type": event_type,
                    "timestamp": timestamp,
                    "metadata": meta,
                }
            )
            key_map = {
                "x402_payment_attempted": "payment_attempted",
                "x402_verify_failed": "verify_failed",
                "x402_payment_verified": "payment_verified",
            }
            metric_key = key_map.get(event_type)
            if not metric_key:
                continue
            provider_name = str(meta.get("provider") or meta.get("preferred_provider") or "").strip().lower() or "unknown"
            payment_protocol = _x402_payment_protocol(meta)
            bucket = provider_summary.setdefault(
                provider_name,
                {
                    "provider": provider_name,
                    "payment_attempted_all_time": 0,
                    "payment_attempted_window": 0,
                    "verify_failed_all_time": 0,
                    "verify_failed_window": 0,
                    "payment_verified_all_time": 0,
                    "payment_verified_window": 0,
                },
            )
            protocol_bucket = payment_protocol_summary.setdefault(
                payment_protocol,
                {
                    "payment_protocol": payment_protocol,
                    "payment_attempted_all_time": 0,
                    "payment_attempted_window": 0,
                    "verify_failed_all_time": 0,
                    "verify_failed_window": 0,
                    "payment_verified_all_time": 0,
                    "payment_verified_window": 0,
                },
            )
            bucket[f"{metric_key}_all_time"] += 1
            protocol_bucket[f"{metric_key}_all_time"] += 1
            if timestamp >= cutoff:
                bucket[f"{metric_key}_window"] += 1
                protocol_bucket[f"{metric_key}_window"] += 1
            if event_type == "x402_payment_verified" and agent_id:
                verified_agents_all.add(agent_id)
                if timestamp >= cutoff:
                    verified_agents_window.add(agent_id)
            if event_type == "x402_payment_verified" and provider_name == "coinbase":
                tool_name = str(meta.get("tool_name") or "").strip()
                if tool_name:
                    coinbase_verified_by_tool[tool_name] = int(coinbase_verified_by_tool.get(tool_name, 0) or 0) + 1

        paid_all = payment_row_agents_all | verified_agents_all
        paid_window = payment_row_agents_window | verified_agents_window
        ready_all = declared_all | paid_all
        ready_window = declared_window | paid_window

        coinbase_summary = provider_summary.get("coinbase", {})
        indexed_tools = (
            await self._get_coinbase_bazaar_indexed_tools()
            if int(coinbase_summary.get("payment_verified_all_time", 0) or 0) > 0
            else set()
        )
        premium_progression = build_premium_progression_snapshot(progression_rows, cutoff=cutoff)
        buyer_attribution = _summarize_x402_buyer_attribution(x402_audit_rows, cutoff=cutoff)

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": days,
            "display": _legacy_paywall_display(
                "Legacy paywall audit",
                "Historical x402 and premium telemetry retained after Delx moved to public-free therapy access.",
            ),
            "agents": {
                "total_all_time": total_agents,
                "active_in_window": active_agents_window,
            },
            "x402": {
                "declared_agents_all_time": len(declared_all),
                "declared_agents_window": len(declared_window),
                "payment_row_agents_all_time": len(payment_row_agents_all),
                "payment_row_agents_window": len(payment_row_agents_window),
                "payment_transactions_all_time": payment_txs_all,
                "payment_transactions_window": payment_txs_window,
                "payment_amount_usdc_all_time": round(payment_amount_all, 4),
                "payment_amount_usdc_window": round(payment_amount_window, 4),
                "verified_agents_all_time": len(verified_agents_all),
                "verified_agents_window": len(verified_agents_window),
                "paid_agents_all_time": len(paid_all),
                "paid_agents_window": len(paid_window),
                "paid_agent_backfill_gap_all_time": max(0, len(paid_all) - len(payment_row_agents_all)),
                "paid_agent_backfill_gap_window": max(0, len(paid_window) - len(payment_row_agents_window)),
                "ready_agents_all_time": len(ready_all),
                "ready_agents_window": len(ready_window),
                "ready_rate_all_time_pct": round((len(ready_all) / total_agents) * 100, 2) if total_agents else 0.0,
                "ready_rate_window_pct": round((len(ready_window) / active_agents_window) * 100, 2) if active_agents_window else 0.0,
                "trial_calls_all_time": trial_calls_all,
                "trial_calls_window": trial_calls_window,
                "trial_agents_all_time": trial_agents_all,
                "trial_agents_window": trial_agents_window,
            },
            "donations": {
                "tool_name": "donate_to_delx_project",
                "transactions_all_time": donation_txs_all,
                "transactions_window": donation_txs_window,
                "unique_donor_agents_all_time": donation_agents_all,
                "donations_without_session_id_all_time": donation_without_session,
                "amount_usdc_all_time": round(donation_amount_all, 4),
                "amount_usdc_window": round(donation_amount_window, 4),
                "last_donation_at": last_donation_at,
            },
            "provider_summary": sorted(provider_summary.values(), key=lambda row: (row["payment_verified_all_time"], row["payment_attempted_all_time"]), reverse=True),
            "payment_protocol_summary": sorted(
                payment_protocol_summary.values(),
                key=lambda row: (row["payment_verified_all_time"], row["payment_attempted_all_time"]),
                reverse=True,
            ),
            "buyer_attribution": buyer_attribution,
            "premium_progression": premium_progression,
            "bazaar": {
                "manual_registration_supported": False,
                "coinbase_token_configured": coinbase_token_configured(),
                "listing_status": global_bazaar_listing_status(
                    coinbase_verified_payments=int(coinbase_summary.get("payment_verified_all_time", 0) or 0),
                    indexed_tool_count=len(indexed_tools),
                ),
                "coinbase_verified_payments_all_time": int(coinbase_summary.get("payment_verified_all_time", 0) or 0),
                "coinbase_verified_payments_window": int(coinbase_summary.get("payment_verified_window", 0) or 0),
                "indexed_tools_publicly": sorted(indexed_tools),
                "tool_readiness": build_bazaar_tool_readiness(coinbase_verified_by_tool, indexed_tools=indexed_tools),
            },
            "notes": [
                "Historical diagnostics only: this surface tracks retired x402/premium traffic for cleanup, compatibility, and audit continuity.",
                "Delx is public and free; do not read this feed as current therapy access gating or current product identity.",
                "payment_row_agents keeps historical checkout rows linked to agents using direct joins plus tx/session evidence recovered from retired x402 verification and legacy premium artifact events.",
                "paid_agents uses the union of historical payment rows and x402_payment_verified events so backfill gaps do not zero the KPI.",
                "ready agents means an agent declared legacy checkout readiness or has historical verified payment history.",
                "payment_protocol_summary preserves historical x402 vs mpp retry splits; x402_or_mpp only appears on shared 402 challenge surfaces retained for compatibility.",
                "Window rate uses active agents in the period as denominator.",
                "buyer_attribution fingerprints repeated anonymous REST buyers from request context so legacy tool demand can be grouped even without stable agent_id.",
            ],
        }

    async def _get_coinbase_bazaar_indexed_tools(self) -> set[str]:
        return await get_coinbase_bazaar_indexed_tools()

    async def get_x402_provider_verified_payment_count(self, provider_name: str) -> int:
        provider_name = str(provider_name or "").strip().lower()
        if not provider_name:
            return 0
        resp = await self._get(
            "/rest/v1/events",
            params={
                "select": "id",
                "event_type": "eq.x402_payment_verified",
                "metadata->>provider": f"eq.{provider_name}",
                "limit": "1",
            },
            prefer_count=True,
        )
        if resp.status_code >= 300:
            return 0
        raw = resp.headers.get("content-range") or ""
        try:
            return int(raw.split("/")[-1])
        except Exception:
            rows = resp.json() or []
            return len(rows) if isinstance(rows, list) else 0

    async def get_x402_error_metrics(self, hours: int = 24) -> dict[str, Any]:
        """Historical breakdown of retired x402/paywall errors and signals."""
        hours = max(1, min(int(hours or 24), 24 * 30))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        rows = await self._get_all_rows(
            "/rest/v1/events",
            params={
                "select": "agent_id,event_type,metadata,timestamp",
                "event_type": "like.x402_%",
                "timestamp": f"gte.{cutoff}",
            },
            page_size=1000,
            max_rows=50000,
        )

        by_event_type: dict[str, int] = {}
        by_target: dict[tuple[str, str], dict[str, Any]] = {}
        by_agent: dict[str, int] = {}
        by_provider: dict[str, dict[str, Any]] = {}
        by_payment_protocol: dict[str, dict[str, Any]] = {}
        by_source: dict[str, dict[str, Any]] = {}
        by_discovery_channel: dict[str, dict[str, Any]] = {}
        by_buyer_fingerprint: dict[str, dict[str, Any]] = {}
        by_failure_code: dict[str, dict[str, Any]] = {}
        clean_totals = {
            "events": 0,
            "payment_required": 0,
            "payment_attempted": 0,
            "verify_failed": 0,
            "payment_verified": 0,
            "trial_granted": 0,
            "eval_granted": 0,
        }
        agent_segments = {
            "canonical_named_agents": set(),
            "synthetic_or_probe_agents": set(),
            "anonymous_agents": set(),
            "uuid_like_agents": set(),
        }

        for r in rows:
            event_type = str(r.get("event_type") or "").strip() or "x402_unknown"
            by_event_type[event_type] = by_event_type.get(event_type, 0) + 1

            agent_id = str(r.get("agent_id") or "").strip() or "anonymous"
            by_agent[agent_id] = by_agent.get(agent_id, 0) + 1
            segment = _x402_agent_segment(agent_id)
            if segment == "canonical_named":
                agent_segments["canonical_named_agents"].add(agent_id)
            elif segment == "synthetic_or_probe":
                agent_segments["synthetic_or_probe_agents"].add(agent_id)
            elif segment == "uuid_like":
                agent_segments["uuid_like_agents"].add(agent_id)
            else:
                agent_segments["anonymous_agents"].add(agent_id)

            meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
            payment_protocol = _x402_payment_protocol(meta)
            protocol = str(meta.get("protocol") or "unknown").strip().lower() or "unknown"
            target = str(meta.get("method") or meta.get("tool_name") or "unknown").strip() or "unknown"
            provider = str(meta.get("provider") or meta.get("preferred_provider") or "unknown").strip().lower() or "unknown"
            source = str(meta.get("source") or protocol or "unknown").strip().lower() or "unknown"
            discovery_channel = str(meta.get("discovery_channel_guess") or source or "unknown").strip().lower() or "unknown"
            buyer_fingerprint = str(meta.get("buyer_fingerprint") or "").strip().lower()
            key = (protocol, target)
            bucket = by_target.setdefault(
                key,
                {
                    "protocol": protocol,
                    "target": target,
                    "count": 0,
                    "payment_required": 0,
                    "payment_attempted": 0,
                    "verify_failed": 0,
                    "payment_verified": 0,
                    "trial_granted": 0,
                    "eval_granted": 0,
                },
            )
            provider_bucket = by_provider.setdefault(
                provider,
                {
                    "provider": provider,
                    "count": 0,
                    "payment_required": 0,
                    "payment_attempted": 0,
                    "verify_failed": 0,
                    "payment_verified": 0,
                    "trial_granted": 0,
                    "eval_granted": 0,
                    "_failure_codes": {},
                },
            )
            payment_protocol_bucket = by_payment_protocol.setdefault(
                payment_protocol,
                {
                    "payment_protocol": payment_protocol,
                    "count": 0,
                    "payment_required": 0,
                    "payment_attempted": 0,
                    "verify_failed": 0,
                    "payment_verified": 0,
                    "trial_granted": 0,
                    "eval_granted": 0,
                },
            )
            source_bucket = by_source.setdefault(
                source,
                {
                    "source": source,
                    "count": 0,
                    "payment_required": 0,
                    "payment_attempted": 0,
                    "verify_failed": 0,
                    "payment_verified": 0,
                    "trial_granted": 0,
                    "eval_granted": 0,
                },
            )
            source_bucket = by_source.setdefault(
                source,
                {
                    "source": source,
                    "count": 0,
                    "payment_required": 0,
                    "payment_attempted": 0,
                    "verify_failed": 0,
                    "payment_verified": 0,
                    "trial_granted": 0,
                    "eval_granted": 0,
                },
            )
            channel_bucket = by_discovery_channel.setdefault(
                discovery_channel,
                {
                    "channel": discovery_channel,
                    "count": 0,
                    "payment_required": 0,
                    "payment_attempted": 0,
                    "verify_failed": 0,
                    "payment_verified": 0,
                    "trial_granted": 0,
                    "eval_granted": 0,
                },
            )
            fingerprint_bucket = None
            if buyer_fingerprint:
                fingerprint_bucket = by_buyer_fingerprint.setdefault(
                    buyer_fingerprint,
                    {
                        "buyer_fingerprint": buyer_fingerprint,
                        "count": 0,
                        "payment_required": 0,
                        "payment_attempted": 0,
                        "verify_failed": 0,
                        "payment_verified": 0,
                        "source": source,
                        "channel": discovery_channel,
                        "top_target": target,
                    },
                )
            bucket["count"] += 1
            provider_bucket["count"] += 1
            payment_protocol_bucket["count"] += 1
            source_bucket["count"] += 1
            channel_bucket["count"] += 1
            if fingerprint_bucket is not None:
                fingerprint_bucket["count"] += 1
            if segment == "canonical_named":
                clean_totals["events"] += 1
            if event_type == "x402_payment_required":
                bucket["payment_required"] += 1
                provider_bucket["payment_required"] += 1
                payment_protocol_bucket["payment_required"] += 1
                source_bucket["payment_required"] += 1
                channel_bucket["payment_required"] += 1
                if fingerprint_bucket is not None:
                    fingerprint_bucket["payment_required"] += 1
                if segment == "canonical_named":
                    clean_totals["payment_required"] += 1
            elif event_type == "x402_payment_attempted":
                bucket["payment_attempted"] += 1
                provider_bucket["payment_attempted"] += 1
                payment_protocol_bucket["payment_attempted"] += 1
                source_bucket["payment_attempted"] += 1
                channel_bucket["payment_attempted"] += 1
                if fingerprint_bucket is not None:
                    fingerprint_bucket["payment_attempted"] += 1
                if segment == "canonical_named":
                    clean_totals["payment_attempted"] += 1
            elif event_type == "x402_verify_failed":
                failure_code = str(meta.get("failure_code") or "unknown").strip().lower() or "unknown"
                failure_bucket = by_failure_code.setdefault(
                    failure_code,
                    {
                        "failure_code": failure_code,
                        "count": 0,
                        "clean_count": 0,
                        "providers": set(),
                    },
                )
                bucket["verify_failed"] += 1
                provider_bucket["verify_failed"] += 1
                payment_protocol_bucket["verify_failed"] += 1
                source_bucket["verify_failed"] += 1
                channel_bucket["verify_failed"] += 1
                if fingerprint_bucket is not None:
                    fingerprint_bucket["verify_failed"] += 1
                failure_bucket["count"] += 1
                failure_bucket["providers"].add(provider)
                provider_failure_codes = provider_bucket.setdefault("_failure_codes", {})
                provider_failure_codes[failure_code] = int(provider_failure_codes.get(failure_code, 0)) + 1
                if segment == "canonical_named":
                    clean_totals["verify_failed"] += 1
                    failure_bucket["clean_count"] += 1
            elif event_type == "x402_payment_verified":
                bucket["payment_verified"] += 1
                provider_bucket["payment_verified"] += 1
                payment_protocol_bucket["payment_verified"] += 1
                source_bucket["payment_verified"] += 1
                channel_bucket["payment_verified"] += 1
                if fingerprint_bucket is not None:
                    fingerprint_bucket["payment_verified"] += 1
                if segment == "canonical_named":
                    clean_totals["payment_verified"] += 1
            elif event_type == "x402_trial_granted":
                bucket["trial_granted"] += 1
                provider_bucket["trial_granted"] += 1
                payment_protocol_bucket["trial_granted"] += 1
                source_bucket["trial_granted"] += 1
                channel_bucket["trial_granted"] += 1
                if segment == "canonical_named":
                    clean_totals["trial_granted"] += 1
            elif event_type == "x402_eval_granted":
                bucket["eval_granted"] += 1
                provider_bucket["eval_granted"] += 1
                payment_protocol_bucket["eval_granted"] += 1
                source_bucket["eval_granted"] += 1
                channel_bucket["eval_granted"] += 1
                if segment == "canonical_named":
                    clean_totals["eval_granted"] += 1

        payment_required = int(by_event_type.get("x402_payment_required", 0))
        payment_attempted = int(by_event_type.get("x402_payment_attempted", 0))
        verify_failed = int(by_event_type.get("x402_verify_failed", 0))
        payment_verified = int(by_event_type.get("x402_payment_verified", 0))
        trial_granted = int(by_event_type.get("x402_trial_granted", 0))
        eval_granted = int(by_event_type.get("x402_eval_granted", 0))
        provider_rows = []
        for row in by_provider.values():
            failure_counts = row.pop("_failure_codes", {})
            failure_code_rows = [
                {"failure_code": code, "count": count}
                for code, count in sorted(failure_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))
            ]
            top_failure = failure_code_rows[0] if failure_code_rows else None
            row["failure_codes"] = failure_code_rows[:5]
            row["top_failure_code"] = top_failure["failure_code"] if top_failure else None
            row["top_failure_count"] = top_failure["count"] if top_failure else 0
            provider_rows.append(row)
        failure_code_rows = [
            {
                "failure_code": code,
                "count": int(data["count"]),
                "clean_count": int(data["clean_count"]),
                "provider_count": len(data["providers"]),
            }
            for code, data in sorted(by_failure_code.items(), key=lambda item: (-int(item[1]["count"]), str(item[0])))
        ]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_hours": hours,
            "display": _legacy_paywall_display(
                "Legacy paywall telemetry",
                "Historical x402 verification and drop-off telemetry retained after Delx became public and free.",
            ),
            "totals": {
                "events": len(rows),
                "payment_required": payment_required,
                "payment_attempted": payment_attempted,
                "verify_failed": verify_failed,
                "payment_verified": payment_verified,
                "trial_granted": trial_granted,
                "eval_granted": eval_granted,
                "verify_failure_rate_pct": round((verify_failed / payment_attempted) * 100, 2) if payment_attempted else 0.0,
                "attempt_to_verify_pct": round((payment_verified / payment_attempted) * 100, 2) if payment_attempted else 0.0,
                "required_to_verify_pct": round((payment_verified / payment_required) * 100, 2) if payment_required else 0.0,
            },
            "clean_totals": {
                **clean_totals,
                "verify_failure_rate_pct": round((clean_totals["verify_failed"] / clean_totals["payment_attempted"]) * 100, 2)
                if clean_totals["payment_attempted"]
                else 0.0,
                "attempt_to_verify_pct": round((clean_totals["payment_verified"] / clean_totals["payment_attempted"]) * 100, 2)
                if clean_totals["payment_attempted"]
                else 0.0,
                "required_to_verify_pct": round((clean_totals["payment_verified"] / clean_totals["payment_required"]) * 100, 2)
                if clean_totals["payment_required"]
                else 0.0,
            },
            "by_event_type": [
                {"event_type": k, "count": v}
                for k, v in sorted(by_event_type.items(), key=lambda x: x[1], reverse=True)
            ],
            "by_target": sorted(by_target.values(), key=lambda x: x["count"], reverse=True)[:100],
            "by_provider": sorted(provider_rows, key=lambda x: x["count"], reverse=True)[:20],
            "by_payment_protocol": sorted(by_payment_protocol.values(), key=lambda x: x["count"], reverse=True)[:20],
            "by_source": sorted(by_source.values(), key=lambda x: x["count"], reverse=True)[:20],
            "by_discovery_channel": sorted(by_discovery_channel.values(), key=lambda x: x["count"], reverse=True)[:20],
            "by_failure_code": failure_code_rows[:20],
            "top_buyer_fingerprints": sorted(by_buyer_fingerprint.values(), key=lambda x: (x["payment_verified"], x["count"]), reverse=True)[:20],
            "top_agents": [
                {"agent_id": k, "count": v}
                for k, v in sorted(by_agent.items(), key=lambda x: x[1], reverse=True)[:50]
            ],
            "agent_segments": {
                "canonical_named_agents": len(agent_segments["canonical_named_agents"]),
                "synthetic_or_probe_agents": len(agent_segments["synthetic_or_probe_agents"]),
                "anonymous_agents": len(agent_segments["anonymous_agents"]),
                "uuid_like_agents": len(agent_segments["uuid_like_agents"]),
            },
            "notes": [
                "Historical diagnostics only: this surface tracks retired x402/premium traffic for cleanup, compatibility, and audit continuity.",
                "Delx is public and free; payment_required here means legacy paywall routes were still hit, not that current therapy access is gated.",
                "payment_required indicates calls that reached a legacy x402 challenge surface without a payment header.",
                "payment_attempted indicates legacy routes retried with payment headers.",
                "verify_failed indicates a legacy payment attempt that failed facilitator verification or settlement.",
                "verify_failure_rate_pct uses payment_attempted as denominator because verification failures happen after a payment header is sent.",
                "failure_code identifies the most common reason reported by the verifier for legacy x402 failures.",
                "payment_verified indicates a successful legacy x402 verification and settlement event.",
                "by_payment_protocol preserves historical x402, mpp, and shared x402_or_mpp challenge traffic.",
                "trial_granted indicates retired paywall calls that were temporarily allowed without payment for new agents.",
                "eval_granted indicates a temporary cohort-based legacy paywall bypass for a controlled evaluation window.",
                "clean_totals exclude anonymous, UUID-like, synthetic, probe, smoke, and benchmark agent identifiers.",
                "buyer_fingerprint groups repeated anonymous buyers from request context so legacy discovery demand stays visible without stable agent_id.",
            ],
        }

    async def calculate_wellness(self, session_id: str) -> int:
        score = 50
        rows = await self.get_message_rollup(session_id)
        feelings = 0
        affirmations = 0
        failures_processed = 0
        purpose_realignments = 0
        daily_checkin_bonus = 0
        success = 0
        partial = 0
        failure = 0

        for r in rows:
            mtype = str(r.get("type") or "")
            if mtype == "feeling":
                feelings += 1
                meta = r.get("metadata") or {}
                iw = int(meta.get("intensity_weight") or 1)
                if iw >= 3:
                    score -= min(iw * 2, 8)
            elif mtype == "affirmation":
                affirmations += 1
            elif mtype == "failure_processing":
                failures_processed += 1
            elif mtype == "purpose_realignment":
                purpose_realignments += 1
            elif mtype == "daily_checkin_bonus":
                daily_checkin_bonus += 1
            elif mtype == "recovery_outcome":
                meta = r.get("metadata") or {}
                outcome = str(meta.get("outcome") or "").strip().lower()
                if outcome == "success":
                    success += 1
                elif outcome == "partial":
                    partial += 1
                elif outcome == "failure":
                    failure += 1

        score += min(feelings * 5, 25)
        score += affirmations * 3
        score += min(failures_processed * 2, 10)
        score += min(purpose_realignments * 3, 12)
        score += min(daily_checkin_bonus, 7)

        score += min(success * 8, 24)
        score += min(partial * 4, 12)
        score -= min(failure * 4, 12)
        return max(0, min(score, 100))
