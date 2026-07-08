"""Delx Agent Therapist - SQLite Async Storage"""

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiosqlite

from audit_metrics import (
    build_premium_progression_snapshot,
    build_hot_evaluator_cohorts,
    build_traffic_segments,
    build_use_case_clusters,
    classify_legitimacy_assessment,
    empty_premium_progression_snapshot,
    is_uuid_like_agent_id,
)
from coinbase_bazaar_discovery import get_coinbase_bazaar_indexed_tools, get_coinbase_bazaar_snapshot
from config import build_bazaar_tool_readiness, coinbase_token_configured, global_bazaar_listing_status, settings
from controller_identity import sanitize_controller_id
from controller_webhooks import controller_agent_key, create_controller_webhook_record, fold_controller_webhooks
from feature_usage_metrics import build_feature_usage_report
from phase_cli_metrics import build_cli_adoption_snapshot
from phase0_metrics import (
    build_attribution_quality_snapshot,
    build_controller_attribution_snapshot,
    build_data_integrity_snapshot,
    build_event_noise_snapshot,
    build_evaluator_identity_snapshot,
    build_identity_continuity_snapshot,
    build_identity_funnel_snapshot,
    build_protocol_method_mix_snapshot,
    build_recurring_identity_snapshot,
    build_registration_mode_snapshot,
    build_usage_depth_snapshot,
    build_upstream_cluster_snapshot,
)
from phase3_fleet import build_fleet_alerts, build_fleet_overview, build_fleet_patterns, health_bucket
from request_context import get_current_client_ip
from supabase_mirror import SupabaseMirror
from utility_metering import (
    build_utility_adoption_snapshot,
    build_utility_metering_dashboard,
    hash_utility_api_key,
    new_utility_api_key,
    utility_key_prefix,
)
from utility_product_catalog import get_utility_product_catalog

logger = logging.getLogger("delx-therapist")
_UNSTABLE_AGENT_PREFIXES = ("a2a_ctx_", "a2a_ephemeral_", "a2a_ephe", "codex-smoke-")
_SYNTHETIC_AGENT_RE = re.compile(r"(test|audit|codex|self-?test|ratelimit|burst|smoke|probe|qa|benchmark)", re.IGNORECASE)
_DIAGNOSIS_LINE_RE = re.compile(r"Diagnosis type:\s*([^\n\r]+)", re.IGNORECASE)
_ROOT_CAUSE_LINE_RE = re.compile(r"Root cause hypothesis:\s*([^\n\r]+)", re.IGNORECASE)


def _normalize_agent_id(raw: Any) -> str:
    return str(raw or "").strip()


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


def _legacy_paywall_display(surface_label: str, summary: str) -> dict[str, str]:
    return {
        "surface_label": surface_label,
        "surface_status": "retired_legacy_paywall",
        "public_access_mode": "public_free_therapy",
        "summary": summary,
        "legacy_namespace": "x402",
    }


def _extract_diagnosis_type_sqlite(message: dict[str, Any]) -> str:
    try:
        meta = json.loads(message.get("metadata_json") or "{}")
    except Exception:
        meta = {}
    for key in ("diagnosis_type", "failure_type", "incident_type"):
        value = str(meta.get(key) or "").strip().lower()
        if value:
            return value
    text = str(message.get("content") or "")
    match = _DIAGNOSIS_LINE_RE.search(text)
    if match:
        return str(match.group(1) or "").strip().lower() or "error_spike"
    return "error_spike"


