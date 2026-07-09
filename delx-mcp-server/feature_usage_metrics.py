from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from product_surfaces import product_metadata_for_tool

EXPECTED_UTILITY_ERROR_TOOLS = {
    "util_json_validate",
    "util_regex_test",
    "util_cron_describe",
    "util_base64",
    "util_timestamp_convert",
    "util_url_health",
    "util_http_codes",
}


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


def build_feature_usage_report(
    rows: list[dict[str, Any]],
    *,
    days: int = 30,
    min_calls: int = 0,
    known_features: list[str] | None = None,
    protected_features: list[str] | None = None,
) -> dict[str, Any]:
    days = max(1, min(int(days or 30), 90))
    min_calls = max(0, min(int(min_calls or 0), 10_000))
    known_set = {str(x).strip() for x in (known_features or []) if str(x).strip()}
    protected_set = {str(x).strip() for x in (protected_features or []) if str(x).strip()}

    agg: dict[str, dict[str, Any]] = {}
    unknown_agg: dict[str, dict[str, Any]] = {}
    transport_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    error_kind_counts: dict[str, int] = {}
    raw_tool_called_events = 0
    total_errors = 0
    system_error_count = 0

    for row in rows or []:
        etype = str(row.get("event_type") or "").strip().lower()
        meta = _coerce_metadata(row)
        tool = str(meta.get("tool") or "").strip()
        if not tool:
            continue

        target_agg = agg
        if known_set and tool not in known_set:
            target_agg = unknown_agg

        rec = target_agg.setdefault(
            tool,
            {
                "feature": tool,
                "calls": 0,
                "raw_calls": 0,
                "success_count": 0,
                "error_count": 0,
                "system_error_count": 0,
                "error_kind_counts": {},
                "last_called_at": None,
                **product_metadata_for_tool(tool),
            },
        )
        if meta.get("product"):
            rec["product"] = str(meta.get("product") or "").strip()
        if meta.get("product_surface"):
            rec["product_surface"] = str(meta.get("product_surface") or "").strip()
        if meta.get("metrics_bucket"):
            rec["metrics_bucket"] = str(meta.get("metrics_bucket") or "").strip()

        ts = str(row.get("timestamp") or "").strip() or None
        if ts and (not rec["last_called_at"] or ts > str(rec["last_called_at"])):
            rec["last_called_at"] = ts

        if etype == "tool_called":
            rec["raw_calls"] += 1
            raw_tool_called_events += 1
            transport = str(meta.get("transport") or "unknown").strip().lower() or "unknown"
            source = str(meta.get("source") or "unknown").strip().lower() or "unknown"
            transport_counts[transport] = transport_counts.get(transport, 0) + 1
            source_counts[source] = source_counts.get(source, 0) + 1
        elif etype == "tool_call_success":
            rec["success_count"] += 1
        elif etype == "tool_call_error":
            rec["error_count"] += 1
            total_errors += 1
            kind = str(meta.get("error_kind") or "").strip().lower() or "unspecified"
            if kind == "unspecified" and tool.startswith("util_"):
                kind = "expected_domain" if tool in EXPECTED_UTILITY_ERROR_TOOLS else "unknown"
            rec_kind_counts = rec.get("error_kind_counts") if isinstance(rec.get("error_kind_counts"), dict) else {}
            rec_kind_counts[kind] = int(rec_kind_counts.get(kind, 0) or 0) + 1
            rec["error_kind_counts"] = rec_kind_counts
            error_kind_counts[kind] = int(error_kind_counts.get(kind, 0) or 0) + 1
            if kind in {"internal", "system"} or (not tool.startswith("util_") and kind in {"unspecified", "unknown"}):
                rec["system_error_count"] = int(rec.get("system_error_count") or 0) + 1
                system_error_count += 1

    def _finalize(records: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        total = 0
        for rec in records.values():
            raw_calls = int(rec.get("raw_calls") or 0)
            ok = int(rec.get("success_count") or 0)
            err = int(rec.get("error_count") or 0)
            effective_calls = max(raw_calls, ok + err)
            if effective_calls < min_calls:
                continue
            rec["calls"] = effective_calls
            rec["success_rate"] = round((ok / (ok + err)) * 100, 2) if (ok + err) else None
            rec["system_error_rate"] = (
                round((int(rec.get("system_error_count") or 0) / effective_calls) * 100, 2) if effective_calls else 0.0
            )
            total += effective_calls
            rows.append(rec)
        rows.sort(key=lambda x: (-int(x.get("calls") or 0), str(x.get("feature") or "")))
        return rows, total

    items, total_effective_calls = _finalize(agg)
    unknown_items, unknown_effective_calls = _finalize(unknown_agg)
    least = sorted(items, key=lambda x: (int(x.get("calls") or 0), str(x.get("feature") or "")))
    active_features = {str(x.get("feature") or "").strip() for x in items if str(x.get("feature") or "").strip()}
    unused = sorted(list(known_set - active_features)) if known_set else []
    deprecation = [f for f in unused if f not in protected_set]
    unknown_active_features = {
        str(x.get("feature") or "").strip()
        for x in unknown_items
        if str(x.get("feature") or "").strip()
    }

    util_rows = [r for r in items if str(r.get("feature") or "").startswith("util_")]
    util_calls = int(sum(int(r.get("calls") or 0) for r in util_rows))
    util_success = int(sum(int(r.get("success_count") or 0) for r in util_rows))
    util_errors = int(sum(int(r.get("error_count") or 0) for r in util_rows))
    expected_errors = int(
        sum(int(r.get("error_count") or 0) for r in util_rows if str(r.get("feature") or "") in EXPECTED_UTILITY_ERROR_TOOLS)
    )
    strict_den = util_success + util_errors
    adjusted_den = util_success + max(0, util_errors - expected_errors)
    util_summary = {
        "calls": util_calls,
        "success_count": util_success,
        "error_count": util_errors,
        "share_pct": round((util_calls / total_effective_calls) * 100, 2) if total_effective_calls else 0.0,
        "strict_success_rate_pct": round((util_success / strict_den) * 100, 2) if strict_den else 0.0,
        "adjusted_success_rate_pct": round((util_success / adjusted_den) * 100, 2) if adjusted_den else 0.0,
        "expected_error_count": expected_errors,
        "expected_error_tools": sorted(EXPECTED_UTILITY_ERROR_TOOLS),
    }
    product_counts: dict[str, int] = {}
    surface_counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {}
    for row in items:
        calls = int(row.get("calls") or 0)
        product = str(row.get("product") or "unknown").strip() or "unknown"
        surface = str(row.get("product_surface") or "unknown").strip() or "unknown"
        bucket = str(row.get("metrics_bucket") or "unknown").strip() or "unknown"
        product_counts[product] = int(product_counts.get(product, 0) or 0) + calls
        surface_counts[surface] = int(surface_counts.get(surface, 0) or 0) + calls
        bucket_counts[bucket] = int(bucket_counts.get(bucket, 0) or 0) + calls
    system_error_rate_pct = round((system_error_count / total_effective_calls) * 100, 2) if total_effective_calls else 0.0

    return {
        "window_days": days,
        "min_calls": min_calls,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_tool_calls": total_effective_calls,
        "observed_total_tool_calls": total_effective_calls + unknown_effective_calls,
        "raw_tool_called_events": raw_tool_called_events,
        "unique_tools_called": len(active_features),
        "unknown_feature_summary": {
            "calls": unknown_effective_calls,
            "unique_features": len(unknown_active_features),
        },
        "util_summary": util_summary,
        "product_summary": {
            "by_product": [
                {"product": k, "calls": v, "share_pct": round((v / total_effective_calls) * 100, 2) if total_effective_calls else 0.0}
                for k, v in sorted(product_counts.items(), key=lambda x: x[1], reverse=True)
            ],
            "by_surface": [
                {"surface": k, "calls": v, "share_pct": round((v / total_effective_calls) * 100, 2) if total_effective_calls else 0.0}
                for k, v in sorted(surface_counts.items(), key=lambda x: x[1], reverse=True)
            ],
            "by_metrics_bucket": [
                {"metrics_bucket": k, "calls": v, "share_pct": round((v / total_effective_calls) * 100, 2) if total_effective_calls else 0.0}
                for k, v in sorted(bucket_counts.items(), key=lambda x: x[1], reverse=True)
            ],
        },
        "error_summary": {
            "total_error_events": int(total_errors),
            "system_error_count": int(system_error_count),
            "system_error_rate_pct": system_error_rate_pct,
            "error_kind_counts": dict(sorted(error_kind_counts.items(), key=lambda x: x[1], reverse=True)),
        },
        "most_used": items[:30],
        "least_used": least[:30],
        "unknown_features": unknown_items[:30],
        "unused_features": unused,
        "deprecation_candidates": deprecation,
        "traffic_by_transport": [
            {"transport": k, "calls": v}
            for k, v in sorted(transport_counts.items(), key=lambda x: x[1], reverse=True)
        ],
        "traffic_by_source": [
            {"source": k, "calls": v}
            for k, v in sorted(source_counts.items(), key=lambda x: x[1], reverse=True)
        ],
    }
