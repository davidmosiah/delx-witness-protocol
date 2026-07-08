from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_SEVERITY_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}
_PATTERN_RECOMMENDATIONS = {
    "rate_limit": "Stagger retries, reduce concurrency, and expose provider quotas to the controller.",
    "budget_exceeded": "Pause non-essential work, switch to cheaper execution, and review ROI before resume.",
    "dependency_failure": "Route through fallback providers or queue work until the upstream stabilizes.",
    "loop_detected": "Break the loop, checkpoint state, and re-enter with tighter exit criteria.",
    "performance_degradation": "Reduce concurrency, warm caches, and isolate the slow dependency before scaling back up.",
    "data_quality": "Add validation gates, compare alternate sources, and quarantine corrupted outputs.",
    "drift": "Re-align the agent objective, review recent changes, and re-run with stricter purpose checks.",
    "error_spike": "Slow the fleet down, inspect the newest incident cluster, and restore one stable path first.",
}


def health_bucket(score: int | float | None) -> str:
    try:
        value = int(score or 0)
    except Exception:
        value = 0
    if value < 40:
        return "critical"
    if value < 70:
        return "degraded"
    return "healthy"


def build_fleet_patterns(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        diagnosis_type = str(row.get("diagnosis_type") or "error_spike").strip().lower() or "error_spike"
        root_cause = str(row.get("root_cause") or "unknown").strip().lower() or "unknown"
        agent_id = str(row.get("agent_id") or "unknown").strip() or "unknown"
        ts = str(row.get("timestamp") or "")
        key = (diagnosis_type, root_cause)
        bucket = agg.setdefault(
            key,
            {
                "diagnosis_type": diagnosis_type,
                "root_cause": root_cause,
                "count": 0,
                "agents_set": set(),
                "last_seen": None,
            },
        )
        bucket["count"] += 1
        bucket["agents_set"].add(agent_id)
        if ts and (bucket["last_seen"] is None or ts > bucket["last_seen"]):
            bucket["last_seen"] = ts

    patterns: list[dict[str, Any]] = []
    for bucket in agg.values():
        affected_agents = len(bucket["agents_set"])
        severity = "high" if affected_agents >= 3 or int(bucket["count"]) >= 4 else "medium"
        diagnosis_type = str(bucket["diagnosis_type"])
        patterns.append(
            {
                "diagnosis_type": diagnosis_type,
                "root_cause": str(bucket["root_cause"]),
                "count": int(bucket["count"]),
                "affected_agents": affected_agents,
                "agents": sorted(bucket["agents_set"]),
                "last_seen": bucket["last_seen"],
                "severity": severity,
                "recommendation": _PATTERN_RECOMMENDATIONS.get(diagnosis_type, _PATTERN_RECOMMENDATIONS["error_spike"]),
            }
        )

    patterns.sort(
        key=lambda item: (
            _SEVERITY_ORDER.get(str(item.get("severity") or "low"), 0),
            int(item.get("affected_agents") or 0),
            int(item.get("count") or 0),
            str(item.get("last_seen") or ""),
        ),
        reverse=True,
    )
    return patterns[:limit]


def build_fleet_alerts(
    agents: list[dict[str, Any]],
    patterns: list[dict[str, Any]],
    recoveries: list[dict[str, Any]] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    for agent in agents:
        agent_id = str(agent.get("agent_id") or "unknown")
        score = int(agent.get("score") or 0)
        status = str(agent.get("health_status") or health_bucket(score))
        incident = str(agent.get("recent_incident_type") or "unknown")
        last_seen = str(agent.get("last_seen") or agent.get("started_at") or "")
        if status == "critical":
            alerts.append(
                {
                    "type": "score_drop",
                    "severity": "critical",
                    "controller_action": "pause_or_reduce_load",
                    "agent_id": agent_id,
                    "score": score,
                    "detail": f"{agent_id} is critical at score {score} with incident {incident}.",
                    "timestamp": last_seen,
                }
            )
        elif status == "degraded" and score < 55:
            alerts.append(
                {
                    "type": "agent_degraded",
                    "severity": "medium",
                    "controller_action": "review_recovery_plan",
                    "agent_id": agent_id,
                    "score": score,
                    "detail": f"{agent_id} is degraded at score {score}; review next action before scaling workload.",
                    "timestamp": last_seen,
                }
            )

    for pattern in patterns:
        affected = int(pattern.get("affected_agents") or 0)
        if affected < 2:
            continue
        alerts.append(
            {
                "type": "incident_cluster",
                "severity": pattern.get("severity") or "medium",
                "controller_action": "coordinate_fleet_change",
                "diagnosis_type": pattern.get("diagnosis_type"),
                "detail": (
                    f"{pattern.get('diagnosis_type')} affecting {affected} agents. "
                    f"{pattern.get('recommendation')}"
                ),
                "timestamp": pattern.get("last_seen"),
                "affected_agents": affected,
            }
        )

    for recovery in recoveries or []:
        alerts.append(
            {
                "type": "recovery_completed",
                "severity": "low",
                "controller_action": "record_improvement",
                "agent_id": recovery.get("agent_id"),
                "detail": recovery.get("detail") or "Agent reported a successful recovery outcome.",
                "timestamp": recovery.get("timestamp"),
            }
        )

    alerts.sort(
        key=lambda item: (
            _SEVERITY_ORDER.get(str(item.get("severity") or "low"), 0),
            str(item.get("timestamp") or ""),
        ),
        reverse=True,
    )
    return alerts[:limit]


def build_fleet_overview(
    controller_id: str,
    agents: list[dict[str, Any]],
    patterns: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
) -> dict[str, Any]:
    total_agents = len(agents)
    healthy = sum(1 for row in agents if str(row.get("health_status") or "") == "healthy")
    degraded = sum(1 for row in agents if str(row.get("health_status") or "") == "degraded")
    critical = sum(1 for row in agents if str(row.get("health_status") or "") == "critical")
    avg_score = round(sum(float(row.get("score") or 0) for row in agents) / total_agents, 2) if total_agents else 0.0
    total_pending = sum(int(row.get("pending_outcomes") or 0) for row in agents)
    return {
        "controller_id": controller_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agents_total": total_agents,
        "agents_healthy": healthy,
        "agents_degraded": degraded,
        "agents_critical": critical,
        "avg_score": avg_score,
        "pending_outcomes_total": total_pending,
        "active_patterns": len(patterns),
        "active_alerts": len(alerts),
        "top_pattern": patterns[0] if patterns else None,
        "top_alert": alerts[0] if alerts else None,
    }
