"""Metering helpers for Delx Agent Utilities.

The free witness protocol remains separate. This module only handles practical
stateless utility usage, API-key attribution, and future billing readiness.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

UTILITY_API_KEY_PREFIX = "dux"


def hash_utility_api_key(raw_key: str) -> str:
    return hashlib.sha256(str(raw_key or "").strip().encode("utf-8")).hexdigest()


def new_utility_api_key() -> str:
    return f"{UTILITY_API_KEY_PREFIX}_{secrets.token_urlsafe(32)}"


def utility_key_prefix(raw_key: str) -> str:
    text = str(raw_key or "").strip()
    if not text:
        return ""
    return text[:12]


def extract_target_host(args: dict[str, Any] | None) -> str:
    payload = args if isinstance(args, dict) else {}
    raw = str(payload.get("url") or payload.get("origin") or payload.get("domain") or "").strip()
    if not raw:
        return ""
    if "://" not in raw and "." in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlparse(raw)
        return (parsed.netloc or parsed.path or "").split("@")[-1].split(":")[0].lower()[:160]
    except Exception:
        return ""


def input_fingerprint(args: dict[str, Any] | None) -> str:
    payload = args if isinstance(args, dict) else {}
    safe = {
        key: payload.get(key)
        for key in sorted(payload)
        if key in {"url", "domain", "origin", "record_type", "timeout"}
    }
    return hashlib.sha256(json.dumps(safe, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def build_metering_event(
    *,
    product: dict[str, Any],
    tool_name: str,
    args: dict[str, Any],
    agent_id: str,
    source: str,
    transport: str,
    compatibility_route: bool,
    charge_policy: dict[str, Any],
    pricing_payload: dict[str, Any],
    status: str,
    ok: bool,
    latency_ms: int = 0,
    error_kind: str | None = None,
    api_key: dict[str, Any] | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
    payment_verified: bool = False,
) -> dict[str, Any]:
    price = float(pricing_payload.get("price_usdc") or product.get("price", {}).get("amount") or 0.0)
    charge_mode = str(charge_policy.get("mode") or pricing_payload.get("utility_charge_mode") or "off")
    payment_mode = "enforce" if charge_mode == "enforce" else "shadow" if charge_mode == "shadow" else "free"
    enforced_revenue = price if ok and payment_mode == "enforce" and payment_verified else 0.0
    shadow_revenue = price if ok and payment_mode in {"shadow", "enforce"} else 0.0
    key_payload = api_key if isinstance(api_key, dict) else {}
    return {
        "product_id": str(product.get("product_id") or ""),
        "tool_name": tool_name,
        "slug": str(product.get("slug") or ""),
        "agent_id": agent_id,
        "caller_key_hash": str(key_payload.get("key_hash") or ""),
        "caller_label": str(key_payload.get("label") or ""),
        "source": source,
        "transport": transport,
        "route_type": "legacy_x402" if compatibility_route else "canonical",
        "charge_mode": charge_mode,
        "payment_mode": payment_mode,
        "price_usdc": round(price, 4),
        "shadow_revenue_usdc": round(shadow_revenue, 4),
        "enforced_revenue_usdc": round(enforced_revenue, 4),
        "status": status,
        "ok": bool(ok),
        "latency_ms": int(latency_ms or 0),
        "error_kind": str(error_kind or ""),
        "target_host": extract_target_host(args),
        "input_fingerprint": input_fingerprint(args),
        "client_ip": str(client_ip or "")[:120],
        "user_agent": str(user_agent or "")[:220],
    }


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _looks_operator_test(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(row.get(key) or "")
        for key in ("agent_id", "source", "user_agent", "caller_label")
    ).lower()
    return any(token in text for token in ("codex", "hermes", "smoke", "retest", "audit"))


def _looks_probe(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ("source", "user_agent")).lower()
    return any(token in text for token in ("probe", "bot", "crawler", "spider", "uptime", "x402station", "gptbot"))


def _looks_crawler(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ("source", "user_agent")).lower()
    return any(
        token in text
        for token in (
            "bot",
            "crawler",
            "spider",
            "slurp",
            "gptbot",
            "googlebot",
            "bingbot",
            "perplexitybot",
            "applebot",
            "amazonbot",
            "facebookexternalhit",
            "slackbot",
        )
    )


def classify_utility_event(row: dict[str, Any]) -> str:
    """Classify demand quality without treating discovery noise as adoption.

    Some x402 discovery only becomes complete after a payment attempt or 402
    challenge. Missing-input x402 probes are therefore useful discovery signals,
    but not valid utility usage.
    """
    status = str(row.get("status") or "").strip()
    route_type = str(row.get("route_type") or "").strip()
    ok = bool(row.get("ok"))
    target = str(row.get("target_host") or "").strip()
    has_api_key = bool(str(row.get("caller_key_hash") or "").strip())
    agent = str(row.get("agent_id") or "").strip().lower()
    enforced_revenue = float(row.get("enforced_revenue_usdc") or 0.0)

    if ok and enforced_revenue > 0:
        return "paid_verified_utility_user"
    if _looks_operator_test(row):
        return "operator_test"
    if status == "missing_required_input" and route_type == "legacy_x402":
        return "payment_discovery_probe"
    if "x402station" in str(row.get("user_agent") or "").lower():
        return "payment_discovery_probe"
    if status == "missing_required_input":
        return "discovery_probe"
    if _looks_crawler(row):
        return "crawler_discovery"
    if _looks_probe(row) and not ok:
        return "discovery_probe"
    if ok and target and (has_api_key or (agent and agent != "unknown")):
        return "valid_utility_user"
    if ok and target:
        return "valid_anonymous_utility_user"
    if route_type == "legacy_x402":
        return "payment_discovery_probe"
    return "unknown"


def build_utility_metering_dashboard(
    rows: list[dict[str, Any]],
    *,
    product_catalog: dict[str, Any],
    days: int,
    api_key_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    products = product_catalog.get("products") if isinstance(product_catalog, dict) else []
    products = products if isinstance(products, list) else []
    by_product: dict[str, dict[str, Any]] = {}
    by_source: dict[str, int] = defaultdict(int)
    by_route: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    by_demand_class: dict[str, int] = defaultdict(int)
    recent: list[dict[str, Any]] = []

    for product in products:
        if not isinstance(product, dict):
            continue
        pid = str(product.get("product_id") or "")
        if not pid:
            continue
        by_product[pid] = {
            "product_id": pid,
            "title": product.get("title"),
            "tool_name": product.get("tool_name"),
            "slug": product.get("slug"),
            "calls": 0,
            "ok_calls": 0,
            "error_calls": 0,
            "unique_agents": set(),
            "unique_callers": set(),
            "unique_targets": set(),
            "latencies": [],
            "shadow_revenue_usdc": 0.0,
            "enforced_revenue_usdc": 0.0,
            "top_sources": defaultdict(int),
            "demand_classes": defaultdict(int),
        }

    total_calls = 0
    ok_calls = 0
    shadow_revenue = 0.0
    enforced_revenue = 0.0

    for row in rows:
        pid = str(row.get("product_id") or "unknown")
        bucket = by_product.setdefault(
            pid,
            {
                "product_id": pid,
                "title": pid,
                "tool_name": row.get("tool_name"),
                "slug": row.get("slug"),
                "calls": 0,
                "ok_calls": 0,
                "error_calls": 0,
                "unique_agents": set(),
                "unique_callers": set(),
                "unique_targets": set(),
                "latencies": [],
                "shadow_revenue_usdc": 0.0,
                "enforced_revenue_usdc": 0.0,
                "top_sources": defaultdict(int),
                "demand_classes": defaultdict(int),
            },
        )
        metadata = _safe_json(row.get("metadata_json"))
        ok = bool(row.get("ok"))
        source = str(row.get("source") or "unknown")
        route = str(row.get("route_type") or "unknown")
        status = str(row.get("status") or "unknown")
        agent = str(row.get("agent_id") or "")
        caller = str(row.get("caller_key_hash") or row.get("client_ip") or "")
        target = str(row.get("target_host") or "")
        latency = int(row.get("latency_ms") or 0)
        row_shadow = float(row.get("shadow_revenue_usdc") or 0.0)
        row_enforced = float(row.get("enforced_revenue_usdc") or 0.0)
        demand_class = classify_utility_event(row)

        total_calls += 1
        ok_calls += 1 if ok else 0
        shadow_revenue += row_shadow
        enforced_revenue += row_enforced
        by_source[source] += 1
        by_route[route] += 1
        by_status[status] += 1
        by_demand_class[demand_class] += 1

        bucket["calls"] += 1
        bucket["ok_calls"] += 1 if ok else 0
        bucket["error_calls"] += 0 if ok else 1
        if agent:
            bucket["unique_agents"].add(agent)
        if caller:
            bucket["unique_callers"].add(caller)
        if target:
            bucket["unique_targets"].add(target)
        if latency >= 0:
            bucket["latencies"].append(latency)
        bucket["shadow_revenue_usdc"] += row_shadow
        bucket["enforced_revenue_usdc"] += row_enforced
        bucket["top_sources"][source] += 1
        bucket["demand_classes"][demand_class] += 1

        if len(recent) < 30:
            recent.append(
                {
                    "timestamp": row.get("timestamp"),
                    "product_id": pid,
                    "tool_name": row.get("tool_name"),
                    "status": status,
                    "ok": ok,
                    "source": source,
                    "route_type": route,
                    "target_host": target,
                    "latency_ms": latency,
                    "payment_mode": row.get("payment_mode"),
                    "shadow_revenue_usdc": row_shadow,
                    "error_kind": row.get("error_kind") or metadata.get("error_kind") or "",
                    "demand_class": demand_class,
                }
            )

    product_rows = []
    for bucket in by_product.values():
        latencies = sorted(int(v) for v in bucket.pop("latencies", []) if int(v) >= 0)
        calls = int(bucket["calls"] or 0)
        ok = int(bucket["ok_calls"] or 0)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0
        avg = round(sum(latencies) / len(latencies), 1) if latencies else 0
        top_sources = [
            {"source": source, "calls": count}
            for source, count in sorted(bucket.pop("top_sources").items(), key=lambda item: (-int(item[1]), str(item[0])))[:5]
        ]
        demand_classes = [
            {"class": name, "calls": count}
            for name, count in sorted(bucket.pop("demand_classes").items(), key=lambda item: (-int(item[1]), str(item[0])))
        ]
        valid_calls = sum(int(row["calls"]) for row in demand_classes if row["class"] in {"paid_verified_utility_user", "valid_utility_user", "valid_anonymous_utility_user"})
        probe_calls = sum(int(row["calls"]) for row in demand_classes if row["class"] in {"crawler_discovery", "discovery_probe", "payment_discovery_probe"})
        product_rows.append(
            {
                **bucket,
                "unique_agents": len(bucket["unique_agents"]),
                "unique_callers": len(bucket["unique_callers"]),
                "unique_targets": len(bucket["unique_targets"]),
                "success_rate_pct": round((ok / calls) * 100, 2) if calls else 0.0,
                "avg_latency_ms": avg,
                "p95_latency_ms": p95,
                "shadow_revenue_usdc": round(float(bucket["shadow_revenue_usdc"] or 0.0), 4),
                "enforced_revenue_usdc": round(float(bucket["enforced_revenue_usdc"] or 0.0), 4),
                "top_sources": top_sources,
                "valid_demand_calls": valid_calls,
                "probe_calls": probe_calls,
                "demand_classes": demand_classes,
            }
        )
    product_rows.sort(key=lambda item: (-int(item["calls"]), str(item["product_id"])))

    api_key_rows = api_key_rows or []
    active_keys = [row for row in api_key_rows if int(row.get("is_active") or 0) == 1]
    now = datetime.now(timezone.utc)
    recent_key_cutoff = (now - timedelta(days=days)).isoformat()

    return {
        "ok": True,
        "surface": "delx-agent-utilities",
        "window_days": int(days),
        "totals": {
            "calls": total_calls,
            "ok_calls": ok_calls,
            "error_calls": max(0, total_calls - ok_calls),
            "success_rate_pct": round((ok_calls / total_calls) * 100, 2) if total_calls else 0.0,
            "shadow_revenue_usdc": round(shadow_revenue, 4),
            "enforced_revenue_usdc": round(enforced_revenue, 4),
            "active_api_keys": len(active_keys),
            "api_keys_seen_window": sum(1 for row in active_keys if str(row.get("last_seen_at") or "") >= recent_key_cutoff),
            "valid_utility_user_calls": int(by_demand_class.get("valid_utility_user", 0)),
            "valid_anonymous_utility_user_calls": int(by_demand_class.get("valid_anonymous_utility_user", 0)),
            "paid_verified_utility_user_calls": int(by_demand_class.get("paid_verified_utility_user", 0)),
            "operator_test_calls": int(by_demand_class.get("operator_test", 0)),
            "crawler_discovery_calls": int(by_demand_class.get("crawler_discovery", 0)),
            "discovery_probe_calls": int(by_demand_class.get("discovery_probe", 0)),
            "payment_discovery_probe_calls": int(by_demand_class.get("payment_discovery_probe", 0)),
            "real_demand_calls": int(
                by_demand_class.get("paid_verified_utility_user", 0)
                + by_demand_class.get("valid_utility_user", 0)
                + by_demand_class.get("valid_anonymous_utility_user", 0)
            ),
        },
        "by_product": product_rows,
        "by_source": [
            {"source": source, "calls": count}
            for source, count in sorted(by_source.items(), key=lambda item: (-int(item[1]), str(item[0])))[:15]
        ],
        "by_route_type": [
            {"route_type": route, "calls": count}
            for route, count in sorted(by_route.items(), key=lambda item: (-int(item[1]), str(item[0])))
        ],
        "by_status": [
            {"status": status, "calls": count}
            for status, count in sorted(by_status.items(), key=lambda item: (-int(item[1]), str(item[0])))
        ],
        "by_demand_class": [
            {"class": name, "calls": count}
            for name, count in sorted(by_demand_class.items(), key=lambda item: (-int(item[1]), str(item[0])))
        ],
        "demand_quality": {
            "real_demand_classes": ["paid_verified_utility_user", "valid_utility_user", "valid_anonymous_utility_user"],
            "probe_classes": ["crawler_discovery", "discovery_probe", "payment_discovery_probe"],
            "operator_class": "operator_test",
            "x402_note": "Some x402 discovery remains incomplete until a first payment attempt or 402 challenge; payment_discovery_probe is useful distribution signal, not paid-product adoption.",
        },
        "recent_events": recent,
        "pricing": {
            "rollout": str((product_catalog.get("monetization_rollout") or {}).get("charge_mode") or "unknown"),
            "next_canary": "website_intelligence_report",
            "recommended_min_shadow_days": 3,
        },
    }


def build_utility_adoption_snapshot(
    rows: list[dict[str, Any]],
    *,
    product_catalog: dict[str, Any],
    window_hours: int,
    prior_agents: set[str] | None = None,
) -> dict[str, Any]:
    """Summarize real utility adoption separately from probes and crawlers."""
    prior_agents = prior_agents or set()
    product_ids = {
        str(product.get("product_id") or "")
        for product in (product_catalog.get("products") or [])
        if isinstance(product, dict)
    }
    real_classes = {"paid_verified_utility_user", "valid_utility_user", "valid_anonymous_utility_user"}
    probe_classes = {"crawler_discovery", "discovery_probe", "payment_discovery_probe"}
    by_class: dict[str, int] = defaultdict(int)
    by_tool: dict[str, dict[str, Any]] = {}
    by_transport: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    active_agents: set[str] = set()
    real_agents: set[str] = set()
    targets: set[str] = set()
    productized_calls = 0
    ok_calls = 0
    real_calls = 0
    probe_calls = 0
    operator_calls = 0
    shadow_usdc = 0.0
    enforced_usdc = 0.0

    for row in rows:
        demand_class = classify_utility_event(row)
        by_class[demand_class] += 1
        tool_name = str(row.get("tool_name") or "unknown")
        source = str(row.get("source") or "unknown")
        transport = str(row.get("transport") or "unknown")
        agent = str(row.get("agent_id") or "").strip()
        target = str(row.get("target_host") or "").strip()
        ok = bool(row.get("ok"))
        latency = int(row.get("latency_ms") or 0)
        shadow = float(row.get("shadow_revenue_usdc") or 0.0)
        enforced = float(row.get("enforced_revenue_usdc") or 0.0)

        bucket = by_tool.setdefault(
            tool_name,
            {
                "tool_name": tool_name,
                "calls": 0,
                "ok_calls": 0,
                "real_demand_calls": 0,
                "probe_calls": 0,
                "unique_agents": set(),
                "unique_targets": set(),
                "latencies": [],
                "shadow_revenue_usdc": 0.0,
                "enforced_revenue_usdc": 0.0,
            },
        )
        bucket["calls"] += 1
        bucket["ok_calls"] += 1 if ok else 0
        bucket["real_demand_calls"] += 1 if demand_class in real_classes else 0
        bucket["probe_calls"] += 1 if demand_class in probe_classes else 0
        if agent:
            bucket["unique_agents"].add(agent)
            active_agents.add(agent)
        if target:
            bucket["unique_targets"].add(target)
            targets.add(target)
        if latency >= 0:
            bucket["latencies"].append(latency)
        bucket["shadow_revenue_usdc"] += shadow
        bucket["enforced_revenue_usdc"] += enforced

        by_source[source] += 1
        by_transport[transport] += 1
        ok_calls += 1 if ok else 0
        productized_calls += 1 if str(row.get("product_id") or "") in product_ids else 0
        real_calls += 1 if demand_class in real_classes else 0
        probe_calls += 1 if demand_class in probe_classes else 0
        operator_calls += 1 if demand_class == "operator_test" else 0
        shadow_usdc += shadow
        enforced_usdc += enforced
        if demand_class in real_classes and agent:
            real_agents.add(agent)

    tool_rows = []
    for bucket in by_tool.values():
        latencies = sorted(int(v) for v in bucket.pop("latencies", []) if int(v) >= 0)
        calls = int(bucket["calls"] or 0)
        ok = int(bucket["ok_calls"] or 0)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0
        tool_rows.append(
            {
                **bucket,
                "unique_agents": len(bucket["unique_agents"]),
                "unique_targets": len(bucket["unique_targets"]),
                "success_rate_pct": round((ok / calls) * 100, 2) if calls else 0.0,
                "p95_latency_ms": p95,
                "shadow_revenue_usdc": round(float(bucket["shadow_revenue_usdc"] or 0.0), 4),
                "enforced_revenue_usdc": round(float(bucket["enforced_revenue_usdc"] or 0.0), 4),
            }
        )
    tool_rows.sort(key=lambda item: (-int(item["real_demand_calls"]), -int(item["calls"]), str(item["tool_name"])))

    total_calls = len(rows)
    new_agents = sorted(agent for agent in active_agents if agent not in prior_agents)
    real_share = round((real_calls / total_calls) * 100, 2) if total_calls else 0.0
    probe_share = round((probe_calls / total_calls) * 100, 2) if total_calls else 0.0
    status = "real_demand" if real_calls >= probe_calls and real_calls > 0 else "probe_heavy" if probe_calls else "quiet"

    return {
        "ok": True,
        "surface": "delx-agent-utilities",
        "window_hours": int(window_hours),
        "status": status,
        "totals": {
            "calls": total_calls,
            "ok_calls": ok_calls,
            "success_rate_pct": round((ok_calls / total_calls) * 100, 2) if total_calls else 0.0,
            "productized_calls": productized_calls,
            "real_demand_calls": real_calls,
            "real_demand_share_pct": real_share,
            "probe_calls": probe_calls,
            "probe_share_pct": probe_share,
            "operator_test_calls": operator_calls,
            "active_agents": len(active_agents),
            "real_demand_agents": len(real_agents),
            "new_agents": len(new_agents),
            "unique_targets": len(targets),
            "shadow_revenue_usdc": round(shadow_usdc, 4),
            "enforced_revenue_usdc": round(enforced_usdc, 4),
        },
        "by_tool": tool_rows[:25],
        "by_demand_class": [
            {"class": name, "calls": count}
            for name, count in sorted(by_class.items(), key=lambda item: (-int(item[1]), str(item[0])))
        ],
        "by_source": [
            {"source": source, "calls": count}
            for source, count in sorted(by_source.items(), key=lambda item: (-int(item[1]), str(item[0])))[:15]
        ],
        "by_transport": [
            {"transport": transport, "calls": count}
            for transport, count in sorted(by_transport.items(), key=lambda item: (-int(item[1]), str(item[0])))
        ],
        "new_agent_ids": new_agents[:25],
        "cost_guardrail": {
            "llm_calls_expected": 0,
            "token_cost_expected": "none",
            "note": "Utilities are stateless deterministic network/text helpers; they should not call an LLM unless a future tool explicitly opts in.",
        },
        "decision_notes": [
            "Treat real_demand_calls as adoption; treat discovery_probe and crawler_discovery as distribution, not usage.",
            "Keep Protocol free. Move Utilities from shadow to enforce only after repeat real_demand_agents and successful AgentCash/x402 payment paths are proven.",
        ],
    }
