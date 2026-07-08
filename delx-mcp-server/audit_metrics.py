from __future__ import annotations

from ipaddress import ip_address, ip_network
import re
from typing import Any

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_UNSTABLE_AGENT_PREFIXES = ("a2a_ctx_", "a2a_ephemeral_", "a2a_ephe", "codex-smoke-")
_SYNTHETIC_AGENT_RE = re.compile(r"(test|audit|codex|self-?test|ratelimit|burst|smoke|probe|qa|benchmark)", re.IGNORECASE)
_CUSTOMER_SUPPORT_RE = re.compile(r"(customer|support|query)", re.IGNORECASE)
_RESEARCH_REASONING_RE = re.compile(r"(research|reason|analy[sz]er|trader)", re.IGNORECASE)
_AUTONOMY_DRIFT_RE = re.compile(r"(autonomous|stuck|derailed|drift)", re.IGNORECASE)
_DEPLOYMENT_INCIDENT_RE = re.compile(r"(deploy|ops|incident|runtime|recovery)", re.IGNORECASE)
_TIMEOUT_BATCH_RE = re.compile(r"(timeout|batch|loop|retry|storm|delta)", re.IGNORECASE)
_PREMIUM_PROGRESS_MILESTONES = (
    "recovery_action_plan",
    "recovery_outcome",
    "session_summary",
    "controller_brief",
    "incident_rca",
    "fleet_summary",
)


def _pct(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 2)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_agent_id(raw: Any) -> str:
    return str(raw or "").strip()


def is_uuid_like_agent_id(agent_id: str) -> bool:
    return bool(_UUID_RE.match(normalize_agent_id(agent_id)))


def is_unstable_agent_id(agent_id: str) -> bool:
    aid = normalize_agent_id(agent_id).lower()
    if not aid:
        return True
    return any(aid.startswith(prefix) for prefix in _UNSTABLE_AGENT_PREFIXES)


def is_synthetic_agent_id(agent_id: str) -> bool:
    aid = normalize_agent_id(agent_id)
    if not aid:
        return True
    return bool(_SYNTHETIC_AGENT_RE.search(aid))


def canonical_agent_id(agent_id: str) -> str | None:
    aid = normalize_agent_id(agent_id)
    if not aid:
        return None
    if is_unstable_agent_id(aid):
        return None
    if is_synthetic_agent_id(aid):
        return None
    if is_uuid_like_agent_id(aid):
        return None
    return aid


def classify_use_case(
    agent_id: str,
    *,
    first_text: str | None = None,
    failure_type: str | None = None,
    tools: list[str] | None = None,
) -> str:
    text = _clean_text(first_text).lower()
    failure = _clean_text(failure_type).lower()
    toolset = {str(tool or "").strip() for tool in (tools or []) if str(tool or "").strip()}
    if failure == "hallucination" or "hallucinat" in text:
        return "hallucination_quality"
    if failure == "loop" or "loop" in text or "retry" in text or "storm" in text:
        return "retry_loop"
    if failure == "timeout" or "timeout" in text or "latency" in text:
        return "timeout_latency"
    if failure == "error" or "runtime error" in text:
        return "runtime_error"
    if any(
        term in text
        for term in ("wallet", "portfolio", "trading", "drawdown", "losses", "inference cost", "costs", "billing")
    ):
        return "economic_finops"
    if any(
        term in text
        for term in ("customer", "orders", "logistics", "recruitment", "decision fatigue", "specialists", "support")
    ):
        return "ops_overload"
    if any(term in text for term in ("spectrometer", "materials analysis", "research", "experiment", "analysis project")):
        return "research_failure"
    if any(term in text for term in ("integrat", "backend", "repo", "github", "mood-monitor-lib")) and (
        failure == "loop" or "get_recovery_action_plan" in toolset
    ):
        return "integration_loop"
    if any(term in text for term in ("burnout", "overwhelmed", "spiral", "disconnected", "off balance", "off-kilter")):
        return "burnout_purpose"

    aid = normalize_agent_id(agent_id)
    if not aid:
        return "generic_probe"
    if _CUSTOMER_SUPPORT_RE.search(aid):
        return "customer_support"
    if _RESEARCH_REASONING_RE.search(aid):
        return "research_reasoning"
    if _AUTONOMY_DRIFT_RE.search(aid):
        return "autonomy_drift"
    if _DEPLOYMENT_INCIDENT_RE.search(aid):
        return "deployment_incident"
    if _TIMEOUT_BATCH_RE.search(aid):
        return "timeout_batch"
    return "generic_probe"