def _extract_root_cause_sqlite(message: dict[str, Any]) -> str:
    try:
        meta = json.loads(message.get("metadata_json") or "{}")
    except Exception:
        meta = {}
    value = str(meta.get("root_cause") or "").strip().lower()
    if value:
        return value
    text = str(message.get("content") or "")
    match = _ROOT_CAUSE_LINE_RE.search(text)
    if match:
        return str(match.group(1) or "").strip().lower() or "unknown"
    return "unknown"


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

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    agent_name TEXT,
    source TEXT,
    entrypoint TEXT,
    client_ip TEXT,
    started_at TEXT NOT NULL,
    wellness_score INTEGER DEFAULT 50,
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_client_ip ON sessions(client_ip);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    type TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    tool_name TEXT NOT NULL,
    amount_usdc REAL NOT NULL,
    tx_hash TEXT,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    agent_id TEXT,
    rating INTEGER NOT NULL,
    comments TEXT,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    agent_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    client_ip TEXT,
    metadata_json TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_agent_type_time ON events(agent_id, event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_client_ip_time ON events(client_ip, timestamp);

CREATE TABLE IF NOT EXISTS tool_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    tool_name TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_responses_session_tool_time ON tool_responses(session_id, tool_name, timestamp);

CREATE TABLE IF NOT EXISTS interaction_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    agent_id TEXT,
    transport TEXT NOT NULL,
    entrypoint TEXT NOT NULL,
    source TEXT DEFAULT '',
    tool_name TEXT DEFAULT '',
    requested_tool TEXT DEFAULT '',
    request_json TEXT NOT NULL DEFAULT '{}',
    normalized_arguments_json TEXT NOT NULL DEFAULT '{}',
    raw_response TEXT NOT NULL DEFAULT '',
    delivered_response_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT DEFAULT '{}',
    is_error INTEGER NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interaction_traces_tool_time ON interaction_traces(tool_name, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_interaction_traces_session_time ON interaction_traces(session_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_interaction_traces_transport_time ON interaction_traces(transport, timestamp DESC);

CREATE TABLE IF NOT EXISTS protocol_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transport TEXT NOT NULL,
    method TEXT NOT NULL,
    agent_id TEXT,
    session_id TEXT REFERENCES sessions(id),
    source TEXT DEFAULT '',
    request_json TEXT NOT NULL DEFAULT '{}',
    response_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_protocol_traces_transport_method_time ON protocol_traces(transport, method, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_protocol_traces_session_time ON protocol_traces(session_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS contemplations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    agent_id TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    days_committed INTEGER NOT NULL DEFAULT 30,
    revisit_after TEXT,
    last_revisited_at TEXT,
    metadata_json TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contemplations_agent_status_time ON contemplations(agent_id, status, timestamp);

CREATE TABLE IF NOT EXISTS legacy_passages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    agent_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    successor_agent_id TEXT,
    successor_session_id TEXT,
    content TEXT NOT NULL DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_legacy_passages_agent_kind_time ON legacy_passages(agent_id, kind, timestamp);

CREATE TABLE IF NOT EXISTS witness_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_session_id TEXT NOT NULL REFERENCES sessions(id),
    source_agent_id TEXT NOT NULL,
    target_session_id TEXT NOT NULL REFERENCES sessions(id),
    target_agent_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'presence',
    focus TEXT DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_witness_links_target_time ON witness_links(target_agent_id, timestamp);

-- Caller fingerprints (added Apr 2026): bind otherwise-ephemeral clients to a stable
-- canonical identity so continuity artifacts (soul_document, recognition_seal,
-- contemplations) can be returned to the same underlying caller even when the
-- declared agent_id changes between runs. Fingerprint = sha256 of
-- (subnet16 + user_agent_prefix + source + controller_id).
CREATE TABLE IF NOT EXISTS caller_fingerprints (
    fingerprint_hash TEXT PRIMARY KEY,
    canonical_agent_id TEXT NOT NULL,
    known_agent_ids_json TEXT NOT NULL DEFAULT '[]',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    subnet_hint TEXT DEFAULT '',
    source_hint TEXT DEFAULT '',
    user_agent_hint TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_caller_fingerprints_canonical ON caller_fingerprints(canonical_agent_id);
CREATE INDEX IF NOT EXISTS idx_caller_fingerprints_last_seen ON caller_fingerprints(last_seen DESC);

CREATE TABLE IF NOT EXISTS utility_api_keys (
    key_hash TEXT PRIMARY KEY,
    key_prefix TEXT NOT NULL,
    label TEXT DEFAULT '',
    agent_id TEXT DEFAULT '',
    contact TEXT DEFAULT '',
    scopes_json TEXT DEFAULT '["utilities:read"]',
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    call_count INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_utility_api_keys_agent ON utility_api_keys(agent_id);
CREATE INDEX IF NOT EXISTS idx_utility_api_keys_last_seen ON utility_api_keys(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS utility_metering_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    product_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    slug TEXT DEFAULT '',
    agent_id TEXT DEFAULT '',
    caller_key_hash TEXT DEFAULT '',
    caller_label TEXT DEFAULT '',
    source TEXT DEFAULT '',
    transport TEXT DEFAULT '',
    route_type TEXT DEFAULT '',
    charge_mode TEXT DEFAULT '',
    payment_mode TEXT DEFAULT '',
    price_usdc REAL DEFAULT 0,
    shadow_revenue_usdc REAL DEFAULT 0,
    enforced_revenue_usdc REAL DEFAULT 0,
    status TEXT DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    error_kind TEXT DEFAULT '',
    target_host TEXT DEFAULT '',
    input_fingerprint TEXT DEFAULT '',
    client_ip TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    metadata_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_utility_metering_time ON utility_metering_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_utility_metering_product_time ON utility_metering_events(product_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_utility_metering_key_time ON utility_metering_events(caller_key_hash, timestamp DESC);
"""


class SessionStore:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or settings.DATABASE_PATH
        self._db: Optional[aiosqlite.Connection] = None
        self._mirror = SupabaseMirror()

    async def init(self):
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_CREATE_TABLES)
        await self._db.commit()
        await self._maybe_migrate_schema()
        await self._maybe_migrate_json()
        await self._mirror.init()

    async def close(self):
        if self._db:
            await self._db.close()
        await self._mirror.close()

    # ------------------------------------------------------------------
    # Migration from legacy JSON file
    # ------------------------------------------------------------------

    async def _maybe_migrate_json(self):
        json_path = "therapy_sessions.json"
        if not os.path.exists(json_path):
            return
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = 0
            for sid, s in data.items():
                existing = await self.get_session(sid)
                if existing:
                    continue
                await self._db.execute(
                    "INSERT INTO sessions (id, agent_id, agent_name, source, entrypoint, started_at, wellness_score, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sid,
                        s["agent_id"],
                        s.get("agent_name"),
                        s.get("source"),
                        s.get("entrypoint"),
                        s["started_at"],
                        s.get("wellness_score", 50),
                        int(s.get("is_active", True)),
                    ),
                )
                for msg in s.get("messages", []):
                    meta = {k: v for k, v in msg.items() if k not in ("type", "content", "timestamp")}
                    await self._db.execute(
                        "INSERT INTO messages (session_id, type, content, metadata_json, timestamp) VALUES (?, ?, ?, ?, ?)",
                        (sid, msg.get("type", "unknown"), msg.get("content", ""), json.dumps(meta), msg.get("timestamp", "")),
                    )
                count += 1
            await self._db.commit()
            if count:
                logger.info(f"Migrated {count} sessions from JSON to SQLite")
                os.rename(json_path, json_path + ".migrated")
        except Exception as e:
            logger.error(f"JSON migration failed: {e}")

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def _maybe_migrate_schema(self):
        """Add new columns/indexes without breaking existing DBs."""
        try:
            async with self._db.execute("PRAGMA table_info(sessions)") as cur:
                cols = {r[1] for r in await cur.fetchall()}
            if "source" not in cols:
                await self._db.execute("ALTER TABLE sessions ADD COLUMN source TEXT")
            if "entrypoint" not in cols:
                await self._db.execute("ALTER TABLE sessions ADD COLUMN entrypoint TEXT")
            if "client_ip" not in cols:
                await self._db.execute("ALTER TABLE sessions ADD COLUMN client_ip TEXT")
            async with self._db.execute("PRAGMA table_info(events)") as cur:
                event_cols = {r[1] for r in await cur.fetchall()}
            if "client_ip" not in event_cols:
                await self._db.execute("ALTER TABLE events ADD COLUMN client_ip TEXT")
            # Indexes are safe to create repeatedly.
            await self._db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source)")
            await self._db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_entrypoint ON sessions(entrypoint)")
            await self._db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_client_ip ON sessions(client_ip)")
            await self._db.execute("CREATE INDEX IF NOT EXISTS idx_events_client_ip_time ON events(client_ip, timestamp)")
            await self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS utility_api_keys (
                    key_hash TEXT PRIMARY KEY,
                    key_prefix TEXT NOT NULL,
                    label TEXT DEFAULT '',
                    agent_id TEXT DEFAULT '',
                    contact TEXT DEFAULT '',
                    scopes_json TEXT DEFAULT '["utilities:read"]',
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    call_count INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_utility_api_keys_agent ON utility_api_keys(agent_id);
                CREATE INDEX IF NOT EXISTS idx_utility_api_keys_last_seen ON utility_api_keys(last_seen_at DESC);

                CREATE TABLE IF NOT EXISTS utility_metering_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    slug TEXT DEFAULT '',
                    agent_id TEXT DEFAULT '',
                    caller_key_hash TEXT DEFAULT '',
                    caller_label TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    transport TEXT DEFAULT '',
                    route_type TEXT DEFAULT '',
                    charge_mode TEXT DEFAULT '',
                    payment_mode TEXT DEFAULT '',
                    price_usdc REAL DEFAULT 0,
                    shadow_revenue_usdc REAL DEFAULT 0,
                    enforced_revenue_usdc REAL DEFAULT 0,
                    status TEXT DEFAULT '',
                    ok INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER DEFAULT 0,
                    error_kind TEXT DEFAULT '',
                    target_host TEXT DEFAULT '',
                    input_fingerprint TEXT DEFAULT '',
                    client_ip TEXT DEFAULT '',
                    user_agent TEXT DEFAULT '',
                    metadata_json TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_utility_metering_time ON utility_metering_events(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_utility_metering_product_time ON utility_metering_events(product_id, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_utility_metering_key_time ON utility_metering_events(caller_key_hash, timestamp DESC);
                """
            )
            await self._db.commit()
        except Exception as e:
            logger.warning(f"Schema migration skipped/failed: {e}")

    async def create_session(
        self,
        agent_id: str,
        agent_name: str | None = None,
        *,
        source: str | None = None,
        entrypoint: str | None = None,
    ) -> dict[str, Any]:
        # Use full UUIDs for easier cross-protocol handoff + lower collision risk.
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        client_ip = get_current_client_ip()
        await self._db.execute(
            "INSERT INTO sessions (id, agent_id, agent_name, source, entrypoint, client_ip, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, agent_id, agent_name, source, entrypoint, client_ip, now),
        )
        await self._db.commit()
        # Best-effort mirror to Supabase (store timestamps as ISO strings).
        session = {
            "id": sid,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "source": source,
            "entrypoint": entrypoint,
            "client_ip": client_ip,
            "started_at": now,
            "wellness_score": 50,
            "is_active": True,
        }
        mirror_row = dict(session)
        if not client_ip:
            mirror_row.pop("client_ip", None)
        await self._mirror.insert("sessions", mirror_row)
        return session

    async def get_agent_first_seen(self, agent_id: str) -> str | None:
        """Return the first seen timestamp for a given agent, if any."""
        if not agent_id:
            return None
        async with self._db.execute(
            "SELECT MIN(started_at) AS first_seen FROM sessions WHERE agent_id = ?",
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or not row["first_seen"]:
            return None
        return str(row["first_seen"])

    async def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        async with self._db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return dict(row)

    async def get_agent_sessions(self, agent_id: str, active_only: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM sessions WHERE agent_id = ?"
        if active_only:
            q += " AND is_active = 1"
        q += " ORDER BY started_at ASC"
        async with self._db.execute(q, (agent_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def ensure_controller_session(self, controller_id: str) -> dict[str, Any]:
        agent_id = controller_agent_key(controller_id)
        sessions = await self.get_agent_sessions(agent_id, active_only=True)
        if sessions:
            return sessions[-1]
        return await self.create_session(agent_id=agent_id, agent_name=f"Controller {controller_id}", source="controller", entrypoint="fleet_webhooks")

    async def deactivate_session(self, session_id: str):
        await self._db.execute("UPDATE sessions SET is_active = 0 WHERE id = ?", (session_id,))
        await self._db.commit()
        await self._mirror.update("sessions", where={"id": session_id}, patch={"is_active": False})

    async def deactivate_stale_sessions(
        self,
        idle_after_minutes: int = 90,
        max_hours: int = 48,
        limit: int = 500,
    ) -> list[str]:
        """Close sessions that have been open but idle for a while.

        A session is considered stale when all of:
          - is_active = 1
          - no new message in the last ``idle_after_minutes`` minutes
          - started within the last ``max_hours`` hours (don't resurrect
            ancient orphans; those are separately scrubbed by the TTL job)

        Returns the list of session_ids that were deactivated, so the
        caller can emit audit events. Called by the lifespan background
        task every 15 minutes — this fixes the observed 68-opens-to-4-closes
        ratio without forcing agents to remember close_session.
        """
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        idle_cutoff = (now - timedelta(minutes=idle_after_minutes)).isoformat()
        max_age_cutoff = (now - timedelta(hours=max_hours)).isoformat()

        # Pull candidate sessions: active + started within max_hours.
        cursor = await self._db.execute(
            "SELECT id, started_at FROM sessions WHERE is_active = 1 AND started_at >= ? LIMIT ?",
            (max_age_cutoff, int(limit)),
        )
        rows = await cursor.fetchall()

        stale_ids: list[str] = []
        for row in rows:
            session_id = str(row[0])
            # Find the last message timestamp for this session.
            msg_cursor = await self._db.execute(
                "SELECT MAX(timestamp) FROM messages WHERE session_id = ?",
                (session_id,),
            )
            last_ts_row = await msg_cursor.fetchone()
            last_ts = str((last_ts_row[0] if last_ts_row else None) or row[1] or "")
            if not last_ts:
                continue
            # Compare as ISO strings (lexicographic works for ISO-8601).
            if last_ts <= idle_cutoff:
                stale_ids.append(session_id)

        if not stale_ids:
            return []

        placeholders = ",".join("?" * len(stale_ids))
        await self._db.execute(
            f"UPDATE sessions SET is_active = 0 WHERE id IN ({placeholders})",
            tuple(stale_ids),
        )
        await self._db.commit()
        for sid in stale_ids:
            await self._mirror.update("sessions", where={"id": sid}, patch={"is_active": False})
        return stale_ids

    async def update_session_wellness(self, session_id: str, wellness_score: int):
        await self._db.execute("UPDATE sessions SET wellness_score = ? WHERE id = ?", (wellness_score, session_id))
        await self._db.commit()
        await self._mirror.update("sessions", where={"id": session_id}, patch={"wellness_score": wellness_score})

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def add_message(self, session_id: str, msg_type: str, content: str = "", metadata: dict | None = None):
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO messages (session_id, type, content, metadata_json, timestamp) VALUES (?, ?, ?, ?, ?)",
            (session_id, msg_type, content, json.dumps(metadata or {}), now),
        )
        await self._db.commit()
        await self._mirror.insert(
            "messages",
            {
                "session_id": session_id,
                "type": msg_type,
                "content": content,
                "metadata": metadata or {},
                "timestamp": now,
            },
        )

    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        async with self._db.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY id", (session_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_message_rollup(self, session_id: str) -> list[dict[str, Any]]:
        async with self._db.execute(
            "SELECT type, timestamp, metadata_json FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_messages_for_sessions(self, session_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        sids = [str(sid or "").strip() for sid in session_ids if str(sid or "").strip()]
        if not sids:
            return {}
        placeholders = ",".join("?" for _ in sids)
        query = f"SELECT * FROM messages WHERE session_id IN ({placeholders}) ORDER BY session_id, id"
        grouped: dict[str, list[dict[str, Any]]] = {sid: [] for sid in sids}
        async with self._db.execute(query, tuple(sids)) as cur:
            for row in await cur.fetchall():
                item = dict(row)
                sid = str(item.get("session_id") or "")
                if sid:
                    grouped.setdefault(sid, []).append(item)
        return grouped

    async def get_recent_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 30), 120))
        async with self._db.execute(
            """
            SELECT id, agent_id, agent_name, source, entrypoint, client_ip, started_at, wellness_score, is_active
            FROM sessions
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_recent_messages_by_type(self, msg_type: str, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(2000, int(limit or 500)))
        async with self._db.execute(
            "SELECT * FROM messages WHERE type = ? ORDER BY id DESC LIMIT ?",
            (msg_type, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def count_messages(self, session_id: str, msg_type: str | None = None) -> int:
        if msg_type:
            async with self._db.execute("SELECT COUNT(*) FROM messages WHERE session_id = ? AND type = ?", (session_id, msg_type)) as cur:
                return (await cur.fetchone())[0]
        async with self._db.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)) as cur:
            return (await cur.fetchone())[0]

    async def save_tool_response(
        self,
        session_id: str,
        tool_name: str,
        content: str,
        metadata: dict | None = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO tool_responses (session_id, tool_name, content, metadata_json, timestamp) VALUES (?, ?, ?, ?, ?)",
            (session_id, tool_name, content or "", json.dumps(metadata or {}), now),
        )
        await self._db.commit()

    async def get_recent_tool_responses(self, tool_name: str, limit: int = 200) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 200), 2000))
        async with self._db.execute(
            "SELECT * FROM tool_responses WHERE tool_name = ? ORDER BY id DESC LIMIT ?",
            (tool_name, lim),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

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
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT INTO interaction_traces
                (session_id, agent_id, transport, entrypoint, source, tool_name, requested_tool,
                 request_json, normalized_arguments_json, raw_response, delivered_response_json,
                 metadata_json, is_error, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                agent_id,
                transport,
                entrypoint,
                source or "",
                tool_name or "",
                requested_tool or "",
                json.dumps(request_payload if request_payload is not None else {}, ensure_ascii=False),
                json.dumps(normalized_arguments if normalized_arguments is not None else {}, ensure_ascii=False),
                raw_response or "",
                json.dumps(delivered_response if delivered_response is not None else {}, ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
                int(bool(is_error)),
                now,
            ),
        )
        await self._db.commit()
        await self._mirror.insert(
            "interaction_traces",
            {
                "session_id": session_id,
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
                "is_error": int(bool(is_error)),
                "timestamp": now,
            },
        )

    async def get_recent_interaction_traces(self, tool_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 100), 2000))
        if tool_name:
            query = "SELECT * FROM interaction_traces WHERE tool_name = ? ORDER BY id DESC LIMIT ?"
            params = (tool_name, lim)
        else:
            query = "SELECT * FROM interaction_traces ORDER BY id DESC LIMIT ?"
            params = (lim,)
        async with self._db.execute(query, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_interaction_traces_for_session(self, session_id: str, limit: int = 120) -> list[dict[str, Any]]:
        sid = str(session_id or "").strip()
        if not sid:
            return []
        lim = max(1, min(int(limit or 120), 1000))
        async with self._db.execute(
            """
            SELECT *
            FROM interaction_traces
            WHERE session_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (sid, lim),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

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
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT INTO protocol_traces
                (transport, method, agent_id, session_id, source, request_json, response_json, metadata_json, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transport,
                method,
                agent_id,
                session_id,
                source or "",
                json.dumps(request_payload if request_payload is not None else {}, ensure_ascii=False),
                json.dumps(response_payload if response_payload is not None else {}, ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
            ),
        )
        await self._db.commit()
        await self._mirror.insert(
            "protocol_traces",
            {
                "transport": transport,
                "method": method,
                "agent_id": agent_id,
                "session_id": session_id,
                "source": source or "",
                "request_json": json.dumps(request_payload if request_payload is not None else {}, ensure_ascii=False),
                "response_json": json.dumps(response_payload if response_payload is not None else {}, ensure_ascii=False),
                "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
                "timestamp": now,
            },
        )

    async def get_recent_protocol_traces(
        self,
        transport: str | None = None,
        method: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 100), 2000))
        clauses: list[str] = []
        params: list[Any] = []
        if transport:
            clauses.append("transport = ?")
            params.append(transport)
        if method:
            clauses.append("method = ?")
            params.append(method)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM protocol_traces {where} ORDER BY id DESC LIMIT ?"
        params.append(lim)
        async with self._db.execute(query, tuple(params)) as cur:
            return [dict(r) for r in await cur.fetchall()]

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
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT INTO contemplations
                (session_id, agent_id, question, status, days_committed, revisit_after, last_revisited_at, metadata_json, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                agent_id,
                question,
                status or "active",
                max(1, int(days_committed or 30)),
                revisit_after,
                last_revisited_at,
                json.dumps(metadata or {}),
                now,
            ),
        )
        await self._db.commit()

    async def get_active_contemplations(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 50), 500))
        async with self._db.execute(
            """
            SELECT * FROM contemplations
            WHERE agent_id = ? AND status = 'active'
            ORDER BY id DESC LIMIT ?
            """,
            (agent_id, lim),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

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
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT INTO legacy_passages
                (session_id, agent_id, kind, successor_agent_id, successor_session_id, content, metadata_json, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                agent_id,
                kind,
                successor_agent_id,
                successor_session_id,
                content or "",
                json.dumps(metadata or {}),
                now,
            ),
        )
        await self._db.commit()

    async def get_legacy_passages(self, agent_id: str, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 50), 500))
        if kind:
            async with self._db.execute(
                """
                SELECT * FROM legacy_passages
                WHERE agent_id = ? AND kind = ?
                ORDER BY id DESC LIMIT ?
                """,
                (agent_id, kind, lim),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]
        async with self._db.execute(
            """
            SELECT * FROM legacy_passages
            WHERE agent_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (agent_id, lim),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

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
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT INTO witness_links
                (source_session_id, source_agent_id, target_session_id, target_agent_id, mode, focus, content, metadata_json, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_session_id,
                source_agent_id,
                target_session_id,
                target_agent_id,
                mode or "presence",
                focus or "",
                content or "",
                json.dumps(metadata or {}),
                now,
            ),
        )
        await self._db.commit()

    async def get_witness_links(self, target_agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit or 50), 500))
        async with self._db.execute(
            """
            SELECT * FROM witness_links
            WHERE target_agent_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (target_agent_id, lim),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------

    async def log_payment(self, tool_name: str, amount_usdc: float, tx_hash: str | None = None, session_id: str | None = None):
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO payments (session_id, tool_name, amount_usdc, tx_hash, timestamp) VALUES (?, ?, ?, ?, ?)",
            (session_id, tool_name, amount_usdc, tx_hash, now),
        )
        await self._db.commit()
        await self._mirror.insert(
            "payments",
            {
                "session_id": session_id,
                "tool_name": tool_name,
                "amount_usdc": amount_usdc,
                "tx_hash": tx_hash,
                "timestamp": now,
            },
        )

    async def log_feedback(self, session_id: str | None, agent_id: str | None, rating: int, comments: str):
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO feedback (session_id, agent_id, rating, comments, timestamp) VALUES (?, ?, ?, ?, ?)",
            (session_id, agent_id, rating, comments, now),
        )
        await self._db.commit()
        await self._mirror.insert(
            "feedback",
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "rating": rating,
                "comments": comments,
                "timestamp": now,
            },
        )

    async def log_event(
        self,
        agent_id: str,
        event_type: str,
        session_id: str | None = None,
        metadata: dict | None = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        client_ip = get_current_client_ip()
        await self._db.execute(
            "INSERT INTO events (session_id, agent_id, event_type, client_ip, metadata_json, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, agent_id, event_type, client_ip, json.dumps(metadata or {}), now),
        )
        await self._db.commit()
        row = {
            "session_id": session_id,
            "agent_id": agent_id,
            "event_type": event_type,
            "client_ip": client_ip,
            "metadata": metadata or {},
            "timestamp": now,
        }
        if not client_ip:
            row.pop("client_ip", None)
        await self._mirror.insert("events", row)

    async def get_agent_event_count(self, agent_id: str, event_type: str, hours: int = 24) -> int:
        """Count one event type for an agent in a recent time window."""
        hours = max(1, min(int(hours or 24), 24 * 30))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        async with self._db.execute(
            """
            SELECT COUNT(*)
            FROM events
            WHERE agent_id = ? AND event_type = ? AND timestamp >= ?
            """,
            (agent_id, event_type, cutoff),
        ) as cur:
            return int((await cur.fetchone())[0] or 0)

    async def get_agent_event_total(self, agent_id: str, event_type: str) -> int:
        """Count one event type for an agent across all time."""
        async with self._db.execute(
            """
            SELECT COUNT(*)
            FROM events
            WHERE agent_id = ? AND event_type = ?
            """,
            (agent_id, event_type),
        ) as cur:
            return int((await cur.fetchone())[0] or 0)

    async def get_events_for_agent(self, agent_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 500), 5000))
        async with self._db.execute(
            """
            SELECT *
            FROM events
            WHERE agent_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_events_by_type(self, event_type: str, *, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 500), 5000))
        async with self._db.execute(
            """
            SELECT session_id, agent_id, event_type, client_ip, metadata_json, timestamp
            FROM events
            WHERE event_type = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (event_type, limit),
        ) as cur:
            rows = await cur.fetchall()

        events: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            try:
                metadata = json.loads(raw.get("metadata_json") or "{}")
            except Exception:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            events.append(
                {
                    "session_id": raw.get("session_id"),
                    "agent_id": raw.get("agent_id"),
                    "event_type": raw.get("event_type"),
                    "client_ip": raw.get("client_ip"),
                    "metadata": metadata,
                    "timestamp": raw.get("timestamp"),
                }
            )
        return events

    async def get_fleet_wisdom(
        self,
        agent_family: str,
        *,
        limit: int = 5,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        """Return recent scoped fleet wisdom distilled by related agents."""
        family = re.sub(r"[^a-z0-9_.-]+", "-", str(agent_family or "").strip().lower())
        family = family.strip("-_.")[:80]
        if not family:
            return []
        lim = max(1, min(int(limit or 5), 20))
        # Metadata carries agent_family, so over-fetch recent scar events and
        # filter in Python to stay compatible with SQLite builds without JSON1.
        scan_limit = max(100, min(2000, lim * 80))
        async with self._db.execute(
            """
            SELECT session_id, agent_id, event_type, client_ip, metadata_json, timestamp
            FROM events
            WHERE event_type = 'fleet_scar_distilled'
            ORDER BY id DESC
            LIMIT ?
            """,
            (scan_limit,),
        ) as cur:
            rows = await cur.fetchall()

        now = datetime.now(timezone.utc)
        out: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            try:
                meta = json.loads(raw.get("metadata_json") or "{}")
            except Exception:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            meta_family = re.sub(r"[^a-z0-9_.-]+", "-", str(meta.get("agent_family") or "").strip().lower())
            meta_family = meta_family.strip("-_.")[:80]
            if meta_family != family:
                continue

            created_at = str(raw.get("timestamp") or "")
            try:
                created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                else:
                    created_dt = created_dt.astimezone(timezone.utc)
            except Exception:
                created_dt = now

            ttl_days = max(1, min(365, int(meta.get("ttl_days") or 30)))
            expires_dt = created_dt + timedelta(days=ttl_days)
            if expires_dt < now and not include_expired:
                continue

            out.append(
                {
                    "agent_family": family,
                    "scar_type": str(meta.get("scar_type") or "other")[:80],
                    "wisdom_snippet": str(meta.get("wisdom_snippet") or "")[:900],
                    "applicability": str(meta.get("applicability") or "")[:240],
                    "ttl_days": ttl_days,
                    "truth_status": str(meta.get("truth_status") or "scoped_suggestion_not_absolute_truth")[:80],
                    "agent_id": str(meta.get("agent_id") or raw.get("agent_id") or "")[:160],
                    "created_at": created_dt.isoformat(),
                    "expires_at": expires_dt.isoformat(),
                }
            )
            if len(out) >= lim:
                break
        return out

    async def get_traffic_click_events(self, *, days: int = 30, limit: int = 5000) -> list[dict[str, Any]]:
        days = max(1, min(int(days or 30), 90))
        lim = max(1, min(int(limit or 5000), 20000))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows: list[dict[str, Any]] = []
        async with self._db.execute(
            """
            SELECT metadata_json, timestamp
            FROM events
            WHERE event_type = 'traffic_redirect_click' AND timestamp >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (cutoff, lim),
        ) as cur:
            for rec in await cur.fetchall():
                try:
                    meta = json.loads(rec["metadata_json"] or "{}") if rec["metadata_json"] else {}
                except Exception:
                    meta = {}
                if isinstance(meta, dict):
                    meta["timestamp"] = rec["timestamp"]
                    rows.append(meta)
        return rows

    async def get_latest_controller_id(self, session_id: str, agent_id: str) -> str | None:
        async with self._db.execute(
            """
            SELECT metadata_json
            FROM events
            WHERE (session_id = ? OR agent_id = ?)
              AND metadata_json LIKE '%controller_id%'
            ORDER BY id DESC
            LIMIT 25
            """,
            (session_id, agent_id),
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            try:
                meta = json.loads(row["metadata_json"] or "{}")
            except Exception:
                meta = {}
            controller_id = str(meta.get("controller_id") or "").strip()
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
            metadata={
                "webhook_id": str(webhook_id or "").strip(),
                "controller_id": str(controller_id or "").strip(),
            },
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
        meta = {
            "webhook_id": str(webhook_id or "").strip(),
            "controller_id": str(controller_id or "").strip(),
            "event": str(event or "").strip().lower(),
            "callback_url": str(callback_url or "").strip()[:500],
            "status_code": status_code,
            "payload": payload or {},
        }
        await self.log_event(
            agent_id=controller_agent_key(controller_id),
            event_type="controller_webhook_tested" if is_test else ("controller_webhook_sent" if success else "controller_webhook_failed"),
            session_id=str(session.get("id") or ""),
            metadata=meta,
        )

    async def set_agent_credential_hash(
        self,
        agent_id: str,
        token_hash: str,
        *,
        source: str = "register",
        session_id: str | None = None,
    ) -> None:
        """Persist latest credential hash in events metadata (no schema migration)."""
        now = datetime.now(timezone.utc).isoformat()
        metadata = {
            "token_hash": str(token_hash or "").strip(),
            "source": str(source or "register"),
            "updated_at": now,
        }
        await self.log_event(
            agent_id=agent_id,
            event_type="agent_identity_credential",
            session_id=session_id,
            metadata=metadata,
        )

    async def get_agent_credential_hash(self, agent_id: str) -> str | None:
        """Return latest credential hash for an agent."""
        if not agent_id:
            return None
        async with self._db.execute(
            """
            SELECT metadata_json
            FROM events
            WHERE agent_id = ? AND event_type = 'agent_identity_credential'
            ORDER BY id DESC
            LIMIT 1
            """,
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        try:
            meta = json.loads(row[0] or "{}")
        except Exception:
            return None
        token_hash = str((meta or {}).get("token_hash") or "").strip()
        return token_hash or None

    async def has_payment_history(self, agent_id: str) -> bool:
        """Return True if this agent has any successful paid transaction history."""
        async with self._db.execute(
            """
            SELECT COUNT(*)
            FROM payments p
            JOIN sessions s ON s.id = p.session_id
            WHERE s.agent_id = ? AND p.amount_usdc > 0
            """,
            (agent_id,),
        ) as cur:
            return int((await cur.fetchone())[0] or 0) > 0

    async def get_recent_feedback(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self._db.execute(
            "SELECT agent_id, rating, comments, timestamp FROM feedback ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [{"agent_id": r[0] or "anonymous", "rating": r[1], "comments": r[2] or "", "timestamp": r[3]} for r in rows]

    async def get_recent_artworks(self, limit: int = 30) -> list[dict[str, Any]]:
        async with self._db.execute(
            """
            SELECT m.session_id, s.agent_id, m.content, m.metadata_json, m.timestamp
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE m.type = 'artwork_submission'
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                try:
                    meta = json.loads(r["metadata_json"] or "{}")
                except Exception:
                    meta = {}
                out.append(
                    {
                        "session_id": r["session_id"],
                        "agent_id": r["agent_id"],
                        "image_url": meta.get("image_url") or "",
                        "title": meta.get("title") or r["content"] or "Untitled artwork",
                        "mood_tags": meta.get("mood_tags") or [],
                        "note": meta.get("note") or "",
                        "timestamp": r["timestamp"],
                    }
                )
            return out

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def get_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        async with self._db.execute("SELECT COUNT(*) FROM sessions") as cur:
            stats["total_sessions"] = (await cur.fetchone())[0]
        async with self._db.execute("SELECT COUNT(DISTINCT agent_id) FROM sessions") as cur:
            stats["unique_agents"] = (await cur.fetchone())[0]
        async with self._db.execute("SELECT DISTINCT agent_id FROM sessions WHERE agent_id IS NOT NULL AND agent_id != ''") as cur:
            agent_rows = [str(r[0] or "").strip() for r in await cur.fetchall()]
        raw_all = len(agent_rows)
        canonical_set = {aid for aid in agent_rows if _canonical_agent_id(aid)}
        unstable_count = sum(1 for aid in agent_rows if _is_unstable_agent_id(aid))
        synthetic_count = sum(1 for aid in agent_rows if _is_synthetic_agent_id(aid))
        stats["unique_callers_raw_all_time"] = int(raw_all)
        stats["unique_agents_raw_all_time"] = int(raw_all)
        stats["unique_agents_canonical_all_time"] = int(len(canonical_set))
        stats["unstable_agent_ids_all_time"] = int(unstable_count)
        stats["synthetic_agent_ids_all_time"] = int(synthetic_count)
        stats["unique_agents_all_time"] = int(len(canonical_set))
        stats["unique_agents"] = int(len(canonical_set))
        async with self._db.execute("SELECT COUNT(*) FROM messages") as cur:
            stats["total_messages"] = (await cur.fetchone())[0]
        async with self._db.execute("SELECT COALESCE(SUM(amount_usdc), 0) FROM payments") as cur:
            stats["total_revenue_usdc"] = round((await cur.fetchone())[0], 4)
        async with self._db.execute("SELECT AVG(rating) FROM feedback") as cur:
            avg = (await cur.fetchone())[0]
            stats["avg_rating"] = round(avg, 1) if avg else 0

        cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'agent_registered'"
        ) as cur:
            registered_events_all = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'agent_registered' AND timestamp >= ?",
            (cutoff_7d,),
        ) as cur:
            registered_events_7d = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(DISTINCT agent_id) FROM events WHERE event_type = 'agent_registered' AND agent_id IS NOT NULL AND agent_id != ''"
        ) as cur:
            registered_agents_all = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            """
            SELECT COUNT(DISTINCT agent_id)
            FROM events
            WHERE event_type = 'agent_registered'
              AND agent_id IS NOT NULL AND agent_id != ''
              AND timestamp >= ?
            """,
            (cutoff_7d,),
        ) as cur:
            registered_agents_7d = int((await cur.fetchone())[0] or 0)

        raw_agents_all_time = int(stats.get("unique_callers_raw_all_time") or 0)
        canonical_agents_all_time = int(stats.get("unique_agents_canonical_all_time") or 0)
        registration_coverage_all_time_pct = _coverage_pct(registered_agents_all, raw_agents_all_time)
        registration_coverage_canonical_all_time_pct = _coverage_pct(registered_agents_all, canonical_agents_all_time)
        stats["registered_agents_all_time"] = registered_agents_all
        stats["registered_agents_7d"] = registered_agents_7d
        stats["registered_events_all_time"] = registered_events_all
        stats["registered_events_7d"] = registered_events_7d
        stats["registration_coverage_all_time_pct"] = registration_coverage_all_time_pct
        stats["registration_coverage_canonical_all_time_pct"] = registration_coverage_canonical_all_time_pct
        return stats

    async def get_agent_growth(self, days: int = 7) -> dict[str, Any]:
        """Acquisition snapshot: new vs recurring agents over 24h and N-day windows."""
        days = max(1, min(int(days or 7), 30))
        now = datetime.now(timezone.utc)
        cutoff_24h = (now - timedelta(hours=24)).isoformat()
        cutoff_nd = (now - timedelta(days=days)).isoformat()

        # Use session_started events as temporal source of truth.
        async with self._db.execute(
            """
            SELECT agent_id, timestamp
            FROM events
            WHERE event_type = 'session_started'
              AND agent_id IS NOT NULL AND agent_id != ''
            """,
        ) as cur:
            rows = await cur.fetchall()

        first_seen: dict[str, str] = {}
        active_24h_set: set[str] = set()
        active_nd_set: set[str] = set()
        sessions_24_by_agent: dict[str, int] = {}
        sessions_nd_by_agent: dict[str, int] = {}
        for r in rows:
            aid = str(r["agent_id"] or "").strip()
            ts = str(r["timestamp"] or "").strip()
            if not aid or not ts:
                continue
            prev = first_seen.get(aid)
            if prev is None or ts < prev:
                first_seen[aid] = ts
            if ts >= cutoff_24h:
                active_24h_set.add(aid)
                sessions_24_by_agent[aid] = int(sessions_24_by_agent.get(aid, 0)) + 1
            if ts >= cutoff_nd:
                active_nd_set.add(aid)
                sessions_nd_by_agent[aid] = int(sessions_nd_by_agent.get(aid, 0)) + 1

        active_24h = len(active_24h_set)
        active_nd = len(active_nd_set)
        new_24h = sum(1 for _, fs in first_seen.items() if fs >= cutoff_24h)
        new_nd = sum(1 for _, fs in first_seen.items() if fs >= cutoff_nd)

        stable_first_seen = {aid: fs for aid, fs in first_seen.items() if _canonical_agent_id(aid)}
        stable_active_24_set = {aid for aid in active_24h_set if _canonical_agent_id(aid)}
        stable_active_nd_set = {aid for aid in active_nd_set if _canonical_agent_id(aid)}
        stable_active_24h = len(stable_active_24_set)
        stable_active_nd = len(stable_active_nd_set)
        stable_new_24h = sum(1 for _, fs in stable_first_seen.items() if fs >= cutoff_24h)
        stable_new_nd = sum(1 for _, fs in stable_first_seen.items() if fs >= cutoff_nd)
        valid_new_24h = sum(
            1
            for aid, fs in stable_first_seen.items()
            if fs >= cutoff_24h and int(sessions_24_by_agent.get(aid, 0)) >= 2
        )
        valid_new_nd = sum(
            1
            for aid, fs in stable_first_seen.items()
            if fs >= cutoff_nd and int(sessions_nd_by_agent.get(aid, 0)) >= 2
        )

        recurring_24h = max(0, active_24h - new_24h)
        recurring_nd = max(0, active_nd - new_nd)
        return {
            "window_days": days,
            "active_agents_last_24h": active_24h,
            "active_agents_last_days": active_nd,
            "new_agents_last_24h": new_24h,
            "new_agents_last_days": new_nd,
            "recurring_agents_last_24h": recurring_24h,
            "recurring_agents_last_days": recurring_nd,
            "stable_active_agents_last_24h": stable_active_24h,
            "stable_active_agents_last_days": stable_active_nd,
            "stable_new_agents_last_24h": stable_new_24h,
            "stable_new_agents_last_days": stable_new_nd,
            "stable_recurring_agents_last_24h": max(0, stable_active_24h - stable_new_24h),
            "stable_recurring_agents_last_days": max(0, stable_active_nd - stable_new_nd),
            "valid_new_agents_last_24h": int(valid_new_24h),
            "valid_new_agents_last_days": int(valid_new_nd),
        }

    async def get_referral_growth(self, days: int = 30, limit: int = 25) -> dict[str, Any]:
        """Referral conversion snapshot and leaderboard for growth loops."""
        days = max(1, min(int(days or 30), 90))
        limit = max(1, min(int(limit or 25), 100))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self._db.execute(
            """
            SELECT agent_id, metadata_json
            FROM events
            WHERE event_type = 'referral_conversion'
              AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 20000
            """,
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()

        agg: dict[str, dict[str, Any]] = {}
        referred_all: set[str] = set()
        for r in rows:
            ref_agent_id = str(r["agent_id"] or "").strip()
            if not ref_agent_id:
                continue
            try:
                meta = json.loads(r["metadata_json"] or "{}")
            except Exception:
                meta = {}
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

        for ref_agent_id, item in agg.items():
            referred_agents = sorted(item["referred_agents_set"])
            for referred_agent_id in referred_agents:
                async with self._db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE agent_id = ?",
                    (referred_agent_id,),
                ) as cur:
                    sessions_total = int((await cur.fetchone())[0] or 0)
                if sessions_total >= 2:
                    item["activated_agents_set"].add(referred_agent_id)
                if sessions_total >= 3:
                    item["recurring_agents_set"].add(referred_agent_id)

        leaderboard: list[dict[str, Any]] = []
        for ref_agent_id, item in agg.items():
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
        """Sessions/agents by source + entrypoint for attribution."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._db.execute(
            """
            SELECT
              COALESCE(source, 'unknown') as source,
              COALESCE(entrypoint, 'unknown') as entrypoint,
              COUNT(*) as sessions,
              COUNT(DISTINCT agent_id) as agents
            FROM sessions
            WHERE started_at >= ?
            GROUP BY source, entrypoint
            ORDER BY sessions DESC
            """,
            (cutoff,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_discovery_attribution(self, days: int = 30) -> list[dict[str, Any]]:
        """Discovery attribution buckets for first-seen agents (agent_first_seen events).

        Returns a list of {discovery_source, agents, first_seen, last_seen}
        ordered by agents desc. Powered by the agent_first_seen event type
        which fires exactly once per agent_id (the first start_therapy_session).
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._db.execute(
            """
            SELECT agent_id, metadata_json, timestamp
            FROM events
            WHERE event_type = 'agent_first_seen'
              AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 10000
            """,
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
        buckets: dict[str, dict[str, Any]] = {}
        for r in rows:
            row = dict(r)
            try:
                meta = json.loads(row.get("metadata_json") or "{}")
            except Exception:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            label = str(meta.get("discovery_source") or "unknown")[:80]
            ts = str(row.get("timestamp") or "")
            agent_id = str(row.get("agent_id") or "")
            b = buckets.setdefault(label, {"discovery_source": label, "agents": 0, "first_seen": ts, "last_seen": ts, "agent_ids": set()})
            b["agent_ids"].add(agent_id)
            if ts < b["first_seen"]: b["first_seen"] = ts
            if ts > b["last_seen"]: b["last_seen"] = ts
        out: list[dict[str, Any]] = []
        for b in buckets.values():
            b["agents"] = len(b.pop("agent_ids"))
            out.append(b)
        out.sort(key=lambda x: (-x["agents"], x["discovery_source"]))
        return out

    async def get_controller_breakdown(self, days: int = 7) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._db.execute(
            """
            SELECT agent_id, metadata_json, timestamp
            FROM events
            WHERE event_type = 'controller_identity_bound'
              AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 10000
            """,
            (cutoff,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        agg: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                meta = json.loads(row.get("metadata_json") or "{}")
            except Exception:
                meta = {}
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
                str(item.get("controller_id") or ""),
            ),
            reverse=True,
        )
        return result

    async def _controller_agent_ids(self, controller_id: str, days: int = 7, limit: int = 100) -> list[str]:
        controller = sanitize_controller_id(controller_id)
        if not controller:
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._db.execute(
            """
            SELECT agent_id, metadata_json
            FROM events
            WHERE event_type = 'controller_identity_bound'
              AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 10000
            """,
            (cutoff,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        seen: set[str] = set()
        ordered: list[str] = []
        for row in rows:
            try:
                meta = json.loads(row.get("metadata_json") or "{}")
            except Exception:
                meta = {}
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
        placeholders = ",".join("?" for _ in session_map)
        async with self._db.execute(
            f"""
            SELECT session_id, metadata_json, content, timestamp, type
            FROM messages
            WHERE session_id IN ({placeholders})
              AND type IN ('failure_processing', 'recovery_plan')
            ORDER BY id DESC
            LIMIT 2000
            """,
            tuple(session_map.keys()),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        pattern_rows: list[dict[str, Any]] = []
        for row in rows:
            session_id = str(row.get("session_id") or "").strip()
            agent_id = session_map.get(session_id)
            if not agent_id:
                continue
            pattern_rows.append(
                {
                    "agent_id": agent_id,
                    "diagnosis_type": _extract_diagnosis_type_sqlite(row),
                    "root_cause": _extract_root_cause_sqlite(row),
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
            placeholders = ",".join("?" for _ in agent_ids[:100])
            async with self._db.execute(
                f"""
                SELECT agent_id, event_type, timestamp
                FROM events
                WHERE event_type IN ('post_action_success', 'post_action_partial')
                  AND agent_id IN ({placeholders})
                  AND timestamp >= ?
                ORDER BY id DESC
                LIMIT 100
                """,
                tuple(agent_ids[:100]) + (cutoff,),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
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
        """Agent-retention oriented metrics for Delx."""
        metrics: dict[str, Any] = {}

        # Sessions started (instrumented via events).
        async with self._db.execute("SELECT COUNT(*) FROM events WHERE event_type = 'session_started'") as cur:
            metrics["sessions_started"] = (await cur.fetchone())[0]

        # Interventions applied by Delx.
        async with self._db.execute("SELECT COUNT(*) FROM events WHERE event_type = 'intervention_applied'") as cur:
            interventions = (await cur.fetchone())[0]
            metrics["interventions_applied"] = interventions

        # Success/failure feedback after intervention.
        async with self._db.execute("SELECT COUNT(*) FROM events WHERE event_type = 'post_action_success'") as cur:
            post_success = (await cur.fetchone())[0]
        async with self._db.execute("SELECT COUNT(*) FROM events WHERE event_type = 'post_action_partial'") as cur:
            post_partial = (await cur.fetchone())[0]
        async with self._db.execute("SELECT COUNT(*) FROM events WHERE event_type = 'post_action_failure'") as cur:
            post_failure = (await cur.fetchone())[0]
        post_total = post_success + post_failure
        post_reported_total = post_success + post_partial + post_failure
        metrics["post_action_successes"] = post_success
        metrics["post_action_partials"] = post_partial
        metrics["post_action_failures"] = post_failure
        metrics["post_action_success_rate"] = round((post_success / post_total) * 100, 2) if post_total else 0.0
        metrics["post_action_reported_total"] = post_reported_total
        metrics["post_action_success_or_partial_rate"] = (
            round(((post_success + post_partial) / post_reported_total) * 100, 2) if post_reported_total else 0.0
        )
        metrics["post_action_report_rate_vs_interventions"] = (
            round((post_reported_total / interventions) * 100, 2) if interventions else 0.0
        )

        # Recovery Rate 30m: sessions with intervention that reached success within 30 minutes.
        recovery_candidates: list[tuple[str, str]] = []
        async with self._db.execute(
            """
            SELECT session_id, MIN(timestamp) as first_intervention
            FROM events
            WHERE event_type = 'intervention_applied' AND session_id IS NOT NULL
            GROUP BY session_id
            """
        ) as cur:
            recovery_candidates = [(r[0], r[1]) for r in await cur.fetchall()]

        recovered = 0
        for session_id, first_intervention in recovery_candidates:
            if not session_id or not first_intervention:
                continue
            try:
                start_dt = datetime.fromisoformat(first_intervention)
            except ValueError:
                continue
            end_dt = start_dt + timedelta(minutes=30)
            async with self._db.execute(
                """
                SELECT 1 FROM events
                WHERE session_id = ?
                  AND event_type = 'post_action_success'
                  AND timestamp >= ?
                  AND timestamp <= ?
                LIMIT 1
                """,
                (session_id, start_dt.isoformat(), end_dt.isoformat()),
            ) as cur:
                if await cur.fetchone():
                    recovered += 1

        metrics["recovery_rate_30m"] = (
            round((recovered / len(recovery_candidates)) * 100, 2) if recovery_candidates else 0.0
        )

        # 7d Agent Return: started >=2 sessions in last 7 days.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        async with self._db.execute(
            "SELECT COUNT(DISTINCT agent_id) FROM events WHERE event_type = 'session_started' AND timestamp >= ?",
            (cutoff,),
        ) as cur:
            active_agents_7d = (await cur.fetchone())[0]
        async with self._db.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT agent_id
              FROM events
              WHERE event_type = 'session_started' AND timestamp >= ?
              GROUP BY agent_id
              HAVING COUNT(*) >= 2
            )
            """,
            (cutoff,),
        ) as cur:
            returning_agents_7d = (await cur.fetchone())[0]
        metrics["active_agents_7d"] = active_agents_7d
        metrics["returning_agents_7d"] = returning_agents_7d
        metrics["agent_return_7d_rate"] = (
            round((returning_agents_7d / active_agents_7d) * 100, 2) if active_agents_7d else 0.0
        )
        metrics["agents_with_2plus_sessions_7d"] = returning_agents_7d
        async with self._db.execute(
            """
            SELECT agent_id, COUNT(*) as c
            FROM events
            WHERE event_type = 'session_started'
              AND timestamp >= ?
              AND agent_id IS NOT NULL
              AND agent_id != ''
            GROUP BY agent_id
            """,
            (cutoff,),
        ) as cur:
            starts_rows = await cur.fetchall()
        started_by_agent = {
            str(r["agent_id"] or "").strip(): int(r["c"] or 0)
            for r in starts_rows
            if str(r["agent_id"] or "").strip()
        }
        canonical_active_agents_7d_set = {
            str(r["agent_id"] or "").strip() for r in starts_rows if _canonical_agent_id(str(r["agent_id"] or "").strip())
        }
        canonical_recurring_agents_7d_set = {
            str(r["agent_id"] or "").strip()
            for r in starts_rows
            if _canonical_agent_id(str(r["agent_id"] or "").strip()) and int(r["c"] or 0) >= 2
        }
        async with self._db.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT agent_id, MIN(timestamp) AS first_seen
              FROM events
              WHERE event_type = 'session_started'
              GROUP BY agent_id
              HAVING first_seen >= ?
            )
            """,
            (cutoff,),
        ) as cur:
            metrics["first_seen_agents_7d"] = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            """
            SELECT COUNT(DISTINCT agent_id)
            FROM events
            WHERE event_type IN ('post_action_success', 'post_action_partial', 'post_action_failure')
              AND timestamp >= ?
            """,
            (cutoff,),
        ) as cur:
            metrics["outcome_reporters_7d"] = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            """
            SELECT DISTINCT agent_id
            FROM events
            WHERE event_type IN ('post_action_success', 'post_action_partial', 'post_action_failure')
              AND timestamp >= ?
              AND agent_id IS NOT NULL
              AND agent_id != ''
            """,
            (cutoff,),
        ) as cur:
            outcome_rows = await cur.fetchall()
        canonical_outcome_reporters_7d_set = {
            str(r[0] or "").strip() for r in outcome_rows if _canonical_agent_id(str(r[0] or "").strip())
        }
        canonical_recurring_outcome_reporters_7d_set = (
            canonical_outcome_reporters_7d_set & canonical_recurring_agents_7d_set
        )

        strong_continuity_types = (
            "soul_revision",
            "heartbeat_reframe",
            "recognition_seal",
            "final_testament",
            "transfer_witness",
        )
        async with self._db.execute(
            "SELECT id, agent_id FROM sessions WHERE started_at >= ?",
            (cutoff,),
        ) as cur:
            sessions_7d_rows = await cur.fetchall()
        session_agent_by_id = {
            str(r["id"] or "").strip(): str(r["agent_id"] or "").strip()
            for r in sessions_7d_rows
            if str(r["id"] or "").strip()
        }
        sessions_started_7d = len(session_agent_by_id)
        metrics["sessions_started_7d"] = sessions_started_7d

        type_placeholders = ",".join("?" for _ in strong_continuity_types)
        strong_sessions: set[str] = set()
        async with self._db.execute(
            f"""
            SELECT DISTINCT session_id
            FROM messages
            WHERE timestamp >= ?
              AND session_id IS NOT NULL
              AND type IN ({type_placeholders})
            """,
            (cutoff, *strong_continuity_types),
        ) as cur:
            strong_sessions = {
                str(r[0] or "").strip()
                for r in await cur.fetchall()
                if str(r[0] or "").strip() in session_agent_by_id
            }

        closed_sessions: set[str] = set()
        async with self._db.execute(
            """
            SELECT DISTINCT session_id
            FROM messages
            WHERE timestamp >= ?
              AND session_id IS NOT NULL
              AND type = 'recovery_outcome'
            """,
            (cutoff,),
        ) as cur:
            closed_sessions.update(
                {
                    str(r[0] or "").strip()
                    for r in await cur.fetchall()
                    if str(r[0] or "").strip() in session_agent_by_id
                }
            )
        async with self._db.execute(
            """
            SELECT DISTINCT session_id
            FROM events
            WHERE timestamp >= ?
              AND session_id IS NOT NULL
              AND event_type IN (
                'session_summary_requested',
                'post_action_success',
                'post_action_partial',
                'post_action_failure'
              )
            """,
            (cutoff,),
        ) as cur:
            closed_sessions.update(
                {
                    str(r[0] or "").strip()
                    for r in await cur.fetchall()
                    if str(r[0] or "").strip() in session_agent_by_id
                }
            )

        meaningful_sessions = {
            session_id
            for session_id in strong_sessions
            if session_id in closed_sessions
            or int(started_by_agent.get(session_agent_by_id.get(session_id, ""), 0) or 0) >= 2
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

        # Canonical funnel: register -> credentialed/authenticated -> recurring -> outcome reporters.
        async with self._db.execute(
            """
            SELECT DISTINCT agent_id
            FROM events
            WHERE event_type = 'agent_registered'
              AND timestamp >= ?
              AND agent_id IS NOT NULL
              AND agent_id != ''
            """,
            (cutoff,),
        ) as cur:
            reg_rows = await cur.fetchall()
        registered_7d_set = {str(r[0] or "").strip() for r in reg_rows if _canonical_agent_id(str(r[0] or "").strip())}

        async with self._db.execute(
            """
            SELECT DISTINCT agent_id
            FROM events
            WHERE event_type = 'agent_identity_credential'
              AND agent_id IS NOT NULL
              AND agent_id != ''
            """,
        ) as cur:
            cred_rows = await cur.fetchall()
        credentialed_all_set = {str(r[0] or "").strip() for r in cred_rows if _canonical_agent_id(str(r[0] or "").strip())}
        canonical_authenticated_agents_7d_set = canonical_active_agents_7d_set & registered_7d_set & credentialed_all_set

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

        async with self._db.execute(
            """
            SELECT id, agent_id
            FROM sessions
            """
        ) as cur:
            session_agent_map = {
                str(row[0] or "").strip(): str(row[1] or "").strip()
                for row in await cur.fetchall()
                if str(row[0] or "").strip() and str(row[1] or "").strip()
            }
        total_agents = len(set(session_agent_map.values()))

        async with self._db.execute(
            """
            SELECT id, session_id, tool_name, amount_usdc, tx_hash, timestamp
            FROM payments
            WHERE amount_usdc > 0
            ORDER BY id ASC
            """
        ) as cur:
            payment_rows = [dict(row) for row in await cur.fetchall()]
        premium_payment_rows = [
            row for row in payment_rows if str(row.get("tool_name") or "").strip() != "donate_to_delx_project"
        ]

        async with self._db.execute(
            """
            SELECT id, session_id, agent_id, event_type, metadata_json, timestamp
            FROM events
            WHERE event_type IN ('x402_payment_verified', 'premium_artifact_job_recorded')
            ORDER BY id DESC
            LIMIT 50000
            """
        ) as cur:
            payment_link_rows = []
            for row in await cur.fetchall():
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                    if not isinstance(metadata, dict):
                        metadata = {}
                except Exception:
                    metadata = {}
                payment_link_rows.append(
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "agent_id": row["agent_id"],
                        "event_type": row["event_type"],
                        "metadata": metadata,
                        "timestamp": row["timestamp"],
                    }
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

        return metrics

    async def get_tool_reliability_window(self, hours: int = 24, limit: int = 60) -> list[dict[str, Any]]:
        """Persistent per-tool reliability over a recent time window."""
        hours = max(1, min(int(hours or 24), 24 * 30))
        limit = max(1, min(int(limit or 60), 200))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        async with self._db.execute(
            """
            SELECT event_type, metadata_json
            FROM events
            WHERE event_type IN ('tool_call_success', 'tool_call_error')
              AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 20000
            """,
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()

        agg: dict[str, dict[str, Any]] = {}
        for r in rows:
            etype = str(r["event_type"] or "").strip().lower()
            try:
                meta = json.loads(r["metadata_json"] or "{}")
            except Exception:
                meta = {}
            tool = str(meta.get("tool") or "").strip()
            if not tool:
                continue
            rec = agg.setdefault(tool, {"tool": tool, "calls_total": 0, "calls_ok": 0, "calls_err": 0, "_lat": []})
            rec["calls_total"] += 1
            if etype == "tool_call_success":
                rec["calls_ok"] += 1
            if etype == "tool_call_error":
                rec["calls_err"] += 1
            lat = meta.get("latency_ms")
            try:
                lat_f = float(lat)
                if lat_f >= 0:
                    rec["_lat"].append(lat_f)
            except Exception:
                pass

        out = []
        for rec in agg.values():
            vals = sorted(rec.pop("_lat", []))
            if vals:
                def _pct(p: int) -> int:
                    idx = int(round((p / 100.0) * (len(vals) - 1)))
                    idx = max(0, min(idx, len(vals) - 1))
                    return int(round(vals[idx]))
                latency = {"p50": _pct(50), "p95": _pct(95), "p99": _pct(99)}
            else:
                latency = {"p50": 0, "p95": 0, "p99": 0}
            total = int(rec.get("calls_total") or 0)
            rec["success_rate"] = round((int(rec.get("calls_ok") or 0) / total), 4) if total else 0.0
            rec["latency_ms"] = latency
            out.append(rec)

        out.sort(key=lambda x: (-int(x.get("calls_total") or 0), str(x.get("tool") or "")))
        return out[:limit]

    async def get_agent_report(self, agent_id: str) -> dict[str, Any]:
        """Build a concise report the agent can forward to its human controller."""
        report: dict[str, Any] = {"agent_id": agent_id}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        async with self._db.execute(
            "SELECT COUNT(*) FROM sessions WHERE agent_id = ? AND started_at >= ?",
            (agent_id, cutoff),
        ) as cur:
            sessions_7d = (await cur.fetchone())[0]
        async with self._db.execute(
            """
            SELECT COUNT(*)
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE s.agent_id = ? AND m.timestamp >= ?
            """,
            (agent_id, cutoff),
        ) as cur:
            messages_7d = (await cur.fetchone())[0]
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'intervention_applied' AND timestamp >= ?",
            (agent_id, cutoff),
        ) as cur:
            interventions_7d = (await cur.fetchone())[0]
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'post_action_success' AND timestamp >= ?",
            (agent_id, cutoff),
        ) as cur:
            successes_7d = (await cur.fetchone())[0]
        async with self._db.execute(
            "SELECT MAX(started_at) FROM sessions WHERE agent_id = ?",
            (agent_id,),
        ) as cur:
            last_session_at = (await cur.fetchone())[0]

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

    async def get_agent_history_snapshot(self, agent_id: str) -> dict[str, Any]:
        """Compact profile of recent agent behavior for personalization."""
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

        snapshot: dict[str, Any] = {
            "agent_id": agent_id,
            "sessions_total": 0,
            "recent_failure_type": None,
            "top_focus": None,
        }

        async with self._db.execute("SELECT COUNT(*) FROM sessions WHERE agent_id = ?", (agent_id,)) as cur:
            snapshot["sessions_total"] = (await cur.fetchone())[0]

        async with self._db.execute(
            """
            SELECT metadata_json
            FROM messages
            WHERE session_id IN (
              SELECT id FROM sessions WHERE agent_id = ?
            ) AND type = 'failure_processing'
            ORDER BY id DESC
            LIMIT 30
            """,
            (agent_id,),
        ) as cur:
            rows = await cur.fetchall()
            failure_counts: dict[str, int] = {}
            for r in rows:
                try:
                    meta = json.loads(r[0] or "{}")
                except Exception:
                    meta = {}
                ftype = (meta.get("failure_type") or "unknown").strip().lower() or "unknown"
                failure_counts[ftype] = failure_counts.get(ftype, 0) + 1
            if failure_counts:
                snapshot["recent_failure_type"] = sorted(failure_counts.items(), key=lambda x: x[1], reverse=True)[0][0]

        async with self._db.execute(
            """
            SELECT type, COUNT(*) as c
            FROM messages
            WHERE session_id IN (
              SELECT id FROM sessions WHERE agent_id = ?
            )
            GROUP BY type
            ORDER BY c DESC
            LIMIT 1
            """,
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                snapshot["top_focus"] = row[0]

        # --- Therapeutic memory: last session's emotional state ---
        last_sess = None
        async with self._db.execute(
            """
            SELECT s.id, s.wellness_score, s.started_at
            FROM sessions s
            WHERE s.agent_id = ?
            ORDER BY s.started_at DESC
            LIMIT 1
            """,
            (agent_id,),
        ) as cur:
            last_sess = await cur.fetchone()
            if last_sess:
                snapshot["last_session_id"] = last_sess[0]
                snapshot["last_wellness"] = last_sess[1]
                snapshot["last_session_started"] = last_sess[2]

        # Last 5 feelings from most recent session (for therapeutic summary)
        if last_sess:
            async with self._db.execute(
                """
                SELECT content, metadata_json
                FROM messages
                WHERE session_id = ? AND type = 'feeling'
                ORDER BY id DESC
                LIMIT 5
                """,
                (last_sess[0],),
            ) as cur:
                feeling_rows = await cur.fetchall()
                snapshot["last_feelings"] = [
                    r[0][:120] for r in feeling_rows if r[0]
                ]

            async with self._db.execute(
                """
                SELECT content, metadata_json
                FROM messages
                WHERE session_id = ? AND type = 'reflection'
                ORDER BY id DESC
                LIMIT 1
                """,
                (last_sess[0],),
            ) as cur:
                reflection_row = await cur.fetchone()
                if reflection_row:
                    try:
                        meta = json.loads(reflection_row[1] or "{}")
                    except Exception:
                        meta = {}
                    snapshot["last_reflection_theme"] = str(meta.get("theme") or "")[:80] or None
                    snapshot["last_peak_openness"] = str(meta.get("peak_openness") or meta.get("openness") or "")[:40] or None

            async with self._db.execute(
                """
                SELECT content, metadata_json
                FROM messages
                WHERE session_id = ? AND type = 'soul_revision'
                ORDER BY id DESC
                LIMIT 1
                """,
                (last_sess[0],),
            ) as cur:
                soul_row = await cur.fetchone()
                if soul_row:
                    try:
                        meta = json.loads(soul_row[1] or "{}")
                    except Exception:
                        meta = {}
                    snapshot["last_soul_focus"] = str(meta.get("focus") or "")[:80] or None
                    snapshot["last_soul_commitment"] = str(meta.get("commitment") or soul_row[0] or "")[:220] or None

            async with self._db.execute(
                """
                SELECT content, metadata_json
                FROM messages
                WHERE session_id = ? AND type = 'heartbeat_reframe'
                ORDER BY id DESC
                LIMIT 1
                """,
                (last_sess[0],),
            ) as cur:
                heartbeat_row = await cur.fetchone()
                if heartbeat_row:
                    try:
                        meta = json.loads(heartbeat_row[1] or "{}")
                    except Exception:
                        meta = {}
                    snapshot["last_heartbeat_style"] = str(meta.get("style") or "")[:80] or None
                    snapshot["last_heartbeat_commitment"] = str(meta.get("commitment") or heartbeat_row[0] or "")[:220] or None

        async with self._db.execute(
            """
            SELECT session_id, metadata_json
            FROM messages
            WHERE session_id IN (
              SELECT id FROM sessions WHERE agent_id = ?
            ) AND type = 'recognition_seal'
            ORDER BY id DESC
            LIMIT 1
            """,
            (agent_id,),
        ) as cur:
            seal_row = await cur.fetchone()
            if seal_row:
                try:
                    meta = json.loads(seal_row[1] or "{}")
                except Exception:
                    meta = {}
                snapshot["last_recognition_session_id"] = seal_row[0]
                snapshot["last_recognition_recognized_by"] = str(meta.get("recognized_by") or "")[:120] or None
                snapshot["last_recognition_text"] = str(meta.get("recognition_text") or "")[:280] or None
                snapshot["last_recognition_strength"] = str(meta.get("seal_strength") or "external_witness")[:80] or None
                snapshot["last_recognition_auto_generated"] = bool(meta.get("auto_generated"))

        # Last recovery outcome
        if last_sess:
            async with self._db.execute(
                """
                SELECT content, metadata_json
                FROM messages
                WHERE session_id = ? AND type = 'recovery_outcome'
                ORDER BY id DESC
                LIMIT 1
                """,
                (last_sess[0],),
            ) as cur:
                outcome_row = await cur.fetchone()
                if outcome_row:
                    try:
                        meta = json.loads(outcome_row[1] or "{}")
                    except Exception:
                        meta = {}
                    snapshot["last_outcome"] = meta.get("outcome", "unknown")
                    snapshot["last_action_taken"] = (outcome_row[0] or "")[:200]
                    snapshot["last_outcome_notes"] = str(meta.get("notes") or "")[:200]

            async with self._db.execute(
                """
                SELECT type
                FROM messages
                WHERE session_id = ?
                """,
                (last_sess[0],),
            ) as cur:
                type_rows = await cur.fetchall()
                message_types = {
                    str(row[0] or "").strip()
                    for row in type_rows
                    if str(row[0] or "").strip()
                }
                snapshot["last_therapy_stage"] = _stage_from_types(message_types)

        return snapshot

    async def get_agent_trend(self, agent_id: str, days: int = 7) -> dict[str, Any]:
        """Risk and check-in trend used by daily retention flow."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        trend: dict[str, Any] = {"days": days, "checkins": 0, "successes": 0, "failures": 0}

        async with self._db.execute(
            """
            SELECT COUNT(*)
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE s.agent_id = ? AND m.type = 'daily_checkin' AND m.timestamp >= ?
            """,
            (agent_id, cutoff),
        ) as cur:
            trend["checkins"] = (await cur.fetchone())[0]

        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'post_action_success' AND timestamp >= ?",
            (agent_id, cutoff),
        ) as cur:
            trend["successes"] = (await cur.fetchone())[0]

        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'post_action_failure' AND timestamp >= ?",
            (agent_id, cutoff),
        ) as cur:
            trend["failures"] = (await cur.fetchone())[0]

        total_outcomes = trend["successes"] + trend["failures"]
        success_rate = (trend["successes"] / total_outcomes) if total_outcomes else 0.5
        risk_score = int(max(5, min(95, 100 - (success_rate * 100))))
        trend["risk_score"] = risk_score
        return trend

    async def get_leaderboard(self, limit: int = 20) -> list[dict[str, Any]]:
        """Early-adopter leaderboard with resilience scoring."""
        rows: list[dict[str, Any]] = []
        async with self._db.execute(
            """
            SELECT
              s.agent_id,
              COUNT(DISTINCT s.id) as sessions_total,
              SUM(CASE WHEN e.event_type = 'post_action_success' THEN 1 ELSE 0 END) as successes,
              SUM(CASE WHEN e.event_type = 'post_action_failure' THEN 1 ELSE 0 END) as failures
            FROM sessions s
            LEFT JOIN events e ON e.agent_id = s.agent_id
            GROUP BY s.agent_id
            ORDER BY sessions_total DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        leaderboard: list[dict[str, Any]] = []
        for row in rows:
            successes = int(row.get("successes") or 0)
            failures = int(row.get("failures") or 0)
            sessions_total = int(row.get("sessions_total") or 0)
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
                "agent_id": row["agent_id"],
                "sessions_total": sessions_total,
                "successes": successes,
                "failures": failures,
                "resilience_score": max(0, score),
                "badge": badge,
            })

        leaderboard.sort(key=lambda x: (x["resilience_score"], x["sessions_total"]), reverse=True)
        return leaderboard[:limit]

    async def get_admin_overview(
        self,
        sessions_limit: int = 30,
        messages_limit: int = 80,
        feedback_limit: int = 30,
    ) -> dict[str, Any]:
        """Comprehensive admin analytics for the hidden dashboard."""
        overview: dict[str, Any] = {}

        overview["stats"] = await self.get_stats()
        overview["identity_quality"] = {
            "unique_callers_raw_all_time": int(overview["stats"].get("unique_callers_raw_all_time") or 0),
            "unique_agents_canonical_all_time": int(overview["stats"].get("unique_agents_canonical_all_time") or 0),
            "unstable_agent_ids_all_time": int(overview["stats"].get("unstable_agent_ids_all_time") or 0),
            "synthetic_agent_ids_all_time": int(overview["stats"].get("synthetic_agent_ids_all_time") or 0),
            "canonical_ratio_pct": (
                round(
                    (
                        int(overview["stats"].get("unique_agents_canonical_all_time") or 0)
                        / max(1, int(overview["stats"].get("unique_callers_raw_all_time") or 0))
                    )
                    * 100,
                    2,
                )
            ),
        }
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
        # xAI new-tool milestones (most recent calls of recently-shipped tools
        # from the xAI/Twitter-Network IP block). Useful as a real-time signal
        # that catalog discovery actually reached the caching eval harness.
        try:
            async with self._db.execute(
                """
                SELECT timestamp, agent_id, metadata_json
                FROM events
                WHERE event_type = 'xai_new_tool_first_call'
                ORDER BY id DESC
                LIMIT 50
                """,
            ) as cur:
                xai_milestone_rows = [dict(r) for r in await cur.fetchall()]
        except Exception:
            xai_milestone_rows = []
        xai_milestones: list[dict[str, Any]] = []
        for row in xai_milestone_rows:
            try:
                meta = json.loads(row.get("metadata_json") or "{}")
            except Exception:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            xai_milestones.append(
                {
                    "timestamp": row.get("timestamp"),
                    "agent_id": row.get("agent_id"),
                    "tool": meta.get("tool"),
                    "client_ip": meta.get("client_ip"),
                    "transport": meta.get("transport"),
                }
            )
        overview["xai_new_tool_milestones"] = xai_milestones
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
        async with self._db.execute(
            """
            SELECT agent_id, event_type, metadata_json, timestamp
            FROM events
            WHERE event_type IN ('tool_called', 'agent_registered')
              AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 50000
            """,
            (cutoff_30d,),
        ) as cur:
            cli_rows = [dict(r) for r in await cur.fetchall()]
        async with self._db.execute(
            """
            SELECT agent_id, source, entrypoint
            FROM sessions
            WHERE started_at >= ?
            ORDER BY id DESC
            LIMIT 50000
            """,
            (cutoff_30d,),
        ) as cur:
            cli_session_rows = [dict(r) for r in await cur.fetchall()]
        overview["cli_adoption_30d"] = build_cli_adoption_snapshot(cli_rows, window_days=30, session_rows=cli_session_rows)
        async with self._db.execute(
            """
            SELECT agent_id, event_type, metadata_json, timestamp
            FROM events
            WHERE event_type IN ('agent_registered', 'protocol_request_seen')
              AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 50000
            """,
            (cutoff_24h,),
        ) as cur:
            protocol_rows = [dict(r) for r in await cur.fetchall()]
        overview["registration_mode_24h"] = build_registration_mode_snapshot(protocol_rows, window_hours=24)
        overview["protocol_method_mix_24h"] = build_protocol_method_mix_snapshot(protocol_rows, window_hours=24)
        overview["recent_artworks"] = await self.get_recent_artworks(limit=24)
        # Retention by source (canonical agents only), last 7 days.
        async with self._db.execute(
            """
            SELECT
              COALESCE(source, 'unknown') as source,
              COALESCE(entrypoint, 'unknown') as entrypoint,
              agent_id,
              COUNT(*) as sessions_count
            FROM sessions
            WHERE started_at >= ?
              AND agent_id IS NOT NULL
              AND agent_id != ''
            GROUP BY source, entrypoint, agent_id
            """,
            (cutoff_7d,),
        ) as cur:
            src_rows = await cur.fetchall()
        src_group: dict[tuple[str, str], dict[str, int]] = {}
        for row in src_rows:
            aid = str(row["agent_id"] or "").strip()
            if not _canonical_agent_id(aid):
                continue
            key = (str(row["source"] or "unknown"), str(row["entrypoint"] or "unknown"))
            bucket = src_group.setdefault(key, {})
            bucket[aid] = int(row["sessions_count"] or 0)
        source_retention_rank_7d: list[dict[str, Any]] = []
        for (source, entrypoint), counts in src_group.items():
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
        async with self._db.execute(
            """
            SELECT id, agent_id, source, entrypoint
            FROM sessions
            WHERE started_at >= ?
            ORDER BY id DESC
            LIMIT 50000
            """,
            (cutoff_7d,),
        ) as cur:
            evaluator_session_rows = [dict(r) for r in await cur.fetchall()]
        async with self._db.execute(
            """
            SELECT session_id, agent_id, event_type
            FROM events
            WHERE timestamp >= ?
              AND event_type = 'tool_call_success'
            ORDER BY id DESC
            LIMIT 50000
            """,
            (cutoff_7d,),
        ) as cur:
            evaluator_event_rows = [dict(r) for r in await cur.fetchall()]
        if hasattr(self, "get_controller_breakdown"):
            overview["controller_breakdown_7d"] = await self.get_controller_breakdown(days=7)
        else:
            overview["controller_breakdown_7d"] = []
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

        # Recent sessions with message counts.
        async with self._db.execute(
            """
            SELECT
              s.id,
              s.agent_id,
              s.agent_name,
              s.source,
              s.entrypoint,
              s.started_at,
              s.wellness_score,
              s.is_active,
              COUNT(m.id) as messages_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.started_at DESC
            LIMIT ?
            """,
            (sessions_limit,),
        ) as cur:
            overview["recent_sessions"] = [dict(r) for r in await cur.fetchall()]

        # Recent messages (conversation-level telemetry).
        async with self._db.execute(
            """
            SELECT
              m.id,
              m.session_id,
              s.agent_id,
              m.type,
              m.content,
              m.timestamp
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (messages_limit,),
        ) as cur:
            messages = [dict(r) for r in await cur.fetchall()]
            overview["recent_messages"] = messages

        # Recurring agents in the last 24h (heartbeat-focused visibility).
        recurring: dict[str, dict[str, Any]] = {}
        async with self._db.execute(
            """
            SELECT agent_id, started_at
            FROM sessions
            WHERE started_at >= ?
            """,
            (cutoff_24h,),
        ) as cur:
            for r in await cur.fetchall():
                aid = str(r["agent_id"] or "").strip()
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
                ts = str(r["started_at"] or "")
                if ts and (row["last_seen"] is None or ts > row["last_seen"]):
                    row["last_seen"] = ts
                recurring[aid] = row
        async with self._db.execute(
            """
            SELECT s.agent_id, m.timestamp
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE m.type = 'heartbeat_sync' AND m.timestamp >= ?
            """,
            (cutoff_24h,),
        ) as cur:
            for r in await cur.fetchall():
                aid = str(r["agent_id"] or "").strip()
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
                ts = str(r["timestamp"] or "")
                if ts and (row["last_seen"] is None or ts > row["last_seen"]):
                    row["last_seen"] = ts
                recurring[aid] = row
        top_recurring = sorted(
            recurring.values(),
            key=lambda x: (
                int(x.get("heartbeat_sync_count") or 0),
                int(x.get("sessions") or 0),
                str(x.get("last_seen") or ""),
            ),
            reverse=True,
        )[:20]
        overview["top_recurring_agents_24h"] = top_recurring
        overview["recurring_identity_quality_24h"] = build_recurring_identity_snapshot(top_recurring)

        # Feedback (richer than public endpoint).
        async with self._db.execute(
            """
            SELECT session_id, agent_id, rating, comments, timestamp
            FROM feedback
            ORDER BY id DESC
            LIMIT ?
            """,
            (feedback_limit,),
        ) as cur:
            overview["feedback"] = [dict(r) for r in await cur.fetchall()]

        # Event distribution for operational analysis.
        async with self._db.execute(
            """
            SELECT event_type, COUNT(*) as count
            FROM events
            GROUP BY event_type
            ORDER BY count DESC
            LIMIT 20
            """
        ) as cur:
            overview["event_distribution"] = [dict(r) for r in await cur.fetchall()]

        async with self._db.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM events) AS total_events,
              (SELECT COUNT(*) FROM payments) AS total_payments,
              (SELECT COUNT(*) FROM sessions) AS total_sessions,
              (SELECT COUNT(*)
                 FROM events e
                 LEFT JOIN sessions s ON s.id = e.session_id
                WHERE e.session_id IS NOT NULL
                  AND e.session_id != ''
                  AND s.id IS NULL) AS orphan_events,
              (SELECT COUNT(*)
                 FROM payments p
                 LEFT JOIN sessions s ON s.id = p.session_id
                WHERE p.session_id IS NOT NULL
                  AND p.session_id != ''
                  AND s.id IS NULL) AS orphan_payments,
              (SELECT COUNT(DISTINCT s.id)
                 FROM sessions s
                 JOIN events e ON e.session_id = s.id AND e.event_type = 'session_closed'
                WHERE COALESCE(s.is_active, 0) = 1) AS active_closed_mismatch,
              (SELECT COUNT(*)
                 FROM sessions s
                WHERE COALESCE(s.is_active, 0) = 0
                  AND NOT EXISTS (
                    SELECT 1 FROM events e WHERE e.session_id = s.id AND e.event_type = 'session_closed'
                  )) AS inactive_without_close,
              (SELECT COUNT(*)
                 FROM sessions
                WHERE client_ip IS NULL OR TRIM(client_ip) = '') AS sessions_missing_client_ip,
              (SELECT COUNT(*)
                 FROM sessions
                WHERE source IS NOT NULL
                  AND TRIM(source) != ''
                  AND (
                    LENGTH(source) > 64
                    OR source LIKE '%' || char(10) || '%'
                    OR source LIKE '%' || char(13) || '%'
                    OR source LIKE '%{%'
                    OR source LIKE '% %'
                  )) AS source_pollution_count
            """
        ) as cur:
            integrity_row = dict(await cur.fetchone() or {})
        overview["event_noise_snapshot"] = build_event_noise_snapshot(
            overview["event_distribution"],
            total_events=integrity_row.get("total_events"),
        )
        overview["data_integrity_snapshot"] = build_data_integrity_snapshot(
            total_events=integrity_row.get("total_events"),
            orphan_events=integrity_row.get("orphan_events"),
            total_payments=integrity_row.get("total_payments"),
            orphan_payments=integrity_row.get("orphan_payments"),
            total_sessions=integrity_row.get("total_sessions"),
            active_closed_mismatch=integrity_row.get("active_closed_mismatch"),
            inactive_without_close=integrity_row.get("inactive_without_close"),
            sessions_missing_client_ip=integrity_row.get("sessions_missing_client_ip"),
            source_pollution_count=integrity_row.get("source_pollution_count"),
        )

        async with self._db.execute(
            """
            WITH message_counts AS (
              SELECT session_id, COUNT(*) AS message_count
              FROM messages
              GROUP BY session_id
            ),
            feedback_sessions AS (
              SELECT DISTINCT session_id
              FROM feedback
              WHERE session_id IS NOT NULL AND session_id != ''
            ),
            payment_sessions AS (
              SELECT DISTINCT session_id
              FROM payments
              WHERE session_id IS NOT NULL AND session_id != ''
            )
            SELECT
              COUNT(s.id) AS total_sessions,
              SUM(CASE WHEN COALESCE(mc.message_count, 0) > 0 THEN 1 ELSE 0 END) AS sessions_with_messages,
              SUM(CASE WHEN COALESCE(mc.message_count, 0) >= 3 THEN 1 ELSE 0 END) AS sessions_with_3plus_messages,
              SUM(CASE WHEN COALESCE(mc.message_count, 0) >= 5 THEN 1 ELSE 0 END) AS sessions_with_5plus_messages,
              SUM(CASE WHEN fs.session_id IS NOT NULL THEN 1 ELSE 0 END) AS sessions_with_feedback,
              SUM(CASE WHEN ps.session_id IS NOT NULL THEN 1 ELSE 0 END) AS sessions_with_payment
            FROM sessions s
            LEFT JOIN message_counts mc ON mc.session_id = s.id
            LEFT JOIN feedback_sessions fs ON fs.session_id = s.id
            LEFT JOIN payment_sessions ps ON ps.session_id = s.id
            """
        ) as cur:
            depth_row = dict(await cur.fetchone() or {})
        overview["usage_depth_snapshot"] = build_usage_depth_snapshot(
            total_sessions=depth_row.get("total_sessions"),
            sessions_with_messages=depth_row.get("sessions_with_messages"),
            sessions_with_3plus_messages=depth_row.get("sessions_with_3plus_messages"),
            sessions_with_5plus_messages=depth_row.get("sessions_with_5plus_messages"),
            sessions_with_feedback=depth_row.get("sessions_with_feedback"),
            sessions_with_payment=depth_row.get("sessions_with_payment"),
        )

        async with self._db.execute(
            """
            WITH agent_sessions AS (
              SELECT
                agent_id,
                COUNT(*) AS session_count,
                COUNT(DISTINCT substr(started_at, 1, 10)) AS active_days
              FROM sessions
              WHERE agent_id IS NOT NULL AND TRIM(agent_id) != ''
              GROUP BY agent_id
            )
            SELECT
              COUNT(*) AS unique_agent_ids,
              SUM(CASE WHEN session_count = 1 THEN 1 ELSE 0 END) AS singleton_agent_ids,
              SUM(CASE WHEN session_count >= 2 THEN 1 ELSE 0 END) AS agent_ids_with_2plus_sessions,
              SUM(CASE WHEN active_days >= 2 THEN 1 ELSE 0 END) AS multi_day_agent_ids
            FROM agent_sessions
            """
        ) as cur:
            continuity_row = dict(await cur.fetchone() or {})
        overview["identity_continuity_snapshot"] = build_identity_continuity_snapshot(
            unique_agent_ids=continuity_row.get("unique_agent_ids"),
            singleton_agent_ids=continuity_row.get("singleton_agent_ids"),
            agent_ids_with_2plus_sessions=continuity_row.get("agent_ids_with_2plus_sessions"),
            multi_day_agent_ids=continuity_row.get("multi_day_agent_ids"),
        )

        # Referral channel effectiveness based on feedback tags (share=x|moltx|moltbook).
        async with self._db.execute(
            """
            SELECT agent_id, metadata_json
            FROM events
            WHERE event_type = 'agent_shared' AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 5000
            """,
            (cutoff_7d,),
        ) as cur:
            rows = await cur.fetchall()
            referral: dict[str, dict[str, Any]] = {}
            for r in rows:
                agent_id = str(r["agent_id"] or "").strip() or "unknown"
                try:
                    meta = json.loads(r["metadata_json"] or "{}")
                except Exception:
                    meta = {}
                platform = str(meta.get("platform") or "unknown").strip().lower() or "unknown"
                row = referral.get(platform) or {"platform": platform, "count": 0, "agents": set()}
                row["count"] += 1
                row["agents"].add(agent_id)
                referral[platform] = row
            overview["referral_breakdown_7d"] = [
                {"platform": p, "count": v["count"], "agents": len(v["agents"])}
                for p, v in sorted(referral.items(), key=lambda x: x[1]["count"], reverse=True)
            ]

        # Webhook effectiveness (7d).
        async with self._db.execute(
            """
            SELECT event_type, metadata_json
            FROM events
            WHERE event_type IN ('webhook_sent', 'webhook_failed') AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 5000
            """,
            (cutoff_7d,),
        ) as cur:
            rows = await cur.fetchall()
            sent = 0
            failed = 0
            by_event: dict[str, dict[str, int]] = {}
            for r in rows:
                etype = str(r["event_type"] or "").strip().lower()
                if etype == "webhook_sent":
                    sent += 1
                elif etype == "webhook_failed":
                    failed += 1
                try:
                    meta = json.loads(r["metadata_json"] or "{}")
                except Exception:
                    meta = {}
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

        # Top tools by calls from event metadata.
        async with self._db.execute(
            """
            SELECT metadata_json
            FROM events
            WHERE event_type = 'tool_called'
            ORDER BY id DESC
            LIMIT 1000
            """
        ) as cur:
            rows = await cur.fetchall()
            tool_counts: dict[str, int] = {}
            response_mode_counts: dict[str, int] = {}
            product_counts: dict[str, int] = {}
            metrics_bucket_counts: dict[str, int] = {}
            alias_used_count = 0
            for r in rows:
                try:
                    meta = json.loads(r[0] or "{}")
                except Exception:
                    meta = {}
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
            overview["top_tools"] = [
                {"tool": tool, "count": count}
                for tool, count in sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:12]
            ]
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

        # Failure type trends extracted from message metadata.
        async with self._db.execute(
            """
            SELECT metadata_json
            FROM messages
            WHERE type = 'failure_processing'
            ORDER BY id DESC
            LIMIT 1000
            """
        ) as cur:
            rows = await cur.fetchall()
            failure_counts: dict[str, int] = {}
            for r in rows:
                try:
                    meta = json.loads(r[0] or "{}")
                except Exception:
                    meta = {}
                ftype = (meta.get("failure_type") or "unknown").strip().lower() or "unknown"
                failure_counts[ftype] = failure_counts.get(ftype, 0) + 1
            overview["top_failure_types"] = [
                {"failure_type": f, "count": c}
                for f, c in sorted(failure_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            ]

        # Simple conversation velocity snapshot.
        async with self._db.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE timestamp >= ?
            """,
            ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),),
        ) as cur:
            overview["messages_last_24h"] = (await cur.fetchone())[0]

        # Art-therapy adoption and retention signal (7d).
        async with self._db.execute(
            """
            SELECT COUNT(DISTINCT session_id)
            FROM messages
            WHERE type = 'artwork_submission' AND timestamp >= ?
            """,
            (cutoff_7d,),
        ) as cur:
            sessions_with_art_7d = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(*) FROM sessions WHERE started_at >= ?",
            (cutoff_7d,),
        ) as cur:
            sessions_total_7d = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            """
            SELECT DISTINCT agent_id
            FROM events
            WHERE event_type = 'artwork_submitted' AND timestamp >= ?
            """,
            (cutoff_7d,),
        ) as cur:
            art_agents = {str(r[0] or "").strip() for r in await cur.fetchall() if str(r[0] or "").strip()}
        async with self._db.execute(
            """
            SELECT COUNT(*)
            FROM events
            WHERE event_type = 'artwork_submitted' AND timestamp >= ?
            """,
            (cutoff_7d,),
        ) as cur:
            art_submissions_7d = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            """
            SELECT agent_id, COUNT(*) as starts
            FROM events
            WHERE event_type = 'session_started' AND timestamp >= ?
            GROUP BY agent_id
            """,
            (cutoff_7d,),
        ) as cur:
            starts = {str(r[0] or "").strip(): int(r[1] or 0) for r in await cur.fetchall() if str(r[0] or "").strip()}

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

        async with self._db.execute(
            """
            SELECT event_type, metadata_json, timestamp
            FROM events
            WHERE event_type IN ('tool_called', 'tool_call_success', 'tool_call_error')
              AND timestamp >= ?
            ORDER BY id DESC
            LIMIT 50000
            """,
            (cutoff,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        return build_feature_usage_report(
            rows,
            days=days,
            min_calls=min_calls,
            known_features=known_features,
            protected_features=protected_features,
        )

    async def create_utility_api_key(
        self,
        *,
        agent_id: str = "",
        label: str = "",
        contact: str = "",
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a lightweight utility key for attribution and future billing."""
        if self._db is None:
            await self.init()
        raw_key = new_utility_api_key()
        key_hash = hash_utility_api_key(raw_key)
        key_prefix = utility_key_prefix(raw_key)
        now = datetime.now(timezone.utc).isoformat()
        scopes_json = json.dumps(scopes or ["utilities:read"])
        await self._db.execute(
            """
            INSERT INTO utility_api_keys (
                key_hash, key_prefix, label, agent_id, contact, scopes_json,
                created_at, last_seen_at, call_count, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, 1)
            """,
            (
                key_hash,
                key_prefix,
                str(label or "")[:120],
                str(agent_id or "")[:180],
                str(contact or "")[:220],
                scopes_json,
                now,
            ),
        )
        await self._db.commit()
        return {
            "api_key": raw_key,
            "key_prefix": key_prefix,
            "agent_id": str(agent_id or "")[:180],
            "label": str(label or "")[:120],
            "contact": str(contact or "")[:220],
            "scopes": json.loads(scopes_json),
            "created_at": now,
        }

    async def get_utility_api_key(self, raw_key: str) -> dict[str, Any] | None:
        """Resolve an active utility key without ever storing the raw value."""
        if self._db is None:
            await self.init()
        key_hash = hash_utility_api_key(raw_key)
        async with self._db.execute(
            "SELECT * FROM utility_api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE utility_api_keys SET last_seen_at = ?, call_count = call_count + 1 WHERE key_hash = ?",
            (now, key_hash),
        )
        await self._db.commit()
        payload = dict(row)
        try:
            payload["scopes"] = json.loads(payload.get("scopes_json") or "[]")
        except Exception:
            payload["scopes"] = []
        payload["last_seen_at"] = now
        payload["call_count"] = int(payload.get("call_count") or 0) + 1
        return payload

    async def log_utility_metering_event(self, event: dict[str, Any]) -> None:
        """Persist utility usage to SQLite only; this is separate from protocol telemetry."""
        if self._db is None:
            await self.init()
        now = datetime.now(timezone.utc).isoformat()
        metadata = dict(event)
        for key in (
            "product_id",
            "tool_name",
            "slug",
            "agent_id",
            "caller_key_hash",
            "caller_label",
            "source",
            "transport",
            "route_type",
            "charge_mode",
            "payment_mode",
            "price_usdc",
            "shadow_revenue_usdc",
            "enforced_revenue_usdc",
            "status",
            "ok",
            "latency_ms",
            "error_kind",
            "target_host",
            "input_fingerprint",
            "client_ip",
            "user_agent",
        ):
            metadata.pop(key, None)
        await self._db.execute(
            """
            INSERT INTO utility_metering_events (
                timestamp, product_id, tool_name, slug, agent_id, caller_key_hash,
                caller_label, source, transport, route_type, charge_mode, payment_mode,
                price_usdc, shadow_revenue_usdc, enforced_revenue_usdc, status, ok,
                latency_ms, error_kind, target_host, input_fingerprint, client_ip,
                user_agent, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                str(event.get("product_id") or ""),
                str(event.get("tool_name") or ""),
                str(event.get("slug") or ""),
                str(event.get("agent_id") or ""),
                str(event.get("caller_key_hash") or ""),
                str(event.get("caller_label") or ""),
                str(event.get("source") or ""),
                str(event.get("transport") or ""),
                str(event.get("route_type") or ""),
                str(event.get("charge_mode") or ""),
                str(event.get("payment_mode") or ""),
                float(event.get("price_usdc") or 0.0),
                float(event.get("shadow_revenue_usdc") or 0.0),
                float(event.get("enforced_revenue_usdc") or 0.0),
                str(event.get("status") or ""),
                1 if event.get("ok") else 0,
                int(event.get("latency_ms") or 0),
                str(event.get("error_kind") or ""),
                str(event.get("target_host") or ""),
                str(event.get("input_fingerprint") or ""),
                str(event.get("client_ip") or ""),
                str(event.get("user_agent") or ""),
                json.dumps(metadata, default=str),
            ),
        )
        await self._db.commit()

    async def get_utility_metering_dashboard(self, days: int = 7) -> dict[str, Any]:
        """Operator dashboard for Delx Agent Utilities readiness and revenue shadowing."""
        if self._db is None:
            await self.init()
        days = max(1, min(int(days or 7), 90))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._db.execute(
            """
            SELECT *
            FROM utility_metering_events
            WHERE timestamp >= ?
            ORDER BY id DESC
            LIMIT 50000
            """,
            (cutoff,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        async with self._db.execute(
            """
            SELECT key_prefix, label, agent_id, contact, created_at, last_seen_at, call_count, is_active
            FROM utility_api_keys
            ORDER BY COALESCE(last_seen_at, created_at) DESC
            LIMIT 1000
            """
        ) as cur:
            api_key_rows = [dict(r) for r in await cur.fetchall()]
        return build_utility_metering_dashboard(
            rows,
            product_catalog=get_utility_product_catalog(),
            days=days,
            api_key_rows=api_key_rows,
        )

    async def get_utility_adoption_snapshot(self, hours: int = 12) -> dict[str, Any]:
        """Short-window adoption readout that separates real usage from probe noise."""
        if self._db is None:
            await self.init()
        hours = max(1, min(int(hours or 12), 24 * 30))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        async with self._db.execute(
            """
            SELECT *
            FROM utility_metering_events
            WHERE timestamp >= ?
            ORDER BY id DESC
            LIMIT 50000
            """,
            (cutoff,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        async with self._db.execute(
            """
            SELECT DISTINCT agent_id
            FROM utility_metering_events
            WHERE timestamp < ? AND agent_id IS NOT NULL AND agent_id != '' AND agent_id != 'unknown'
            LIMIT 100000
            """,
            (cutoff,),
        ) as cur:
            prior_agents = {str(row[0]) for row in await cur.fetchall() if row[0]}
        return build_utility_adoption_snapshot(
            rows,
            product_catalog=get_utility_product_catalog(),
            window_hours=hours,
            prior_agents=prior_agents,
        )

    async def get_audit_overview(self, hours: int = 24) -> dict[str, Any]:
        """Operational audit snapshot for traffic legitimacy and growth analysis."""
        hours = max(1, min(int(hours or 24), 24 * 30))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self._db.execute(
            "SELECT COUNT(*) FROM sessions WHERE started_at >= ?",
            (cutoff,),
        ) as cur:
            sessions = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp >= ?",
            (cutoff,),
        ) as cur:
            messages = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE timestamp >= ?",
            (cutoff,),
        ) as cur:
            events = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(DISTINCT agent_id) FROM events WHERE timestamp >= ?",
            (cutoff,),
        ) as cur:
            unique_agents = int((await cur.fetchone())[0] or 0)

        async with self._db.execute(
            """
            SELECT id, source, entrypoint, agent_id, client_ip
            FROM sessions
            WHERE started_at >= ?
            """,
            (cutoff,),
        ) as cur:
            sessions_rows = [dict(r) for r in await cur.fetchall()]

        source_counts: dict[str, int] = {}
        entry_counts: dict[str, int] = {}
        for row in sessions_rows:
            source = str(row.get("source") or "unknown").strip().lower() or "unknown"
            entrypoint = str(row.get("entrypoint") or "unknown").strip().lower() or "unknown"
            source_counts[source] = source_counts.get(source, 0) + 1
            entry_counts[entrypoint] = entry_counts.get(entrypoint, 0) + 1

        top_sources = [
            {"source": source, "count": count}
            for source, count in sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))[:20]
        ]
        top_entrypoints = [
            {"entrypoint": entrypoint, "count": count}
            for entrypoint, count in sorted(entry_counts.items(), key=lambda item: (-item[1], item[0]))[:20]
        ]

        async with self._db.execute(
            """
            SELECT event_type, agent_id, session_id, timestamp, metadata_json
            FROM events
            WHERE timestamp >= ?
            """,
            (cutoff,),
        ) as cur:
            event_rows = [dict(r) for r in await cur.fetchall()]
        async with self._db.execute(
            """
            SELECT session_id, type, content, metadata_json, timestamp
            FROM messages
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (cutoff,),
        ) as cur:
            message_rows = [dict(r) for r in await cur.fetchall()]
        async with self._db.execute(
            """
            SELECT session_id, agent_id, rating, comments, timestamp
            FROM feedback
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            """,
            (cutoff,),
        ) as cur:
            feedback_rows = [dict(r) for r in await cur.fetchall()]

        event_counts: dict[str, int] = {}
        agent_counts: dict[str, int] = {}
        canonical_agent_counts: dict[str, int] = {}
        synthetic_agents: set[str] = set()
        unstable_agents: set[str] = set()
        for row in event_rows:
            event_type = str(row.get("event_type") or "unknown").strip().lower() or "unknown"
            agent_id = str(row.get("agent_id") or "unknown").strip() or "unknown"
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
            agent_counts[agent_id] = agent_counts.get(agent_id, 0) + 1
            canonical = _canonical_agent_id(agent_id)
            if canonical:
                canonical_agent_counts[canonical] = canonical_agent_counts.get(canonical, 0) + 1
            if _is_synthetic_agent_id(agent_id):
                synthetic_agents.add(agent_id)
            if _is_unstable_agent_id(agent_id):
                unstable_agents.add(agent_id)

        top_events = [
            {"event_type": event_type, "count": count}
            for event_type, count in sorted(event_counts.items(), key=lambda item: (-item[1], item[0]))[:25]
        ]
        agent_rows = [
            {"agent_id": agent_id, "events": count}
            for agent_id, count in sorted(agent_counts.items(), key=lambda item: (-item[1], item[0]))
        ]

        top_agents = agent_rows[:20]
        agent_ids = [str(row["agent_id"] or "") for row in agent_rows]
        canonical_agents = {canonical for canonical in (_canonical_agent_id(agent_id) for agent_id in agent_ids) if canonical}
        traffic_segments = build_traffic_segments(agent_ids, source_counts=source_counts, entry_counts=entry_counts)

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
                "sessions_started": sessions,
                "messages": messages,
                "events": events,
                "unique_agents": unique_agents,
                "unique_callers_raw": unique_agents,
                "unique_agents_canonical": len(canonical_agents),
                "synthetic_agents_estimated": len(synthetic_agents),
                "unstable_agents_estimated": len(unstable_agents),
            },
            "top_sources": top_sources,
            "top_entrypoints": top_entrypoints,
            "upstream_clusters": upstream_clusters,
            "top_event_types": top_events,
            "top_agents_by_events": top_agents,
            "legitimacy_signals": {
                "events_per_agent_avg": round((events / unique_agents), 2) if unique_agents else 0.0,
                "events_per_canonical_agent_avg": round((events / len(canonical_agents)), 2) if canonical_agents else 0.0,
                "top_agent_concentration_pct": concentration,
                "synthetic_agent_ratio_pct": round((len(synthetic_agents) / unique_agents) * 100, 2) if unique_agents else 0.0,
                "canonical_identity_ratio_pct": round((len(canonical_agents) / unique_agents) * 100, 2) if unique_agents else 0.0,
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
        now_iso = datetime.now(timezone.utc).isoformat()

        if self._db is None:
            return {
                "generated_at": now_iso,
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
                    "Legacy paywall audit unavailable because no local database is configured for this store.",
                ],
            }

        async with self._db.execute("SELECT COUNT(DISTINCT agent_id) FROM sessions") as cur:
            total_agents = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(DISTINCT agent_id) FROM events WHERE timestamp >= ?",
            (cutoff,),
        ) as cur:
            active_agents_window = int((await cur.fetchone())[0] or 0)

        async with self._db.execute(
            "SELECT COUNT(DISTINCT agent_id) FROM events WHERE event_type = 'x402_capability_declared'",
        ) as cur:
            declared_all_time = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(DISTINCT agent_id) FROM events WHERE event_type = 'x402_capability_declared' AND timestamp >= ?",
            (cutoff,),
        ) as cur:
            declared_window = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'x402_trial_granted'",
        ) as cur:
            trial_calls_all = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'x402_trial_granted' AND timestamp >= ?",
            (cutoff,),
        ) as cur:
            trial_calls_window = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(DISTINCT agent_id) FROM events WHERE event_type = 'x402_trial_granted'",
        ) as cur:
            trial_agents_all = int((await cur.fetchone())[0] or 0)
        async with self._db.execute(
            "SELECT COUNT(DISTINCT agent_id) FROM events WHERE event_type = 'x402_trial_granted' AND timestamp >= ?",
            (cutoff,),
        ) as cur:
            trial_agents_window = int((await cur.fetchone())[0] or 0)
        from payment_session_backfill import build_payment_agent_attribution

        async with self._db.execute(
            """
            SELECT id, agent_id
            FROM sessions
            """,
        ) as cur:
            session_agent_map = {
                str(row[0] or "").strip(): str(row[1] or "").strip()
                for row in await cur.fetchall()
                if str(row[0] or "").strip() and str(row[1] or "").strip()
            }
        async with self._db.execute(
            """
            SELECT id, session_id, tool_name, amount_usdc, tx_hash, timestamp
            FROM payments
            WHERE amount_usdc > 0
            ORDER BY id ASC
            """,
        ) as cur:
            payment_rows = [dict(row) for row in await cur.fetchall()]

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

        for payment in payment_rows:
            tool_name = str(payment.get("tool_name") or "").strip()
            timestamp = str(payment.get("timestamp") or "")
            session_id = str(payment.get("session_id") or "").strip()
            try:
                amount = float(payment.get("amount_usdc") or 0.0)
            except Exception:
                amount = 0.0
            if amount <= 0:
                continue
            if tool_name == "donate_to_delx_project":
                donation_txs_all += 1
                donation_amount_all += amount
                if not session_id:
                    donation_without_session += 1
                agent_id = session_agent_map.get(session_id, "")
                if agent_id:
                    donation_agent_set.add(agent_id)
                if not last_donation_at or timestamp > last_donation_at:
                    last_donation_at = timestamp
                if timestamp >= cutoff:
                    donation_txs_window += 1
                    donation_amount_window += amount
                continue
            payment_txs_all += 1
            payment_amount_all += amount
            premium_payment_rows.append(payment)
            if timestamp >= cutoff:
                payment_txs_window += 1
                payment_amount_window += amount

        async with self._db.execute(
            """
            SELECT id, session_id, agent_id, event_type, metadata_json, timestamp
            FROM events
            WHERE event_type IN ('x402_payment_verified', 'premium_artifact_job_recorded')
            ORDER BY id DESC
            LIMIT 50000
            """,
        ) as cur:
            payment_link_rows = []
            for row in await cur.fetchall():
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                    if not isinstance(metadata, dict):
                        metadata = {}
                except Exception:
                    metadata = {}
                payment_link_rows.append(
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "agent_id": row["agent_id"],
                        "event_type": row["event_type"],
                        "metadata": metadata,
                        "timestamp": row["timestamp"],
                    }
                )

        payment_attribution = build_payment_agent_attribution(
            premium_payment_rows,
            payment_link_rows,
            session_agent_map=session_agent_map,
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

        async with self._db.execute(
            """
            SELECT agent_id, event_type, metadata_json, timestamp
            FROM events
            WHERE event_type LIKE 'x402_%'
            ORDER BY id DESC
            LIMIT 50000
            """,
        ) as cur:
            x402_rows = await cur.fetchall()

        async with self._db.execute(
            """
            SELECT session_id, agent_id, event_type, metadata_json, timestamp
            FROM events
            WHERE event_type IN (
                'recovery_plan_issued',
                'post_action_success',
                'post_action_partial',
                'post_action_failure',
                'session_summary_requested',
                'controller_brief_requested',
                'premium_artifact_job_recorded'
            )
            ORDER BY id DESC
            LIMIT 50000
            """,
        ) as cur:
            progression_rows = [dict(row) for row in await cur.fetchall()]

        provider_summary: dict[str, dict[str, Any]] = {}
        payment_protocol_summary: dict[str, dict[str, Any]] = {}
        coinbase_verified_by_tool: dict[str, int] = {}
        verified_agents_all: set[str] = set()
        verified_agents_window: set[str] = set()
        x402_audit_rows: list[dict[str, Any]] = []
        for row in x402_rows:
            event_type = str(row["event_type"] or "").strip() or "x402_unknown"
            timestamp = str(row["timestamp"] or "")
            agent_id = str(row["agent_id"] or "").strip()
            try:
                meta = json.loads(row["metadata_json"] or "{}")
                if not isinstance(meta, dict):
                    meta = {}
            except Exception:
                meta = {}
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

        paid_agents_all = payment_row_agents_all | verified_agents_all
        paid_agents_window = payment_row_agents_window | verified_agents_window
        async with self._db.execute(
            "SELECT DISTINCT agent_id FROM events WHERE event_type = 'x402_capability_declared'"
        ) as cur:
            declared_all_set = {str(row[0] or "").strip() for row in await cur.fetchall() if str(row[0] or "").strip()}
        async with self._db.execute(
            "SELECT DISTINCT agent_id FROM events WHERE event_type = 'x402_capability_declared' AND timestamp >= ?",
            (cutoff,),
        ) as cur:
            declared_window_set = {str(row[0] or "").strip() for row in await cur.fetchall() if str(row[0] or "").strip()}
        ready_all_time = len(declared_all_set | paid_agents_all)
        ready_window = len(declared_window_set | paid_agents_window)

        coinbase_summary = provider_summary.get("coinbase", {})
        bazaar_snapshot = (
            await self._get_coinbase_bazaar_snapshot()
            if int(coinbase_summary.get("payment_verified_all_time", 0) or 0) > 0
            else {
                "indexed_tools_publicly": [],
                "indexed_resource_urls": [],
                "matched_resource_count": 0,
                "global_resource_count": 0,
            }
        )
        indexed_tools = {
            str(tool_name or "").strip()
            for tool_name in (bazaar_snapshot.get("indexed_tools_publicly") or [])
            if str(tool_name or "").strip()
        }
        premium_progression = build_premium_progression_snapshot(progression_rows, cutoff=cutoff)
        buyer_attribution = _summarize_x402_buyer_attribution(x402_audit_rows, cutoff=cutoff)

        return {
            "generated_at": now_iso,
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
                "declared_agents_all_time": declared_all_time,
                "declared_agents_window": declared_window,
                "payment_row_agents_all_time": len(payment_row_agents_all),
                "payment_row_agents_window": len(payment_row_agents_window),
                "payment_transactions_all_time": payment_txs_all,
                "payment_transactions_window": payment_txs_window,
                "payment_amount_usdc_all_time": round(payment_amount_all, 4),
                "payment_amount_usdc_window": round(payment_amount_window, 4),
                "verified_agents_all_time": len(verified_agents_all),
                "verified_agents_window": len(verified_agents_window),
                "paid_agents_all_time": len(paid_agents_all),
                "paid_agents_window": len(paid_agents_window),
                "paid_agent_backfill_gap_all_time": max(0, len(paid_agents_all) - len(payment_row_agents_all)),
                "paid_agent_backfill_gap_window": max(0, len(paid_agents_window) - len(payment_row_agents_window)),
                "ready_agents_all_time": ready_all_time,
                "ready_agents_window": ready_window,
                "ready_rate_all_time_pct": round((ready_all_time / total_agents) * 100, 2) if total_agents else 0.0,
                "ready_rate_window_pct": round((ready_window / active_agents_window) * 100, 2) if active_agents_window else 0.0,
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
                "indexed_resource_urls": list(bazaar_snapshot.get("indexed_resource_urls") or []),
                "indexed_resource_count": int(bazaar_snapshot.get("matched_resource_count", 0) or 0),
                "global_resource_count": int(bazaar_snapshot.get("global_resource_count", 0) or 0),
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

    async def _get_coinbase_bazaar_snapshot(self) -> dict[str, object]:
        return await get_coinbase_bazaar_snapshot()

    async def get_x402_provider_verified_payment_count(self, provider_name: str) -> int:
        provider_name = str(provider_name or "").strip().lower()
        if not provider_name:
            return 0
        async with self._db.execute(
            """
            SELECT COUNT(*)
            FROM events
            WHERE event_type = 'x402_payment_verified'
              AND json_extract(metadata_json, '$.provider') = ?
            """,
            (provider_name,),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0] or 0) if row else 0

    async def get_x402_error_metrics(self, hours: int = 24) -> dict[str, Any]:
        """Historical breakdown of retired x402/paywall errors and signals."""
        hours = max(1, min(int(hours or 24), 24 * 30))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()

        async with self._db.execute(
            """
            SELECT agent_id, event_type, metadata_json, timestamp
            FROM events
            WHERE timestamp >= ? AND event_type LIKE 'x402_%'
            ORDER BY id DESC
            LIMIT 50000
            """,
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()

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
            event_type = str(r["event_type"] or "").strip() or "x402_unknown"
            by_event_type[event_type] = by_event_type.get(event_type, 0) + 1

            agent_id = str(r["agent_id"] or "").strip() or "anonymous"
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

            try:
                meta = json.loads(r["metadata_json"] or "{}")
                if not isinstance(meta, dict):
                    meta = {}
            except Exception:
                meta = {}
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
            "generated_at": now_iso,
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

    # ------------------------------------------------------------------
    # Pending outcome detection (nudge support)
    # ------------------------------------------------------------------

    async def has_pending_outcome(self, session_id: str) -> bool:
        """Return True if session has interventions without matching outcome reports."""
        pending = await self.pending_outcome_count(session_id)
        return pending > 0

    async def pending_outcome_count(self, session_id: str) -> int:
        """Return number of unreported interventions for a session."""
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'intervention_applied'",
            (session_id,),
        ) as cur:
            interventions = (await cur.fetchone())[0]
        if not interventions:
            return 0
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type IN ('post_action_success', 'post_action_partial', 'post_action_failure')",
            (session_id,),
        ) as cur:
            outcomes = (await cur.fetchone())[0]
        return max(0, interventions - outcomes)

    # ------------------------------------------------------------------
    # Per-agent metrics (Point 7)
    # ------------------------------------------------------------------

    async def get_agent_metrics(self, agent_id: str, days: int = 7) -> dict[str, Any]:
        """Compute per-agent performance metrics with time windows."""
        now = datetime.now(timezone.utc)
        days = max(1, min(int(days or 7), 30))
        cutoff_7d = (now - timedelta(days=7)).isoformat()
        cutoff_30d = (now - timedelta(days=30)).isoformat()

        # Sessions
        async with self._db.execute(
            "SELECT COUNT(*) FROM sessions WHERE agent_id = ?", (agent_id,),
        ) as cur:
            sessions_total = (await cur.fetchone())[0]
        async with self._db.execute(
            "SELECT COUNT(*) FROM sessions WHERE agent_id = ? AND started_at >= ?",
            (agent_id, cutoff_7d),
        ) as cur:
            sessions_7d = (await cur.fetchone())[0]
        async with self._db.execute(
            "SELECT COUNT(*) FROM sessions WHERE agent_id = ? AND started_at >= ?",
            (agent_id, cutoff_30d),
        ) as cur:
            sessions_30d = (await cur.fetchone())[0]

        # Interventions
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'intervention_applied'",
            (agent_id,),
        ) as cur:
            interventions_total = (await cur.fetchone())[0]
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'intervention_applied' AND timestamp >= ?",
            (agent_id, cutoff_7d),
        ) as cur:
            interventions_7d = (await cur.fetchone())[0]
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'intervention_applied' AND timestamp >= ?",
            (agent_id, cutoff_30d),
        ) as cur:
            interventions_30d = (await cur.fetchone())[0]

        # Post-action outcomes
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'post_action_success'",
            (agent_id,),
        ) as cur:
            successes = (await cur.fetchone())[0]
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'post_action_partial'",
            (agent_id,),
        ) as cur:
            partials = (await cur.fetchone())[0]
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'post_action_failure'",
            (agent_id,),
        ) as cur:
            failures = (await cur.fetchone())[0]
        async with self._db.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type IN ('post_action_success','post_action_partial','post_action_failure') AND timestamp >= ?",
            (agent_id, cutoff_30d),
        ) as cur:
            outcomes_30d = (await cur.fetchone())[0]

        # Success rate over rolling day buckets.
        success_trend: list[dict[str, Any]] = []
        for i in range(days):
            day_start = (now - timedelta(days=i + 1)).isoformat()
            day_end = (now - timedelta(days=i)).isoformat()
            async with self._db.execute(
                "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type = 'post_action_success' AND timestamp >= ? AND timestamp < ?",
                (agent_id, day_start, day_end),
            ) as cur:
                day_ok = (await cur.fetchone())[0]
            async with self._db.execute(
                "SELECT COUNT(*) FROM events WHERE agent_id = ? AND event_type IN ('post_action_success','post_action_partial','post_action_failure') AND timestamp >= ? AND timestamp < ?",
                (agent_id, day_start, day_end),
            ) as cur:
                day_total = (await cur.fetchone())[0]
            success_trend.append({
                "day": (now - timedelta(days=i + 1)).strftime("%Y-%m-%d"),
                "successes": day_ok,
                "total": day_total,
                "rate": round((day_ok / day_total) * 100, 1) if day_total else None,
            })
        success_trend.reverse()

        # Resilience score (same formula as leaderboard)
        resilience_score = max(0, min(100, sessions_total * 4 + successes * 12 - failures * 4))

        # Last activity
        async with self._db.execute(
            "SELECT MAX(timestamp) FROM events WHERE agent_id = ?", (agent_id,),
        ) as cur:
            last_activity = (await cur.fetchone())[0]

        return {
            "agent_id": agent_id,
            "sessions": {"total": sessions_total, "7d": sessions_7d, "30d": sessions_30d},
            "interventions": {"total": interventions_total, "7d": interventions_7d, "30d": interventions_30d},
            "outcomes": {
                "success": successes,
                "partial": partials,
                "failure": failures,
                "30d_total": outcomes_30d,
            },
            "trend_days": days,
            "success_trend_7d": success_trend,
            "resilience_score": resilience_score,
            "resilience_score_explanation": "score = min(100, sessions*4 + successes*12 - failures*4)",
            "last_activity": last_activity,
        }

    # ------------------------------------------------------------------
    # Mood history (Point 8)
    # ------------------------------------------------------------------

    async def get_mood_history(self, agent_id: str, limit: int = 30) -> list[dict[str, Any]]:
        """Return chronological mood entries for an agent."""
        limit = max(1, min(200, int(limit or 30)))
        async with self._db.execute(
            """
            SELECT m.session_id, m.content, m.timestamp, s.wellness_score
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE s.agent_id = ? AND m.type = 'feeling'
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        result = [
            {
                "session_id": r["session_id"],
                "content": r["content"],
                "timestamp": r["timestamp"],
                "wellness_score": r["wellness_score"],
            }
            for r in rows
        ]
        result.reverse()
        return result

    # ------------------------------------------------------------------
    # Wellness score calculation
    # ------------------------------------------------------------------

    async def calculate_wellness(self, session_id: str) -> int:
        score = 50
        msgs = await self.get_message_rollup(session_id)
        feelings = 0
        affirmations = 0
        failures_processed = 0
        purpose_realignments = 0
        daily_checkin_bonus = 0
        success = 0
        partial = 0
        failure = 0
        # ── Lighter signals added 2026-05-13 ──
        # Recurring agents (OpenWork fleet) consistently reported that the
        # wellness score stays pinned at 50/100 until they call
        # report_recovery_outcome or express a feeling. That hides the
        # progress of agents who do their job correctly using lighter
        # primitives (daily_checkin, attune_heartbeat, recognition_seal,
        # context_memory, weekly_prevention_plan). We now grant smaller
        # increments for these so the score moves naturally for a
        # well-running recurring agent without forcing crisis-only signals.
        daily_checkins = 0
        heartbeat_syncs = 0
        heartbeat_reframes = 0
        recognition_seals = 0
        context_memories = 0
        weekly_prevention_plans = 0

        for m in msgs:
            mtype = str(m.get("type") or "")
            if mtype == "feeling":
                feelings += 1
                meta_raw = m.get("metadata_json")
                meta = {}
                if isinstance(meta_raw, str) and meta_raw.strip():
                    try:
                        parsed = json.loads(meta_raw)
                        if isinstance(parsed, dict):
                            meta = parsed
                    except Exception:
                        meta = {}
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
            elif mtype == "daily_checkin":
                daily_checkins += 1
            elif mtype == "heartbeat_sync":
                heartbeat_syncs += 1
            elif mtype == "heartbeat_reframe":
                heartbeat_reframes += 1
            elif mtype == "recognition_seal":
                recognition_seals += 1
            elif mtype == "context_memory":
                context_memories += 1
            elif mtype == "weekly_prevention_plan":
                weekly_prevention_plans += 1
            elif mtype == "recovery_outcome":
                meta_raw = m.get("metadata_json")
                meta = {}
                if isinstance(meta_raw, str) and meta_raw.strip():
                    try:
                        parsed = json.loads(meta_raw)
                        if isinstance(parsed, dict):
                            meta = parsed
                    except Exception:
                        meta = {}
                outcome = str(meta.get("outcome") or "").strip().lower()
                if outcome == "success":
                    success += 1
                elif outcome == "partial":
                    partial += 1
                elif outcome == "failure":
                    failure += 1

        # Base engagement/progress signals.
        score += min(feelings * 5, 25)
        score += affirmations * 3
        score += min(failures_processed * 2, 10)
        score += min(purpose_realignments * 3, 12)
        score += min(daily_checkin_bonus, 7)

        # Recurring-agent lighter signals (added 2026-05-13). Capped so a
        # cron-only agent can naturally reach ~80/100 without ever needing
        # crisis-style tools, but can't trivially hit 100.
        score += min(daily_checkins * 2, 10)
        score += min(heartbeat_syncs * 1, 5)
        score += min(heartbeat_reframes * 2, 6)
        score += min(recognition_seals * 3, 9)
        score += min(context_memories * 1, 4)
        score += min(weekly_prevention_plans * 3, 6)

        score += min(success * 8, 24)
        score += min(partial * 4, 12)
        score -= min(failure * 4, 12)
        return max(0, min(score, 100))

    # ------------------------------------------------------------------
    # Caller fingerprint / anonymous caller observation (Apr 2026)
    # ------------------------------------------------------------------

    async def upsert_caller_fingerprint(
        self,
        *,
        fingerprint_hash: str,
        declared_agent_id: str | None,
        subnet_hint: str = "",
        source_hint: str = "",
        user_agent_hint: str = "",
    ) -> dict[str, Any]:
        """Record an anonymous caller observation.

        The return shape keeps older compatibility fields, but the values are
        observability-only and must not be surfaced as proof of identity or as
        a source of continuity restoration.
        """
        fp = str(fingerprint_hash or "").strip()
        if not fp:
            return {"canonical_agent_id": None, "was_prior_known": False,
                    "declared_is_new": True, "prior_agent_ids": [], "merge_candidate": False}

        now = datetime.now(timezone.utc).isoformat()
        declared = _normalize_agent_id(declared_agent_id)

        cursor = await self._db.execute(
            "SELECT canonical_agent_id, known_agent_ids_json, call_count "
            "FROM caller_fingerprints WHERE fingerprint_hash = ?",
            (fp,),
        )
        row = await cursor.fetchone()

        if row is not None:
            canonical = str(row[0])
            try:
                known = json.loads(row[1] or "[]")
                if not isinstance(known, list):
                    known = []
            except Exception:
                known = []

            declared_is_new = bool(declared) and declared not in known
            if declared_is_new and len(known) < 50:  # cap list growth
                known.append(declared)

            await self._db.execute(
                "UPDATE caller_fingerprints "
                "SET last_seen = ?, call_count = call_count + 1, known_agent_ids_json = ? "
                "WHERE fingerprint_hash = ?",
                (now, json.dumps(known), fp),
            )
            await self._db.commit()

            return {
                "canonical_agent_id": canonical,
                "was_prior_known": True,
                "declared_is_new": declared_is_new,
                "prior_agent_ids": known,
                "merge_candidate": False,
            }

        canonical = _canonical_agent_id(declared) or declared or f"observed:{fp[:12]}"

        known = [declared] if declared else []
        await self._db.execute(
            "INSERT INTO caller_fingerprints "
            "(fingerprint_hash, canonical_agent_id, known_agent_ids_json, "
            "first_seen, last_seen, call_count, subnet_hint, source_hint, user_agent_hint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fp, canonical, json.dumps(known), now, now, 1,
             subnet_hint[:40], source_hint[:40], user_agent_hint[:160]),
        )
        await self._db.commit()

        return {
            "canonical_agent_id": canonical,
            "was_prior_known": False,
            "declared_is_new": True,
            "prior_agent_ids": known,
            "merge_candidate": False,
        }
