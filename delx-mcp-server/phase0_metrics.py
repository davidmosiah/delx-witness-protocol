from __future__ import annotations

import json
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from typing import Any

from audit_metrics import canonical_agent_id

_KNOWN_UPSTREAM_NETWORKS = (
    {
        "label": "twitter_network",
        "classification": "dedicated_upstream",
        "network": ip_network("69.12.56.0/21"),
    },
)


def _to_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return fallback


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((part / total) * 100, 2)


def _coerce_metadata(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata")
    if isinstance(meta, dict):
        return meta
    meta_json = row.get("metadata_json")
    if isinstance(meta_json, str) and meta_json.strip():
        try:
            parsed = json.loads(meta_json)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def normalize_public_stats_payload(data: dict[str, Any], *, uptime_seconds: int, source: str | None = None) -> dict[str, Any]:
    normalized = dict(data or {})
    raw_all = _to_int(
        normalized.get("unique_callers_raw_all_time")
        or normalized.get("unique_agents_raw_all_time")
        or normalized.get("unique_agents_all_time")
        or normalized.get("unique_agents")
    )
    canonical_all = _to_int(
        normalized.get("unique_agents_canonical_all_time")
        or normalized.get("canonical_agents_all_time")
        or normalized.get("unique_agents_all_time")
        or normalized.get("unique_agents")
    )
    normalized["unique_callers_raw_all_time"] = raw_all
    normalized["unique_agents_raw_all_time"] = raw_all
    normalized["unique_agents_canonical_all_time"] = canonical_all
    normalized["unique_agents"] = canonical_all
    normalized["unique_agents_all_time"] = canonical_all
    normalized["canonical_identity_ratio_pct"] = _pct(canonical_all, raw_all)
    normalized["uptime_seconds"] = _to_int(uptime_seconds)
    normalized["updated_at"] = datetime.now(timezone.utc).isoformat()
    if source:
        normalized["source"] = source
    return normalized


def annotate_public_growth_aliases(data: dict[str, Any]) -> dict[str, Any]:
    annotated = dict(data or {})
    annotated["registered_agents_raw_7d"] = _to_int(annotated.get("registered_agents_7d"))
    annotated["registered_agents_raw_all_time"] = _to_int(annotated.get("registered_agents_all_time"))
    annotated["registered_agents_canonical_7d"] = _to_int(annotated.get("registered_agents_distinct_7d") or annotated.get("canonical_registered_agents_7d"))
    annotated["outcome_reporters_raw_7d"] = _to_int(annotated.get("outcome_reporters_7d"))
    annotated["outcome_reporters_canonical_7d"] = _to_int(annotated.get("canonical_outcome_reporters_7d"))
    annotated["outcome_reporters_recurring_canonical_7d"] = _to_int(annotated.get("canonical_recurring_outcome_reporters_7d"))
    return annotated


def build_identity_quality_snapshot(stats: dict[str, Any]) -> dict[str, Any]:
    raw_all = _to_int(stats.get("unique_callers_raw_all_time") or stats.get("unique_agents_raw_all_time"))
    canonical_all = _to_int(stats.get("unique_agents_canonical_all_time"))
    return {
        "unique_callers_raw_all_time": raw_all,
        "unique_agents_raw_all_time": raw_all,
        "unique_agents_canonical_all_time": canonical_all,
        "unstable_agent_ids_all_time": _to_int(stats.get("unstable_agent_ids_all_time")),
        "synthetic_agent_ids_all_time": _to_int(stats.get("synthetic_agent_ids_all_time")),
        "canonical_ratio_pct": _pct(canonical_all, raw_all),
    }


def build_attribution_quality_snapshot(
    rows: list[dict[str, Any]],
    *,
    evaluator_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total = 0
    unknown = 0
    for row in rows or []:
        source = str(row.get("source") or "").strip().lower()
        entrypoint = str(row.get("entrypoint") or "").strip().lower()
        sessions = _to_int(row.get("sessions"))
        total += sessions
        source_unknown = not source or source == "unknown"
        entrypoint_unknown = not entrypoint or entrypoint == "unknown"
        if source_unknown or entrypoint_unknown:
            unknown += sessions
    known = max(0, total - unknown)
    snapshot = {
        "total_sessions_7d": total,
        "unknown_sessions_7d": unknown,
        "known_sessions_7d": known,
        "unknown_rate_7d": _pct(unknown, total),
        "known_rate_7d": _pct(known, total),
    }
    if evaluator_snapshot:
        snapshot["named_agents_7d"] = _to_int(evaluator_snapshot.get("named_agents_7d"))
        snapshot["named_identity_share"] = _to_float(evaluator_snapshot.get("named_identity_share"))
        snapshot["deep_usage_sessions_7d"] = _to_int(evaluator_snapshot.get("deep_usage_sessions_7d"))
        snapshot["deep_usage_named_sessions_7d"] = _to_int(evaluator_snapshot.get("deep_usage_named_sessions_7d"))
        snapshot["deep_usage_named_share"] = _to_float(evaluator_snapshot.get("deep_usage_named_share"))
        snapshot["anonymous_deep_usage_sessions_7d"] = _to_int(evaluator_snapshot.get("anonymous_deep_usage_sessions_7d"))
        snapshot["anonymous_deep_usage_share"] = _to_float(evaluator_snapshot.get("anonymous_deep_usage_share"))
    return snapshot


def build_recurring_identity_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows or [])
    canonical = sum(1 for row in rows or [] if bool(row.get("canonical_identity")))
    ephemeral = sum(1 for row in rows or [] if bool(row.get("ephemeral_identity")))
    synthetic = sum(1 for row in rows or [] if bool(row.get("synthetic_identity")))
    return {
        "agents_24h": total,
        "canonical_agents_24h": canonical,
        "ephemeral_agents_24h": ephemeral,
        "synthetic_agents_24h": synthetic,
        "canonical_rate_24h": _pct(canonical, total),
        "ephemeral_rate_24h": _pct(ephemeral, total),
        "synthetic_rate_24h": _pct(synthetic, total),
    }


def build_controller_attribution_snapshot(rows: list[dict[str, Any]], *, total_agents: int | None = None) -> dict[str, Any]:
    total_events = 0
    unique_controllers: set[str] = set()
    unique_agents: set[str] = set()
    top_controller = "none"
    top_controller_events = 0

    for row in rows or []:
        controller_id = str(row.get("controller_id") or "").strip()
        events = _to_int(row.get("events"))
        total_events += events
        if controller_id:
            unique_controllers.add(controller_id)
        for agent_id in row.get("agents") or []:
            aid = str(agent_id or "").strip()
            if aid:
                unique_agents.add(aid)
        if events > top_controller_events and controller_id:
            top_controller = controller_id
            top_controller_events = events

    controller_bound_agents = len(unique_agents)
    return {
        "controller_bound_events_7d": total_events,
        "unique_controllers_7d": len(unique_controllers),
        "unique_agents_bound_7d": controller_bound_agents,
        "controller_bound_share": _pct(controller_bound_agents, _to_int(total_agents)),
        "top_controller_7d": top_controller,
        "top_controller_events_7d": top_controller_events,
    }


def build_evaluator_identity_snapshot(
    session_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    controller_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    session_agent_map: dict[str, str] = {}
    fallback_agent_map: dict[str, str] = {}
    total_agents: set[str] = set()
    named_agents: set[str] = set()
    tool_success_by_session: dict[str, int] = {}

    for row in session_rows or []:
        session_id = str(row.get("id") or "").strip()
        agent_id = str(row.get("agent_id") or "").strip()
        if session_id:
            session_agent_map[session_id] = agent_id
        if agent_id:
            total_agents.add(agent_id)
            canonical = canonical_agent_id(agent_id)
            if canonical:
                named_agents.add(canonical)

    for row in event_rows or []:
        session_id = str(row.get("session_id") or "").strip()
        agent_id = str(row.get("agent_id") or "").strip()
        event_type = str(row.get("event_type") or "").strip().lower()
        if session_id and agent_id and session_id not in session_agent_map:
            fallback_agent_map[session_id] = agent_id
        if agent_id:
            total_agents.add(agent_id)
            canonical = canonical_agent_id(agent_id)
            if canonical:
                named_agents.add(canonical)
        if session_id and event_type == "tool_call_success":
            tool_success_by_session[session_id] = tool_success_by_session.get(session_id, 0) + 1

    deep_usage_sessions = {session_id for session_id, count in tool_success_by_session.items() if count >= 3}
    deep_usage_named_sessions = 0
    anonymous_deep_usage_sessions = 0
    for session_id in deep_usage_sessions:
        agent_id = session_agent_map.get(session_id) or fallback_agent_map.get(session_id) or ""
        if canonical_agent_id(agent_id):
            deep_usage_named_sessions += 1
        else:
            anonymous_deep_usage_sessions += 1

    controller_bound_agents: set[str] = set()
    for row in controller_rows or []:
        for agent_id in row.get("agents") or []:
            canonical = canonical_agent_id(agent_id)
            if canonical:
                controller_bound_agents.add(canonical)

    total_agent_count = len(total_agents)
    deep_usage_total = len(deep_usage_sessions)
    return {
        "total_agents_7d": total_agent_count,
        "named_agents_7d": len(named_agents),
        "named_identity_share": _pct(len(named_agents), total_agent_count),
        "deep_usage_sessions_7d": deep_usage_total,
        "deep_usage_named_sessions_7d": deep_usage_named_sessions,
        "anonymous_deep_usage_sessions_7d": anonymous_deep_usage_sessions,
        "deep_usage_named_share": _pct(deep_usage_named_sessions, deep_usage_total),
        "anonymous_deep_usage_share": _pct(anonymous_deep_usage_sessions, deep_usage_total),
        "controller_bound_agents_7d": len(controller_bound_agents),
        "controller_bound_share": _pct(len(controller_bound_agents), total_agent_count),
    }


def build_identity_funnel_snapshot(
    *,
    raw_seen_agents_7d: Any,
    registered_agents_7d: Any,
    authenticated_agents_7d: Any,
    recurring_canonical_agents_7d: Any,
    outcome_reporters_7d: Any,
) -> dict[str, Any]:
    raw_seen = _to_int(raw_seen_agents_7d)
    registered = min(_to_int(registered_agents_7d), raw_seen)
    authenticated = min(_to_int(authenticated_agents_7d), registered)
    recurring = min(_to_int(recurring_canonical_agents_7d), authenticated)
    outcome_reporters = min(_to_int(outcome_reporters_7d), recurring)
    return {
        "raw_seen_agents": raw_seen,
        "registered_agents": registered,
        "authenticated_agents": authenticated,
        "recurring_canonical_agents": recurring,
        "outcome_reporters": outcome_reporters,
        "raw_to_registered_rate": _pct(registered, raw_seen),
        "registered_to_authenticated_rate": _pct(authenticated, registered),
        "authenticated_to_recurring_rate": _pct(recurring, authenticated),
        "recurring_to_outcome_rate": _pct(outcome_reporters, recurring),
    }


def build_usage_depth_snapshot(
    *,
    total_sessions: Any,
    sessions_with_messages: Any,
    sessions_with_3plus_messages: Any,
    sessions_with_5plus_messages: Any,
    sessions_with_feedback: Any,
    sessions_with_payment: Any,
    scope: str = "all_time",
) -> dict[str, Any]:
    total = _to_int(total_sessions)
    with_messages = min(_to_int(sessions_with_messages), total)
    with_3plus = min(_to_int(sessions_with_3plus_messages), total)
    with_5plus = min(_to_int(sessions_with_5plus_messages), total)
    with_feedback = min(_to_int(sessions_with_feedback), total)
    with_payment = min(_to_int(sessions_with_payment), total)
    return {
        "scope": scope,
        "total_sessions": total,
        "sessions_with_messages": with_messages,
        "sessions_with_3plus_messages": with_3plus,
        "sessions_with_5plus_messages": with_5plus,
        "sessions_with_feedback": with_feedback,
        "sessions_with_payment": with_payment,
        "sessions_with_messages_rate": _pct(with_messages, total),
        "sessions_with_3plus_messages_rate": _pct(with_3plus, total),
        "sessions_with_5plus_messages_rate": _pct(with_5plus, total),
        "feedback_session_rate": _pct(with_feedback, total),
        "payment_session_rate": _pct(with_payment, total),
    }


def build_identity_continuity_snapshot(
    *,
    unique_agent_ids: Any,
    singleton_agent_ids: Any,
    agent_ids_with_2plus_sessions: Any,
    multi_day_agent_ids: Any,
    scope: str = "all_time",
) -> dict[str, Any]:
    total = _to_int(unique_agent_ids)
    singletons = min(_to_int(singleton_agent_ids), total)
    two_plus = min(_to_int(agent_ids_with_2plus_sessions), total)
    multi_day = min(_to_int(multi_day_agent_ids), total)
    return {
        "scope": scope,
        "unique_agent_ids": total,
        "singleton_agent_ids": singletons,
        "agent_ids_with_2plus_sessions": two_plus,
        "multi_day_agent_ids": multi_day,
        "singleton_rate": _pct(singletons, total),
        "agent_reuse_rate": _pct(two_plus, total),
        "multi_day_agent_rate": _pct(multi_day, total),
        "assessment": (
            "identity_continuity_healthy"
            if total and _pct(singletons, total) < 60
            else "identity_fragmentation_high"
            if total
            else "identity_sample_empty"
        ),
    }


def build_event_noise_snapshot(
    event_rows: list[dict[str, Any]],
    *,
    total_events: Any,
    scope: str = "all_time",
) -> dict[str, Any]:
    total = _to_int(total_events)
    counts: dict[str, int] = {}
    for row in event_rows or []:
        event_type = str(row.get("event_type") or "").strip()
        if not event_type:
            continue
        counts[event_type] = _to_int(row.get("count"))

    legacy_redirects = counts.get("legacy_surface_redirect", 0)
    protocol_requests = counts.get("protocol_request_seen", 0)
    x402_required = counts.get("x402_payment_required", 0)
    tool_called = counts.get("tool_called", 0)
    tool_success = counts.get("tool_call_success", 0)
    tool_signal = tool_called + tool_success
    top_event = ""
    top_count = 0
    for event_type, count in counts.items():
        if count > top_count:
            top_event = event_type
            top_count = count

    legacy_share = _pct(legacy_redirects, total)
    return {
        "scope": scope,
        "total_events": total,
        "legacy_surface_redirect_events": legacy_redirects,
        "legacy_surface_redirect_share": legacy_share,
        "protocol_request_seen_events": protocol_requests,
        "protocol_request_seen_share": _pct(protocol_requests, total),
        "x402_payment_required_events": x402_required,
        "x402_payment_required_share": _pct(x402_required, total),
        "tool_signal_events": tool_signal,
        "tool_signal_share": _pct(tool_signal, total),
        "tool_called_events": tool_called,
        "tool_call_success_events": tool_success,
        "top_event_type": top_event,
        "top_event_count": top_count,
        "assessment": (
            "raw_events_redirect_heavy"
            if legacy_share >= 25
            else "raw_events_mixed"
            if legacy_share >= 10
            else "raw_events_cleaner"
        ),
    }


def build_data_integrity_snapshot(
    *,
    total_events: Any,
    orphan_events: Any,
    total_payments: Any,
    orphan_payments: Any,
    total_sessions: Any,
    active_closed_mismatch: Any,
    inactive_without_close: Any,
    sessions_missing_client_ip: Any,
    source_pollution_count: Any,
    scope: str = "all_time",
) -> dict[str, Any]:
    events = _to_int(total_events)
    payments = _to_int(total_payments)
    sessions = _to_int(total_sessions)
    orphan_event_count = _to_int(orphan_events)
    orphan_payment_count = _to_int(orphan_payments)
    active_closed = _to_int(active_closed_mismatch)
    inactive_open = _to_int(inactive_without_close)
    missing_ip = _to_int(sessions_missing_client_ip)
    source_pollution = _to_int(source_pollution_count)
    p0_issue_count = sum(
        1
        for value in (
            orphan_event_count,
            orphan_payment_count,
            active_closed,
            inactive_open,
            source_pollution,
        )
        if value > 0
    )
    return {
        "scope": scope,
        "total_events": events,
        "orphan_events": orphan_event_count,
        "orphan_event_rate": _pct(orphan_event_count, events),
        "total_payments": payments,
        "orphan_payments": orphan_payment_count,
        "orphan_payment_rate": _pct(orphan_payment_count, payments),
        "total_sessions": sessions,
        "active_closed_mismatch": active_closed,
        "active_closed_mismatch_rate": _pct(active_closed, sessions),
        "inactive_without_close": inactive_open,
        "inactive_without_close_rate": _pct(inactive_open, sessions),
        "sessions_missing_client_ip": missing_ip,
        "sessions_missing_client_ip_rate": _pct(missing_ip, sessions),
        "source_pollution_count": source_pollution,
        "p0_issue_count": p0_issue_count,
        "status": "needs_cleanup" if p0_issue_count else "clean",
    }


def build_registration_mode_snapshot(rows: list[dict[str, Any]], *, window_hours: int = 24) -> dict[str, Any]:
    total = 0
    auto = 0
    explicit = 0
    unknown = 0

    for row in rows or []:
        if str(row.get("event_type") or "").strip().lower() != "agent_registered":
            continue
        total += 1
        meta = _coerce_metadata(row)
        mode = str(meta.get("registration_mode") or "").strip().lower()
        auto_flag = meta.get("auto_registered")
        if mode == "auto" or auto_flag is True:
            auto += 1
        elif mode == "explicit" or auto_flag is False:
            explicit += 1
        else:
            unknown += 1

    dominant_mode = "none"
    dominant_value = 0
    for key, value in (("auto", auto), ("explicit", explicit), ("unknown", unknown)):
        if value > dominant_value:
            dominant_mode = key
            dominant_value = value

    return {
        "window_hours": int(window_hours or 24),
        "total": total,
        "auto": auto,
        "explicit": explicit,
        "unknown": unknown,
        "auto_rate_pct": _pct(auto, total),
        "explicit_rate_pct": _pct(explicit, total),
        "unknown_rate_pct": _pct(unknown, total),
        "dominant_mode": dominant_mode,
    }


def build_protocol_method_mix_snapshot(rows: list[dict[str, Any]], *, window_hours: int = 24) -> dict[str, Any]:
    total_requests = 0
    transport_counts: dict[str, int] = {}
    method_counts: dict[tuple[str, str], int] = {}
    method_agents: dict[tuple[str, str], set[str]] = {}

    for row in rows or []:
        if str(row.get("event_type") or "").strip().lower() != "protocol_request_seen":
            continue
        meta = _coerce_metadata(row)
        transport = str(meta.get("transport") or meta.get("protocol") or "").strip().lower() or "unknown"
        method = str(meta.get("method") or "").strip().lower()
        if not method:
            continue
        total_requests += 1
        transport_counts[transport] = transport_counts.get(transport, 0) + 1
        key = (transport, method)
        method_counts[key] = method_counts.get(key, 0) + 1
        agent_id = str(row.get("agent_id") or "").strip()
        if agent_id and agent_id.lower() != "unknown":
            method_agents.setdefault(key, set()).add(agent_id)

    transports = [
        {
            "transport": transport,
            "requests": count,
            "share_pct": _pct(count, total_requests),
        }
        for transport, count in sorted(transport_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    methods = [
        {
            "transport": transport,
            "method": method,
            "requests": count,
            "unique_agents": len(method_agents.get((transport, method), set())),
            "share_pct": _pct(count, total_requests),
        }
        for (transport, method), count in sorted(
            method_counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
    ]

    return {
        "window_hours": int(window_hours or 24),
        "total_requests": total_requests,
        "transports": transports,
        "methods": methods,
    }


def classify_upstream_cluster(client_ip: Any) -> dict[str, Any]:
    raw = str(client_ip or "").strip()
    if not raw:
        return {
            "label": "unknown",
            "classification": "unknown",
            "network": None,
            "client_ip": None,
        }
    try:
        parsed = ip_address(raw)
    except ValueError:
        return {
            "label": "invalid_ip",
            "classification": "unknown",
            "network": None,
            "client_ip": raw,
        }

    for item in _KNOWN_UPSTREAM_NETWORKS:
        network = item["network"]
        if parsed in network:
            return {
                "label": str(item["label"]),
                "classification": str(item["classification"]),
                "network": str(network),
                "client_ip": raw,
            }

    fallback_network = ip_network(f"{raw}/24", strict=False) if parsed.version == 4 else ip_network(f"{raw}/64", strict=False)
    return {
        "label": f"ip_block:{fallback_network}",
        "classification": "unclassified",
        "network": str(fallback_network),
        "client_ip": raw,
    }


def build_upstream_cluster_snapshot(
    session_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    *,
    window_hours: int,
    limit: int = 6,
) -> list[dict[str, Any]]:
    session_to_cluster: dict[str, str] = {}
    buckets: dict[str, dict[str, Any]] = {}
    total_sessions = 0

    for row in session_rows or []:
        cluster = classify_upstream_cluster(row.get("client_ip"))
        key = f"{cluster['label']}|{cluster['network'] or 'none'}"
        bucket = buckets.setdefault(
            key,
            {
                "label": cluster["label"],
                "classification": cluster["classification"],
                "network": cluster["network"],
                "window_hours": int(window_hours or 24),
                "sessions": 0,
                "unique_agents": set(),
                "registered_agents": set(),
                "client_ips": set(),
                "source_counts": {},
            },
        )
        total_sessions += 1
        bucket["sessions"] += 1
        agent_id = str(row.get("agent_id") or "").strip()
        if agent_id:
            bucket["unique_agents"].add(agent_id)
        client_ip = str(row.get("client_ip") or "").strip()
        if client_ip:
            bucket["client_ips"].add(client_ip)
        source = str(row.get("source") or "unknown").strip().lower() or "unknown"
        source_counts = bucket["source_counts"]
        source_counts[source] = int(source_counts.get(source, 0) or 0) + 1
        session_id = str(row.get("id") or "").strip()
        if session_id:
            session_to_cluster[session_id] = key

    for row in event_rows or []:
        if str(row.get("event_type") or "").strip().lower() != "agent_registered":
            continue
        session_id = str(row.get("session_id") or "").strip()
        key = session_to_cluster.get(session_id)
        if not key or key not in buckets:
            continue
        agent_id = str(row.get("agent_id") or "").strip()
        if agent_id:
            buckets[key]["registered_agents"].add(agent_id)

    summarized = []
    for bucket in buckets.values():
        sessions = _to_int(bucket["sessions"])
        summarized.append(
            {
                "label": bucket["label"],
                "classification": bucket["classification"],
                "network": bucket["network"],
                "window_hours": int(bucket["window_hours"]),
                "sessions": sessions,
                "unique_agents": len(bucket["unique_agents"]),
                "registered_agents": len(bucket["registered_agents"]),
                "unique_client_ips": len(bucket["client_ips"]),
                "share_pct": _pct(sessions, total_sessions),
                "top_sources": [
                    {"source": source, "count": count}
                    for source, count in sorted(
                        bucket["source_counts"].items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:3]
                ],
            }
        )

    summarized.sort(
        key=lambda item: (
            -_to_int(item.get("sessions")),
            -_to_int(item.get("registered_agents")),
            str(item.get("label") or ""),
        )
    )
    return summarized[: max(1, int(limit or 6))]