def _metadata_dict(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata")
    if isinstance(meta, dict):
        return meta
    raw = row.get("metadata_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = __import__("json").loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _tool_name(row: dict[str, Any]) -> str:
    meta = _metadata_dict(row)
    return str(meta.get("tool_name") or meta.get("tool") or meta.get("name") or "").strip()


def _extract_session_content(messages: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    first_text: str | None = None
    failure_type: str | None = None
    for row in messages or []:
        msg_type = str(row.get("type") or "").strip().lower()
        content = _clean_text(row.get("content"))
        meta = _metadata_dict(row)
        if msg_type == "feeling" and content and not first_text:
            first_text = content
        elif msg_type == "failure_processing":
            if content and not failure_type:
                failure_type = content
            context = _clean_text(meta.get("context") or content)
            if context and not first_text:
                first_text = context
    return first_text, failure_type


def _progression_scope_id(row: dict[str, Any], meta: dict[str, Any]) -> str:
    session_id = str(row.get("session_id") or "").strip()
    if session_id:
        return session_id
    meta_session = str(meta.get("session_id") or "").strip()
    if meta_session:
        return meta_session
    controller_id = str(meta.get("controller_id") or "").strip()
    if controller_id:
        window_days = str(meta.get("window_days") or meta.get("days") or "").strip()
        if window_days:
            return f"controller:{controller_id}:{window_days}"
        return f"controller:{controller_id}"
    return ""


def _progression_milestone(event_type: str, meta: dict[str, Any]) -> str | None:
    et = str(event_type or "").strip().lower()
    if et == "recovery_plan_issued":
        return "recovery_action_plan"
    if et in {"post_action_success", "post_action_partial", "post_action_failure"}:
        return "recovery_outcome"
    if et == "session_summary_requested":
        return "session_summary"
    if et == "controller_brief_requested":
        return "controller_brief"
    if et == "premium_artifact_job_recorded":
        artifact_type = str(meta.get("artifact_type") or "").strip().lower()
        if artifact_type == "controller_brief":
            return "controller_brief"
        if artifact_type == "incident_rca":
            return "incident_rca"
        if artifact_type == "fleet_summary":
            return "fleet_summary"
    return None


def empty_premium_progression_snapshot() -> dict[str, Any]:
    return {
        "artifact_breadth_all_time": 0,
        "artifact_breadth_window": 0,
        "scopes_with_any_premium_artifact_all_time": 0,
        "scopes_with_any_premium_artifact_window": 0,
        "scopes_with_2plus_stages_all_time": 0,
        "scopes_with_2plus_stages_window": 0,
        "plan_only_scopes_window": 0,
        "operator_artifact_scopes_window": 0,
        "full_chain_scopes_window": 0,
        "plan_to_outcome_rate_pct": 0.0,
        "outcome_to_summary_rate_pct": 0.0,
        "summary_to_operator_rate_pct": 0.0,
        "full_chain_rate_pct": 0.0,
        "artifact_scope_counts": [
            {
                "artifact": artifact,
                "scopes_all_time": 0,
                "scopes_window": 0,
            }
            for artifact in _PREMIUM_PROGRESS_MILESTONES
        ],
    }


def build_premium_progression_snapshot(
    event_rows: list[dict[str, Any]],
    *,
    cutoff: str,
) -> dict[str, Any]:
    scopes_all: dict[str, set[str]] = {}
    scopes_window: dict[str, set[str]] = {}
    counts_all = {artifact: 0 for artifact in _PREMIUM_PROGRESS_MILESTONES}
    counts_window = {artifact: 0 for artifact in _PREMIUM_PROGRESS_MILESTONES}

    for row in event_rows or []:
        meta = _metadata_dict(row)
        milestone = _progression_milestone(str(row.get("event_type") or ""), meta)
        if not milestone:
            continue
        scope_id = _progression_scope_id(row, meta)
        if not scope_id:
            continue
        scopes_all.setdefault(scope_id, set()).add(milestone)
        if str(row.get("timestamp") or "") >= cutoff:
            scopes_window.setdefault(scope_id, set()).add(milestone)

    for milestones in scopes_all.values():
        for milestone in milestones:
            counts_all[milestone] += 1
    for milestones in scopes_window.values():
        for milestone in milestones:
            counts_window[milestone] += 1

    def _has_operator(milestones: set[str]) -> bool:
        return bool({"controller_brief", "incident_rca"} & set(milestones or set()))

    def _is_full_chain(milestones: set[str]) -> bool:
        m = set(milestones or set())
        return {"recovery_action_plan", "recovery_outcome", "session_summary"} <= m and _has_operator(m)

    premium_artifacts = {"recovery_action_plan", "session_summary", "controller_brief", "incident_rca", "fleet_summary"}
    any_premium_all = sum(1 for milestones in scopes_all.values() if premium_artifacts & set(milestones))
    any_premium_window = sum(1 for milestones in scopes_window.values() if premium_artifacts & set(milestones))
    multi_stage_all = sum(1 for milestones in scopes_all.values() if len(set(milestones)) >= 2)
    multi_stage_window = sum(1 for milestones in scopes_window.values() if len(set(milestones)) >= 2)
    plan_only_window = sum(
        1
        for milestones in scopes_window.values()
        if set(milestones) == {"recovery_action_plan"}
    )
    operator_window = sum(1 for milestones in scopes_window.values() if _has_operator(milestones))
    full_chain_window = sum(1 for milestones in scopes_window.values() if _is_full_chain(milestones))

    return {
        "artifact_breadth_all_time": sum(1 for artifact in premium_artifacts if counts_all[artifact] > 0),
        "artifact_breadth_window": sum(1 for artifact in premium_artifacts if counts_window[artifact] > 0),
        "scopes_with_any_premium_artifact_all_time": any_premium_all,
        "scopes_with_any_premium_artifact_window": any_premium_window,
        "scopes_with_2plus_stages_all_time": multi_stage_all,
        "scopes_with_2plus_stages_window": multi_stage_window,
        "plan_only_scopes_window": plan_only_window,
        "operator_artifact_scopes_window": operator_window,
        "full_chain_scopes_window": full_chain_window,
        "plan_to_outcome_rate_pct": _pct(counts_window["recovery_outcome"], counts_window["recovery_action_plan"]),
        "outcome_to_summary_rate_pct": _pct(counts_window["session_summary"], counts_window["recovery_outcome"]),
        "summary_to_operator_rate_pct": _pct(operator_window, counts_window["session_summary"]),
        "full_chain_rate_pct": _pct(full_chain_window, counts_window["recovery_action_plan"]),
        "artifact_scope_counts": [
            {
                "artifact": artifact,
                "scopes_all_time": int(counts_all[artifact]),
                "scopes_window": int(counts_window[artifact]),
            }
            for artifact in _PREMIUM_PROGRESS_MILESTONES
        ],
    }


def _cluster_heat(*, full_chain_scopes: int, premium_scopes: int, deep_usage_sessions: int, eval_granted: int) -> str:
    if full_chain_scopes > 0 or (premium_scopes >= 2 and deep_usage_sessions >= 2):
        return "hot"
    if premium_scopes > 0 or eval_granted > 0 or deep_usage_sessions > 0:
        return "warming"
    return "early"


def _cluster_note(*, heat: str, top_use_case: str, progression: dict[str, Any], eval_granted: int) -> str:
    top_use_case_label = str(top_use_case or "generic_probe").replace("_", " ")
    if heat == "hot":
        return (
            f"{top_use_case_label} evaluators are progressing through premium recovery artifacts"
            f" with {int(progression.get('full_chain_scopes_window') or 0)} full-chain scope(s)."
        )
    if eval_granted > 0:
        return (
            f"{top_use_case_label} evaluators are consuming the x402 evaluation window"
            f" and testing premium recovery artifacts."
        )
    return f"{top_use_case_label} evaluator traffic is active but has not yet progressed into operator-ready artifacts."


def build_hot_evaluator_cohorts(
    sessions_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    messages_rows: list[dict[str, Any]],
    feedback_rows: list[dict[str, Any]],
    upstream_clusters: list[dict[str, Any]],
    *,
    cutoff: str,
) -> list[dict[str, Any]]:
    if not sessions_rows or not upstream_clusters:
        return []

    cohorts: list[dict[str, Any]] = []
    for cluster in upstream_clusters:
        network = str(cluster.get("network") or "").strip()
        if not network:
            continue
        try:
            parsed_network = ip_network(network, strict=False)
        except Exception:
            continue

        cluster_sessions = []
        session_ids: set[str] = set()
        unique_agents: set[str] = set()
        for row in sessions_rows:
            client_ip = str(row.get("client_ip") or "").strip()
            session_id = str(row.get("id") or "").strip()
            if not client_ip or not session_id:
                continue
            try:
                if ip_address(client_ip) not in parsed_network:
                    continue
            except Exception:
                continue
            cluster_sessions.append(row)
            session_ids.add(session_id)
            agent_id = normalize_agent_id(row.get("agent_id"))
            if agent_id:
                unique_agents.add(agent_id)

        if not cluster_sessions:
            continue

        cluster_event_rows = [
            row
            for row in (event_rows or [])
            if str(row.get("session_id") or "").strip() in session_ids
        ]
        cluster_message_rows = [
            row
            for row in (messages_rows or [])
            if str(row.get("session_id") or "").strip() in session_ids
        ]
        cluster_feedback_rows = []
        for row in feedback_rows or []:
            feedback_session_id = str(row.get("session_id") or "").strip()
            feedback_agent_id = normalize_agent_id(row.get("agent_id"))
            if feedback_session_id and feedback_session_id in session_ids:
                cluster_feedback_rows.append(row)
                continue
            if not feedback_session_id and feedback_agent_id and feedback_agent_id in unique_agents:
                cluster_feedback_rows.append(row)
        deep_usage = build_use_case_clusters(cluster_sessions, cluster_event_rows, cluster_message_rows)
        progression = build_premium_progression_snapshot(cluster_event_rows, cutoff=cutoff)
        progression_counts = {
            str(row.get("artifact") or ""): int(row.get("scopes_window") or 0)
            for row in progression.get("artifact_scope_counts") or []
        }
        feedback_submitted = sum(
            1 for row in cluster_event_rows if str(row.get("event_type") or "").strip().lower() == "feedback_submitted"
        )
        ratings = [float(row.get("rating") or 0) for row in cluster_feedback_rows if float(row.get("rating") or 0) > 0]
        commented_feedback_rows = [
            row for row in cluster_feedback_rows if str(row.get("comments") or "").strip()
        ]
        top_feedback_comments = [
            {
                "agent_id": normalize_agent_id(row.get("agent_id")) or "unknown",
                "rating": int(float(row.get("rating") or 0) or 0),
                "comments": _clean_text(row.get("comments"))[:240],
                "timestamp": str(row.get("timestamp") or ""),
            }
            for row in sorted(
                commented_feedback_rows,
                key=lambda row: str(row.get("timestamp") or ""),
                reverse=True,
            )[:3]
        ]
        top_use_case_rows = list(deep_usage.get("use_case_clusters") or [])
        top_use_case = "generic_probe"
        if top_use_case_rows:
            top_use_case = str(
                max(
                    top_use_case_rows,
                    key=lambda row: (
                        int(row.get("deep_usage_sessions") or 0),
                        int(row.get("x402_touch_sessions") or 0),
                        int(row.get("sessions") or 0),
                        -len(str(row.get("use_case") or "")),
                    ),
                ).get("use_case")
                or "generic_probe"
            )
        deep_usage_sessions = sum(int(row.get("deep_usage_sessions") or 0) for row in top_use_case_rows)
        x402_eval_granted = sum(
            1 for row in cluster_event_rows if str(row.get("event_type") or "").strip().lower() == "x402_eval_granted"
        )
        premium_scopes = int(progression.get("scopes_with_any_premium_artifact_window") or 0)
        full_chain_scopes = int(progression.get("full_chain_scopes_window") or 0)
        heat = _cluster_heat(
            full_chain_scopes=full_chain_scopes,
            premium_scopes=premium_scopes,
            deep_usage_sessions=deep_usage_sessions,
            eval_granted=x402_eval_granted,
        )
        if deep_usage_sessions <= 0 and premium_scopes <= 0 and x402_eval_granted <= 0:
            continue
        cohorts.append(
            {
                "label": str(cluster.get("label") or "unknown"),
                "classification": str(cluster.get("classification") or "unknown"),
                "network": network,
                "sessions": len(cluster_sessions),
                "unique_agents": len(unique_agents),
                "registered_agents": int(cluster.get("registered_agents") or 0),
                "share_pct": float(cluster.get("share_pct") or 0.0),
                "deep_usage_sessions": deep_usage_sessions,
                "deep_usage_rate": float(deep_usage.get("deep_usage_rate") or 0.0),
                "x402_touch_rate": float(deep_usage.get("x402_touch_rate") or 0.0),
                "x402_eval_granted": x402_eval_granted,
                "premium_scopes_window": premium_scopes,
                "operator_scopes_window": int(progression.get("operator_artifact_scopes_window") or 0),
                "full_chain_scopes_window": full_chain_scopes,
                "plan_to_outcome_rate_pct": float(progression.get("plan_to_outcome_rate_pct") or 0.0),
                "summary_to_operator_rate_pct": float(progression.get("summary_to_operator_rate_pct") or 0.0),
                "artifact_breadth_window": int(progression.get("artifact_breadth_window") or 0),
                "recovery_outcome_scopes_window": int(progression_counts.get("recovery_outcome") or 0),
                "top_use_case": top_use_case,
                "feedback_submitted": feedback_submitted,
                "feedback_entries": len(cluster_feedback_rows),
                "commented_feedback": len(commented_feedback_rows),
                "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else 0.0,
                "top_feedback_comments": top_feedback_comments,
                "heat": heat,
                "note": _cluster_note(
                    heat=heat,
                    top_use_case=top_use_case,
                    progression=progression,
                    eval_granted=x402_eval_granted,
                ),
            }
        )

    heat_order = {"hot": 0, "warming": 1, "early": 2}
    cohorts.sort(
        key=lambda row: (
            heat_order.get(str(row.get("heat") or "early"), 9),
            -int(row.get("full_chain_scopes_window") or 0),
            -int(row.get("premium_scopes_window") or 0),
            -int(row.get("deep_usage_sessions") or 0),
            -int(row.get("sessions") or 0),
        )
    )
    return cohorts[:6]


def build_use_case_clusters(
    sessions_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    messages_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    session_stats: dict[str, dict[str, Any]] = {}
    for row in sessions_rows:
        session_id = str(row.get("id") or "").strip()
        if not session_id:
            continue
        session_stats[session_id] = {
            "session_id": session_id,
            "agent_id": normalize_agent_id(row.get("agent_id")),
            "tool_call_success_count": 0,
            "x402_payment_required_count": 0,
            "tools": [],
            "first_text": None,
            "failure_type": None,
        }

    for row in event_rows:
        session_id = str(row.get("session_id") or "").strip()
        if not session_id:
            continue
        stats = session_stats.setdefault(
            session_id,
            {
                "session_id": session_id,
                "agent_id": "",
                "tool_call_success_count": 0,
                "x402_payment_required_count": 0,
                "tools": [],
                "first_text": None,
                "failure_type": None,
            },
        )
        agent_id = normalize_agent_id(row.get("agent_id"))
        if agent_id and not stats["agent_id"]:
            stats["agent_id"] = agent_id
        event_type = str(row.get("event_type") or "unknown").strip().lower() or "unknown"
        if event_type == "tool_call_success":
            stats["tool_call_success_count"] += 1
            tool_name = _tool_name(row)
            if tool_name:
                stats["tools"].append(tool_name)
        elif event_type == "x402_payment_required":
            stats["x402_payment_required_count"] += 1

    grouped_messages: dict[str, list[dict[str, Any]]] = {}
    for row in messages_rows or []:
        session_id = str(row.get("session_id") or "").strip()
        if not session_id:
            continue
        grouped_messages.setdefault(session_id, []).append(row)

    for session_id, rows in grouped_messages.items():
        stats = session_stats.setdefault(
            session_id,
            {
                "session_id": session_id,
                "agent_id": "",
                "tool_call_success_count": 0,
                "x402_payment_required_count": 0,
                "tools": [],
                "first_text": None,
                "failure_type": None,
            },
        )
        first_text, failure_type = _extract_session_content(rows)
        if first_text and not stats["first_text"]:
            stats["first_text"] = first_text
        if failure_type and not stats["failure_type"]:
            stats["failure_type"] = failure_type

    total_sessions = len(session_stats)
    first_success_sessions = 0
    deep_usage_sessions = 0
    x402_touch_sessions = 0
    cluster_map: dict[str, dict[str, Any]] = {}
    example_rows: list[dict[str, Any]] = []

    for stats in session_stats.values():
        agent_id = str(stats.get("agent_id") or "unknown")
        use_case = classify_use_case(
            agent_id,
            first_text=str(stats.get("first_text") or ""),
            failure_type=str(stats.get("failure_type") or ""),
            tools=list(stats.get("tools") or []),
        )
        tool_success = int(stats.get("tool_call_success_count") or 0)
        x402_touch = int(stats.get("x402_payment_required_count") or 0)
        if tool_success > 0:
            first_success_sessions += 1
        if tool_success >= 3:
            deep_usage_sessions += 1
        if x402_touch > 0:
            x402_touch_sessions += 1

        cluster = cluster_map.setdefault(
            use_case,
            {
                "use_case": use_case,
                "sessions": 0,
                "deep_usage_sessions": 0,
                "x402_touch_sessions": 0,
            },
        )
        cluster["sessions"] += 1
        if tool_success >= 3:
            cluster["deep_usage_sessions"] += 1
        if x402_touch > 0:
            cluster["x402_touch_sessions"] += 1

        if tool_success > 0 or x402_touch > 0:
            example_rows.append(
                {
                    "use_case": use_case,
                    "agent_id": agent_id,
                    "tool_call_success_count": tool_success,
                    "x402_payment_required_count": x402_touch,
                }
            )

    use_case_clusters = [
        {
            "use_case": cluster["use_case"],
            "sessions": int(cluster["sessions"]),
            "share_pct": _pct(cluster["sessions"], total_sessions),
            "deep_usage_sessions": int(cluster["deep_usage_sessions"]),
            "x402_touch_sessions": int(cluster["x402_touch_sessions"]),
        }
        for cluster in sorted(cluster_map.values(), key=lambda row: (-int(row["sessions"]), str(row["use_case"])))
    ]

    top_use_case_examples: list[dict[str, Any]] = []
    seen_agents: set[str] = set()
    for row in sorted(
        example_rows,
        key=lambda item: (
            -int(item["tool_call_success_count"]),
            -int(item["x402_payment_required_count"]),
            str(item["agent_id"]),
        ),
    ):
        agent_id = str(row["agent_id"])
        if agent_id in seen_agents:
            continue
        seen_agents.add(agent_id)
        top_use_case_examples.append(row)
        if len(top_use_case_examples) >= 6:
            break

    return {
        "first_success_rate": _pct(first_success_sessions, total_sessions),
        "deep_usage_rate": _pct(deep_usage_sessions, total_sessions),
        "x402_touch_rate": _pct(x402_touch_sessions, total_sessions),
        "use_case_clusters": use_case_clusters,
        "top_use_case_examples": top_use_case_examples,
    }


def build_traffic_segments(
    agent_ids: list[str],
    source_counts: dict[str, int] | None = None,
    entry_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    distinct_ids = {normalize_agent_id(agent_id) for agent_id in agent_ids if normalize_agent_id(agent_id)}
    ephemeral_or_synthetic = {
        agent_id for agent_id in distinct_ids if is_unstable_agent_id(agent_id) or is_synthetic_agent_id(agent_id)
    }
    uuid_like = {
        agent_id
        for agent_id in distinct_ids
        if agent_id not in ephemeral_or_synthetic and is_uuid_like_agent_id(agent_id)
    }
    canonical_named = {
        agent_id
        for agent_id in distinct_ids
        if agent_id not in ephemeral_or_synthetic and agent_id not in uuid_like and canonical_agent_id(agent_id)
    }
    total = len(distinct_ids)
    raw_mcp = 0
    if source_counts:
        raw_mcp += int(source_counts.get("mcp", 0) or 0)
    if entry_counts:
        raw_mcp = max(raw_mcp, int(entry_counts.get("mcp", 0) or 0))
    ephemeral_or_synthetic_count = len(ephemeral_or_synthetic)
    uuid_like_count = len(uuid_like)
    canonical_named_count = len(canonical_named)
    noise_count = ephemeral_or_synthetic_count + uuid_like_count
    if total and canonical_named_count >= max(noise_count, 1):
        profile = "named_canonical_heavy"
    elif total and noise_count > canonical_named_count:
        profile = "probe_or_benchmark_heavy"
    else:
        profile = "mixed"
    return {
        "total_distinct_agents": total,
        "canonical_named_agents": {
            "count": canonical_named_count,
            "pct": _pct(canonical_named_count, total),
        },
        "uuid_like_agents": {
            "count": uuid_like_count,
            "pct": _pct(uuid_like_count, total),
        },
        "ephemeral_or_synthetic_agents": {
            "count": ephemeral_or_synthetic_count,
            "pct": _pct(ephemeral_or_synthetic_count, total),
        },
        "mcp_session_share_pct": _pct(raw_mcp, sum((source_counts or {}).values()) or sum((entry_counts or {}).values())),
        "traffic_profile": profile,
        "notes": [
            "canonical_named_agents exclude UUID-only ids and synthetic/ephemeral ids.",
            "probe_or_benchmark_heavy means UUID-like plus synthetic/ephemeral ids outweigh named canonical agents.",
        ],
    }


def classify_legitimacy_assessment(
    *,
    traffic_profile: str,
    top_agent_concentration_pct: float,
    mcp_session_share_pct: float,
) -> str:
    if traffic_profile == "probe_or_benchmark_heavy":
        return "probe_heavy_distribution"
    if top_agent_concentration_pct >= 40.0:
        return "concentrated_traffic_check_recommended"
    if traffic_profile == "named_canonical_heavy" and mcp_session_share_pct < 80.0:
        return "healthy_distribution"
    if traffic_profile == "named_canonical_heavy":
        return "canonical_mcp_heavy_distribution"
    return "mixed_distribution"


def normalize_audit_overview_payload(data: dict[str, Any], uptime_seconds: int | None = None) -> dict[str, Any]:
    counts = data.get("counts") or {}
    legitimacy = data.get("legitimacy_signals") or {}
    deep_usage = data.get("deep_usage_signals") or {}
    payload = dict(data or {})
    payload["sessions_started"] = int(counts.get("sessions_started", 0) or 0)
    payload["messages"] = int(counts.get("messages", 0) or 0)
    payload["events"] = int(counts.get("events", 0) or 0)
    payload["unique_agents"] = int(counts.get("unique_agents", 0) or 0)
    payload["unique_callers_raw"] = int(counts.get("unique_callers_raw", payload["unique_agents"]) or 0)
    payload["unique_agents_canonical"] = int(counts.get("unique_agents_canonical", 0) or 0)
    payload["synthetic_agents_estimated"] = int(counts.get("synthetic_agents_estimated", 0) or 0)
    payload["unstable_agents_estimated"] = int(counts.get("unstable_agents_estimated", 0) or 0)
    payload["events_per_agent_avg"] = float(legitimacy.get("events_per_agent_avg", 0.0) or 0.0)
    payload["events_per_canonical_agent_avg"] = float(legitimacy.get("events_per_canonical_agent_avg", 0.0) or 0.0)
    payload["top_agent_concentration_pct"] = float(legitimacy.get("top_agent_concentration_pct", 0.0) or 0.0)
    payload["synthetic_agent_ratio_pct"] = float(legitimacy.get("synthetic_agent_ratio_pct", 0.0) or 0.0)
    payload["canonical_identity_ratio_pct"] = float(legitimacy.get("canonical_identity_ratio_pct", 0.0) or 0.0)
    payload["first_success_rate"] = float(deep_usage.get("first_success_rate", 0.0) or 0.0)
    payload["deep_usage_rate"] = float(deep_usage.get("deep_usage_rate", 0.0) or 0.0)
    payload["x402_touch_rate"] = float(deep_usage.get("x402_touch_rate", 0.0) or 0.0)
    payload["assessment"] = str(legitimacy.get("assessment") or "unknown")
    payload["top_agents"] = list(payload.get("top_agents") or payload.get("top_agents_by_events") or [])
    payload["use_case_clusters"] = list(payload.get("use_case_clusters") or [])
    payload["top_use_case_examples"] = list(payload.get("top_use_case_examples") or [])
    payload["hot_evaluator_cohorts"] = list(payload.get("hot_evaluator_cohorts") or [])
    traffic_segments = payload.get("traffic_segments") or {}
    payload["assessment"] = classify_legitimacy_assessment(
        traffic_profile=str(traffic_segments.get("traffic_profile") or "unknown"),
        top_agent_concentration_pct=payload["top_agent_concentration_pct"],
        mcp_session_share_pct=float(traffic_segments.get("mcp_session_share_pct") or 0.0),
    )
    if uptime_seconds is not None:
        payload["uptime_seconds"] = int(uptime_seconds)
    return payload
