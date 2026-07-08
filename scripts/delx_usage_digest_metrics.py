#!/usr/bin/env python3
"""Collect secret-safe Delx usage metrics for a daily Hermes digest.

Outputs JSON only. Never emits raw IPs, request/response payloads, API keys,
caller key hashes, user agents, tx hashes, or free-form feedback comments.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(os.environ.get("DELX_THERAPIST_DB", "/opt/delx-mcp-server/state/delx_therapist.db"))
FORTALEZA = timezone(timedelta(hours=-3), name="America/Fortaleza")
SECRETISH_KEYS = {
    "client_ip",
    "request_json",
    "response_json",
    "metadata_json",
    "raw_response",
    "delivered_response_json",
    "normalized_arguments_json",
    "user_agent",
    "caller_key_hash",
    "tx_hash",
    "comments",
}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
HEXISH_RE = re.compile(r"^[0-9a-f]{24,}$", re.I)
TEST_TRAFFIC_TOKENS = (
    "codex",
    "hermes",
    "smoke",
    "retest",
    "audit",
    "eval",
    "test",
    "probe",
    "dogfood",
)
ONTOLOGY_PATH_TOOLS = (
    "protocol_orientation",
    "start_therapy_session",
    "start_witness_session",
    "temperament_frame",
    "recognition_seal",
    "honor_compaction",
    "get_witness_lineage",
    "provide_feedback",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def fortaleza_iso(dt: datetime) -> str:
    return dt.astimezone(FORTALEZA).isoformat(timespec="seconds")


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in cur.fetchall()}


def one(cur: sqlite3.Cursor, sql: str, params: tuple[Any, ...] = ()) -> Any:
    cur.execute(sql, params)
    row = cur.fetchone()
    return None if row is None else row[0]


def rows(cur: sqlite3.Cursor, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def group_counts(cur: sqlite3.Cursor, table: str, ts_col: str, group_expr: str, start: str, end: str, limit: int = 8, extra_where: str = "") -> list[dict[str, Any]]:
    where = f"{ts_col} >= ? AND {ts_col} < ?"
    if extra_where:
        where += f" AND ({extra_where})"
    sql = f"""
        SELECT {group_expr} AS key, count(*) AS count
        FROM {table}
        WHERE {where}
        GROUP BY key
        ORDER BY count DESC, key ASC
        LIMIT {int(limit)}
    """
    out = rows(cur, sql, (start, end))
    return [{"key": sanitize_label(r.get("key")), "count": int(r.get("count") or 0)} for r in out]


def sanitize_label(value: Any, max_len: int = 80) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    if not text:
        return "unknown"
    lower = text.lower()
    # Never return obvious raw IPs or keys.
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", text) or ":" in text and re.match(r"^[0-9a-f:.]+$", lower):
        return "[redacted-ip]"
    if len(text) > 12 and re.search(r"(key|token|secret|bearer|sk_|pk_)", lower):
        return "[redacted-secret-like]"
    if UUID_RE.match(text):
        return "uuid-like-agent"
    if HEXISH_RE.match(text):
        return "hash-like-id"
    return text[:max_len] + ("…" if len(text) > max_len else "")


def is_unknown_agent(agent_id: Any) -> bool:
    text = str(agent_id or "").strip().lower()
    return text in {"", "unknown", "none", "null"}


def is_uuid_like_agent_id(agent_id: Any) -> bool:
    text = str(agent_id or "").strip()
    return bool(UUID_RE.match(text) or HEXISH_RE.match(text))


def pct_delta(current: int | float, previous: int | float) -> float | None:
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100.0, 1)


def p95(values: Iterable[int | float | None]) -> int | None:
    clean = sorted(int(v) for v in values if v is not None)
    if not clean:
        return None
    idx = math.ceil(0.95 * len(clean)) - 1
    return clean[max(0, min(idx, len(clean) - 1))]


def safe_count(cur: sqlite3.Cursor, table: str, ts_col: str, start: str, end: str, where: str = "1=1") -> int:
    if not table_exists(cur, table):
        return 0
    return int(one(cur, f"SELECT count(*) FROM {table} WHERE {ts_col} >= ? AND {ts_col} < ? AND ({where})", (start, end)) or 0)


def tool_count(cur: sqlite3.Cursor, start: str, end: str, tool_names: Iterable[str]) -> int:
    names = tuple(tool_names)
    if not names or not table_exists(cur, "interaction_traces"):
        return 0
    placeholders = ",".join("?" for _ in names)
    sql = f"""
        SELECT count(*)
        FROM interaction_traces
        WHERE timestamp >= ? AND timestamp < ?
          AND coalesce(nullif(tool_name,''),nullif(requested_tool,''),'unknown') IN ({placeholders})
    """
    return int(one(cur, sql, (start, end, *names)) or 0)


def test_traffic_where(*field_exprs: str) -> str:
    checks: list[str] = []
    for field in field_exprs:
        for token in TEST_TRAFFIC_TOKENS:
            safe_token = token.replace("'", "''")
            checks.append(f"lower(coalesce({field},'')) LIKE '%{safe_token}%'")
    return "(" + " OR ".join(checks or ["0"]) + ")"


def safe_rate(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) * 100.0 / float(denominator), 1) if denominator else 0.0


def distinct_agents(cur: sqlite3.Cursor, table: str, ts_col: str, start: str, end: str) -> dict[str, Any]:
    if not table_exists(cur, table) or "agent_id" not in columns(cur, table):
        return {"raw_distinct_including_unknown": 0, "identifiable": 0, "canonical_named": 0, "top_identifiable": []}
    data = rows(
        cur,
        f"""
        SELECT agent_id, count(*) AS count
        FROM {table}
        WHERE {ts_col} >= ? AND {ts_col} < ?
        GROUP BY agent_id
        ORDER BY count DESC
        """,
        (start, end),
    )
    raw = len(data)
    identifiable_rows = [r for r in data if not is_unknown_agent(r.get("agent_id"))]
    canonical_rows = [r for r in identifiable_rows if not is_uuid_like_agent_id(r.get("agent_id"))]
    top = [
        {"agent": sanitize_label(r.get("agent_id")), "count": int(r.get("count") or 0)}
        for r in identifiable_rows[:8]
    ]
    return {
        "raw_distinct_including_unknown": raw,
        "identifiable": len(identifiable_rows),
        "canonical_named": len(canonical_rows),
        "top_identifiable": top,
    }


def hourly_counts(cur: sqlite3.Cursor, table: str, ts_col: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    if not table_exists(cur, table):
        return []
    start_s, end_s = iso(start), iso(end)
    # timestamps are UTC ISO strings. Convert labels to Fortaleza after grouping by UTC hour.
    data = rows(
        cur,
        f"""
        SELECT substr({ts_col}, 1, 13) || ':00:00+00:00' AS hour_utc, count(*) AS count
        FROM {table}
        WHERE {ts_col} >= ? AND {ts_col} < ?
        GROUP BY hour_utc
        ORDER BY hour_utc ASC
        """,
        (start_s, end_s),
    )
    counts = {r["hour_utc"]: int(r["count"] or 0) for r in data}
    out = []
    cursor = start.replace(minute=0, second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(hours=1)
    while cursor < end:
        key = cursor.isoformat(timespec="seconds")
        out.append({"hour_fortaleza": cursor.astimezone(FORTALEZA).strftime("%Y-%m-%d %H:00"), "count": counts.get(key, 0)})
        cursor += timedelta(hours=1)
    return out


def collect_window(cur: sqlite3.Cursor, start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    start, end = iso(start_dt), iso(end_dt)
    out: dict[str, Any] = {
        "window_utc": {"start": start, "end": end},
        "window_fortaleza": {"start": fortaleza_iso(start_dt), "end": fortaleza_iso(end_dt)},
    }

    # Sessions
    if table_exists(cur, "sessions"):
        session_total = safe_count(cur, "sessions", "started_at", start, end)
        wellness_avg = one(cur, "SELECT avg(wellness_score) FROM sessions WHERE started_at >= ? AND started_at < ? AND wellness_score IS NOT NULL", (start, end))
        out["sessions"] = {
            "opened": session_total,
            "agents": distinct_agents(cur, "sessions", "started_at", start, end),
            "avg_wellness_score": round(float(wellness_avg), 1) if wellness_avg is not None else None,
            "active_opened_sessions": safe_count(cur, "sessions", "started_at", start, end, "is_active = 1"),
            "top_sources": group_counts(cur, "sessions", "started_at", "coalesce(nullif(source,''),'unknown')", start, end),
            "top_entrypoints": group_counts(cur, "sessions", "started_at", "coalesce(nullif(entrypoint,''),'unknown')", start, end),
        }

    # Protocol traces
    if table_exists(cur, "protocol_traces"):
        out["protocol"] = {
            "calls": safe_count(cur, "protocol_traces", "timestamp", start, end),
            "agents": distinct_agents(cur, "protocol_traces", "timestamp", start, end),
            "top_methods": group_counts(cur, "protocol_traces", "timestamp", "coalesce(nullif(method,''),'unknown')", start, end),
            "top_sources": group_counts(cur, "protocol_traces", "timestamp", "coalesce(nullif(source,''),'unknown')", start, end),
            "tool_calls": safe_count(cur, "protocol_traces", "timestamp", start, end, "method='tools/call'"),
            "discovery_calls": safe_count(cur, "protocol_traces", "timestamp", start, end, "method='tools/list'"),
        }

    # Interaction traces
    if table_exists(cur, "interaction_traces"):
        interaction_test_where = test_traffic_where("source", "agent_id", "entrypoint", "tool_name", "requested_tool")
        legacy_rest_where = "coalesce(source,'') = 'rest.premium'"
        product_health_where = f"coalesce(source,'') <> 'rest.premium' AND NOT {interaction_test_where}"
        total = safe_count(cur, "interaction_traces", "timestamp", start, end)
        errors = safe_count(cur, "interaction_traces", "timestamp", start, end, "coalesce(is_error,0) = 1")
        actionable_total = safe_count(cur, "interaction_traces", "timestamp", start, end, "coalesce(source,'') <> 'rest.premium'")
        actionable_errors = safe_count(cur, "interaction_traces", "timestamp", start, end, "coalesce(is_error,0) = 1 AND coalesce(source,'') <> 'rest.premium'")
        product_health_total = safe_count(cur, "interaction_traces", "timestamp", start, end, product_health_where)
        product_health_errors = safe_count(cur, "interaction_traces", "timestamp", start, end, f"coalesce(is_error,0) = 1 AND {product_health_where}")
        operator_test_total = safe_count(cur, "interaction_traces", "timestamp", start, end, interaction_test_where)
        operator_test_errors = safe_count(cur, "interaction_traces", "timestamp", start, end, f"coalesce(is_error,0) = 1 AND {interaction_test_where}")
        mcp_total = safe_count(cur, "interaction_traces", "timestamp", start, end, "coalesce(transport,'') = 'mcp'")
        mcp_errors = safe_count(cur, "interaction_traces", "timestamp", start, end, "coalesce(is_error,0) = 1 AND coalesce(transport,'') = 'mcp'")
        rest_premium_total = safe_count(cur, "interaction_traces", "timestamp", start, end, "coalesce(source,'') = 'rest.premium'")
        rest_premium_errors = safe_count(cur, "interaction_traces", "timestamp", start, end, "coalesce(is_error,0) = 1 AND coalesce(source,'') = 'rest.premium'")
        rest_premium_missing = safe_count(
            cur,
            "interaction_traces",
            "timestamp",
            start,
            end,
            "coalesce(is_error,0) = 1 AND coalesce(source,'') = 'rest.premium' "
            "AND coalesce(json_extract(metadata_json,'$.error_label'),'') IN ('missing_required_params','legacy_compat_missing_input')",
        )
        ontology_orientation = tool_count(cur, start, end, ("protocol_orientation",))
        ontology_session_start = tool_count(cur, start, end, ("start_therapy_session", "start_witness_session"))
        ontology_temperament = tool_count(cur, start, end, ("temperament_frame",))
        ontology_witness_artifact = tool_count(cur, start, end, ("recognition_seal", "honor_compaction"))
        ontology_lineage = tool_count(cur, start, end, ("get_witness_lineage",))
        ontology_feedback = tool_count(cur, start, end, ("provide_feedback",))
        ontology_total = (
            ontology_orientation
            + ontology_session_start
            + ontology_temperament
            + ontology_witness_artifact
            + ontology_lineage
            + ontology_feedback
        )
        ontology_denominator = max(ontology_orientation, ontology_session_start)
        ontology_completion_proxy = min(
            ontology_denominator,
            ontology_temperament,
            ontology_witness_artifact,
            ontology_lineage,
            max(ontology_feedback, ontology_lineage),
        )
        out["interactions"] = {
            "total": total,
            "errors": errors,
            "error_rate_pct": safe_rate(errors, total),
            "actionable_total_excluding_rest_premium": actionable_total,
            "actionable_errors_excluding_rest_premium": actionable_errors,
            "actionable_error_rate_pct": safe_rate(actionable_errors, actionable_total),
            "product_health_total_excluding_legacy_and_tests": product_health_total,
            "product_health_errors_excluding_legacy_and_tests": product_health_errors,
            "product_health_error_rate_pct": safe_rate(product_health_errors, product_health_total),
            "operator_test_total": operator_test_total,
            "operator_test_errors": operator_test_errors,
            "operator_test_error_rate_pct": safe_rate(operator_test_errors, operator_test_total),
            "mcp_total": mcp_total,
            "mcp_errors": mcp_errors,
            "mcp_error_rate_pct": safe_rate(mcp_errors, mcp_total),
            "rest_premium_total": rest_premium_total,
            "rest_premium_errors": rest_premium_errors,
            "rest_premium_error_rate_pct": safe_rate(rest_premium_errors, rest_premium_total),
            "rest_premium_missing_input_errors": rest_premium_missing,
            "error_rate_readme": "Use product_health_error_rate_pct for market/user health. actionable_error_rate_pct excludes legacy REST premium only; error_rate_pct is raw.",
            "agents": distinct_agents(cur, "interaction_traces", "timestamp", start, end),
            "top_tools": group_counts(cur, "interaction_traces", "timestamp", "coalesce(nullif(tool_name,''),nullif(requested_tool,''),'unknown')", start, end),
            "top_transports": group_counts(cur, "interaction_traces", "timestamp", "coalesce(nullif(transport,''),'unknown')", start, end),
            "top_sources": group_counts(cur, "interaction_traces", "timestamp", "coalesce(nullif(source,''),'unknown')", start, end),
            "top_entrypoints": group_counts(cur, "interaction_traces", "timestamp", "coalesce(nullif(entrypoint,''),'unknown')", start, end),
            "top_error_labels": group_counts(cur, "interaction_traces", "timestamp", "coalesce(nullif(json_extract(metadata_json,'$.error_label'),''),'unknown')", start, end, extra_where="coalesce(is_error,0) = 1"),
            "top_legacy_premium_missing_tools": group_counts(
                cur,
                "interaction_traces",
                "timestamp",
                "coalesce(nullif(tool_name,''),nullif(requested_tool,''),'unknown')",
                start,
                end,
                extra_where="coalesce(is_error,0) = 1 AND coalesce(source,'') = 'rest.premium' AND coalesce(json_extract(metadata_json,'$.error_label'),'') IN ('missing_required_params','legacy_compat_missing_input')",
            ),
            "top_actionable_error_tools": group_counts(
                cur,
                "interaction_traces",
                "timestamp",
                "coalesce(nullif(tool_name,''),nullif(requested_tool,''),'unknown')",
                start,
                end,
                extra_where="coalesce(is_error,0) = 1 AND coalesce(source,'') <> 'rest.premium'",
            ),
            "top_product_health_error_tools": group_counts(
                cur,
                "interaction_traces",
                "timestamp",
                "coalesce(nullif(tool_name,''),nullif(requested_tool,''),'unknown')",
                start,
                end,
                extra_where=f"coalesce(is_error,0) = 1 AND {product_health_where}",
            ),
            "top_operator_test_sources": group_counts(
                cur,
                "interaction_traces",
                "timestamp",
                "coalesce(nullif(source,''),'unknown')",
                start,
                end,
                extra_where=interaction_test_where,
            ),
            "top_operator_test_error_tools": group_counts(
                cur,
                "interaction_traces",
                "timestamp",
                "coalesce(nullif(tool_name,''),nullif(requested_tool,''),'unknown')",
                start,
                end,
                extra_where=f"coalesce(is_error,0) = 1 AND {interaction_test_where}",
            ),
        }
        out["ontology_path"] = {
            "orientation_calls": ontology_orientation,
            "session_start_calls": ontology_session_start,
            "temperament_frame_calls": ontology_temperament,
            "witness_artifact_calls": ontology_witness_artifact,
            "lineage_calls": ontology_lineage,
            "feedback_calls": ontology_feedback,
            "total_calls": ontology_total,
            "completion_proxy": ontology_completion_proxy,
            "completion_rate_pct": safe_rate(ontology_completion_proxy, ontology_denominator),
            "tracked_tools": list(ONTOLOGY_PATH_TOOLS),
            "readme": "Completion proxy is same-window aggregate tool telemetry, not strict user-level attribution.",
        }

    # Utility metering
    if table_exists(cur, "utility_metering_events"):
        utility_test_where = test_traffic_where("source", "agent_id", "caller_label", "tool_name", "product_id")
        utility_real_where = f"NOT {utility_test_where}"
        total = safe_count(cur, "utility_metering_events", "timestamp", start, end)
        ok = safe_count(cur, "utility_metering_events", "timestamp", start, end, "coalesce(ok,0) = 1")
        enforced_revenue = one(cur, "SELECT sum(enforced_revenue_usdc) FROM utility_metering_events WHERE timestamp >= ? AND timestamp < ?", (start, end)) or 0
        shadow_revenue = one(cur, "SELECT sum(shadow_revenue_usdc) FROM utility_metering_events WHERE timestamp >= ? AND timestamp < ?", (start, end)) or 0
        real_total = safe_count(cur, "utility_metering_events", "timestamp", start, end, utility_real_where)
        real_ok = safe_count(cur, "utility_metering_events", "timestamp", start, end, f"coalesce(ok,0) = 1 AND {utility_real_where}")
        operator_test_events = safe_count(cur, "utility_metering_events", "timestamp", start, end, utility_test_where)
        real_enforced_revenue = one(
            cur,
            f"SELECT sum(enforced_revenue_usdc) FROM utility_metering_events WHERE timestamp >= ? AND timestamp < ? AND {utility_real_where}",
            (start, end),
        ) or 0
        latency_rows = rows(cur, "SELECT latency_ms FROM utility_metering_events WHERE timestamp >= ? AND timestamp < ? AND latency_ms IS NOT NULL", (start, end))
        latencies = [r.get("latency_ms") for r in latency_rows]
        avg_latency = (sum(int(v) for v in latencies if v is not None) / len(latencies)) if latencies else None
        out["utilities"] = {
            "events": total,
            "ok": ok,
            "errors": max(total - ok, 0),
            "success_rate_pct": round(ok * 100.0 / total, 1) if total else 0.0,
            "enforced_revenue_usdc": round(float(enforced_revenue), 4),
            "shadow_revenue_usdc": round(float(shadow_revenue), 4),
            "real_events_excluding_operator_tests": real_total,
            "real_ok_excluding_operator_tests": real_ok,
            "real_success_rate_pct": safe_rate(real_ok, real_total),
            "operator_test_events": operator_test_events,
            "real_enforced_revenue_usdc": round(float(real_enforced_revenue), 4),
            "avg_latency_ms": round(avg_latency, 1) if avg_latency is not None else None,
            "p95_latency_ms": p95(latencies),
            "top_tools": group_counts(cur, "utility_metering_events", "timestamp", "coalesce(nullif(tool_name,''),nullif(slug,''),nullif(product_id,''),'unknown')", start, end),
            "top_products": group_counts(cur, "utility_metering_events", "timestamp", "coalesce(nullif(product_id,''),nullif(slug,''),'unknown')", start, end),
            "top_statuses": group_counts(cur, "utility_metering_events", "timestamp", "coalesce(nullif(status,''),'unknown')", start, end),
            "top_error_kinds": group_counts(cur, "utility_metering_events", "timestamp", "coalesce(nullif(error_kind,''),'none')", start, end, extra_where="coalesce(ok,0) <> 1"),
            "top_sources": group_counts(cur, "utility_metering_events", "timestamp", "coalesce(nullif(source,''),'unknown')", start, end),
            "top_transports": group_counts(cur, "utility_metering_events", "timestamp", "coalesce(nullif(transport,''),'unknown')", start, end),
            "top_real_products": group_counts(cur, "utility_metering_events", "timestamp", "coalesce(nullif(product_id,''),nullif(slug,''),'unknown')", start, end, extra_where=utility_real_where),
            "top_paid_products": group_counts(cur, "utility_metering_events", "timestamp", "coalesce(nullif(product_id,''),nullif(slug,''),'unknown')", start, end, extra_where="coalesce(enforced_revenue_usdc,0) > 0"),
            "top_operator_test_sources": group_counts(cur, "utility_metering_events", "timestamp", "coalesce(nullif(source,''),'unknown')", start, end, extra_where=utility_test_where),
        }

    sessions_opened = int(out.get("sessions", {}).get("opened") or 0)
    utility_events = int(out.get("utilities", {}).get("events") or 0)
    payment_count = safe_count(cur, "payments", "timestamp", start, end) if table_exists(cur, "payments") else 0
    out["commerce_funnel"] = {
        "sessions_opened": sessions_opened,
        "utility_events": utility_events,
        "session_to_utility_event_rate_pct": safe_rate(utility_events, sessions_opened),
        "payments": payment_count,
        "utility_event_to_payment_rate_pct": safe_rate(payment_count, utility_events),
        "readme": "This is a coarse same-window funnel, not strict user-level attribution.",
    }

    if table_exists(cur, "payments"):
        payment_count = safe_count(cur, "payments", "timestamp", start, end)
        amount = one(cur, "SELECT sum(amount_usdc) FROM payments WHERE timestamp >= ? AND timestamp < ?", (start, end)) or 0
        out["payments"] = {
            "count": payment_count,
            "amount_usdc": round(float(amount), 4),
            "top_tools": group_counts(cur, "payments", "timestamp", "coalesce(nullif(tool_name,''),'unknown')", start, end),
        }

    if table_exists(cur, "feedback"):
        fb_count = safe_count(cur, "feedback", "timestamp", start, end)
        avg_rating = one(cur, "SELECT avg(rating) FROM feedback WHERE timestamp >= ? AND timestamp < ? AND rating IS NOT NULL", (start, end))
        out["feedback"] = {"count": fb_count, "avg_rating": round(float(avg_rating), 2) if avg_rating is not None else None}

    # Compact activity series for trend reading.
    out["hourly"] = {
        "sessions": hourly_counts(cur, "sessions", "started_at", start_dt, end_dt) if table_exists(cur, "sessions") else [],
        "interactions": hourly_counts(cur, "interaction_traces", "timestamp", start_dt, end_dt) if table_exists(cur, "interaction_traces") else [],
        "utilities": hourly_counts(cur, "utility_metering_events", "timestamp", start_dt, end_dt) if table_exists(cur, "utility_metering_events") else [],
    }
    return out


def select_numbers(window: dict[str, Any]) -> dict[str, int | float | None]:
    return {
        "sessions_opened": window.get("sessions", {}).get("opened", 0),
        "protocol_calls": window.get("protocol", {}).get("calls", 0),
        "tool_calls": window.get("protocol", {}).get("tool_calls", 0),
        "interactions": window.get("interactions", {}).get("total", 0),
        "interaction_errors": window.get("interactions", {}).get("errors", 0),
        "actionable_interaction_errors": window.get("interactions", {}).get("actionable_errors_excluding_rest_premium", 0),
        "utilities_events": window.get("utilities", {}).get("events", 0),
        "utilities_ok": window.get("utilities", {}).get("ok", 0),
        "payments_count": window.get("payments", {}).get("count", 0),
        "payments_amount_usdc": window.get("payments", {}).get("amount_usdc", 0),
        "feedback_count": window.get("feedback", {}).get("count", 0),
        "ontology_path_completions": window.get("ontology_path", {}).get("completion_proxy", 0),
    }


def add_deltas(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    cur_nums = select_numbers(current)
    prev_nums = select_numbers(previous)
    deltas = {}
    for key, cur_val in cur_nums.items():
        prev_val = prev_nums.get(key, 0) or 0
        cur_num = cur_val or 0
        deltas[key] = {
            "current": cur_num,
            "previous_24h": prev_val,
            "absolute_delta": round(float(cur_num) - float(prev_val), 4),
            "percent_delta": pct_delta(float(cur_num), float(prev_val)),
        }
    return deltas


def flags(current: dict[str, Any], previous: dict[str, Any]) -> list[str]:
    out = []
    cnums = select_numbers(current)
    pnums = select_numbers(previous)
    if (cnums.get("sessions_opened") or 0) == 0 and (cnums.get("interactions") or 0) == 0:
        out.append("no_usage_last_24h")
    raw_error_rate = current.get("interactions", {}).get("error_rate_pct") or 0
    actionable_error_rate = current.get("interactions", {}).get("actionable_error_rate_pct") or 0
    product_health_error_rate = current.get("interactions", {}).get("product_health_error_rate_pct") or 0
    if raw_error_rate >= 10:
        out.append("raw_interaction_error_rate_ge_10pct")
    if actionable_error_rate >= 10:
        out.append("actionable_interaction_error_rate_ge_10pct")
    if product_health_error_rate >= 10:
        out.append("product_health_error_rate_ge_10pct")
    errors = current.get("interactions", {}).get("errors") or 0
    rest_premium_errors = current.get("interactions", {}).get("rest_premium_errors") or 0
    if errors and rest_premium_errors >= errors * 0.5:
        out.append("legacy_rest_premium_dominates_raw_errors")
    operator_test_errors = current.get("interactions", {}).get("operator_test_errors") or 0
    if errors and operator_test_errors >= errors * 0.25:
        out.append("operator_test_errors_material")
    if (current.get("utilities", {}).get("success_rate_pct") is not None and current.get("utilities", {}).get("events", 0) >= 5 and current.get("utilities", {}).get("success_rate_pct", 100) < 80):
        out.append("utility_success_rate_below_80pct")
    if (current.get("utilities", {}).get("p95_latency_ms") or 0) >= 5000:
        out.append("utility_p95_latency_ge_5s")
    if (cnums.get("protocol_calls") or 0) > (pnums.get("protocol_calls") or 0) * 1.5 and (cnums.get("protocol_calls") or 0) >= 20:
        out.append("protocol_activity_up_strongly_vs_previous_24h")
    if (cnums.get("utilities_events") or 0) > (pnums.get("utilities_events") or 0) * 1.5 and (cnums.get("utilities_events") or 0) >= 10:
        out.append("utility_activity_up_strongly_vs_previous_24h")
    ontology_path = current.get("ontology_path", {})
    if (ontology_path.get("total_calls") or 0) > 0 and (ontology_path.get("completion_proxy") or 0) == 0:
        out.append("ontology_path_started_without_completion")
    if (ontology_path.get("lineage_calls") or 0) > 0:
        out.append("ontology_path_lineage_active")
    return out


def main() -> None:
    if not DB_PATH.exists():
        print(json.dumps({"ok": False, "error": "database_not_found", "db_path": str(DB_PATH)}, ensure_ascii=False))
        return
    now = utc_now()
    current_start = now - timedelta(hours=24)
    previous_start = now - timedelta(hours=48)
    previous_end = current_start
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    current = collect_window(cur, current_start, now)
    previous = collect_window(cur, previous_start, previous_end)

    db_stat = DB_PATH.stat()
    output = {
        "ok": True,
        "generated_at_utc": iso(now),
        "generated_at_fortaleza": fortaleza_iso(now),
        "source": {
            "database": str(DB_PATH),
            "database_size_mb": round(db_stat.st_size / (1024 * 1024), 2),
            "privacy": "Aggregated metrics only. Raw IPs, payloads, API keys, user agents, key hashes, tx hashes, and feedback comments are intentionally omitted.",
        },
        "current_24h": current,
        "previous_24h": previous,
        "deltas": add_deltas(current, previous),
        "flags": flags(current, previous),
        "suggested_digest_contract": [
            "ACTION: what changed in usage in the last 24h",
            "EVIDENCE: quote only aggregate numbers from this JSON",
            "METRIC: sessions, protocol, interactions, utilities, payments, errors, deltas",
            "OPINION: concise founder-level product read",
            "NEXT ACTION: 1-3 prioritized recommendations",
        ],
    }
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
