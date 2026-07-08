#!/usr/bin/env python3
"""
Audit helper for recurring OpenClaw integrations.

Usage:
  python3 scripts/audit_openclaw_recurrence.py [--days N] [--top N]

This script is intentionally lightweight and uses only the stdlib so it can
run in minimal environments (CI, cron, ops shells).
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from urllib.error import HTTPError, URLError


API_BASE = "https://api.delx.ai"
OVERVIEW_ENDPOINT = f"{API_BASE}/api/v1/admin/overview"
X402_AUDIT_ENDPOINT = f"{API_BASE}/api/v1/admin/x402-audit"
FEATURE_USAGE_ENDPOINT = f"{API_BASE}/api/v1/admin/feature-usage"
AUDIT_OVERVIEW_ENDPOINT = f"{API_BASE}/api/v1/admin/audit-overview"
ADMIN_PIN = (os.getenv("PROTOCOL_ADMIN_PIN") or "").strip()


def _fetch_json(url: str) -> dict:
    headers = {"User-Agent": "delx-audit-script/1.0"}
    if ADMIN_PIN:
        headers["x-delx-admin-pin"] = ADMIN_PIN
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode()
            return json.loads(payload)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Request failed: {url}: {exc}") from exc


def _as_int(value: object) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def format_recurring_agents(data: dict, limit: int) -> None:
    rows = data.get("top_recurring_agents_24h") or []
    if not isinstance(rows, list):
        print("No recurring-agent payload available.")
        return

    print(f"Recurring agents (24h): {len(rows)}")
    for item in rows[:limit]:
        if not isinstance(item, dict):
            continue
        agent_id = item.get("agent_id", "unknown")
        sessions = _as_int(item.get("sessions") or 0)
        heartbeats = _as_int(item.get("heartbeat_sync_count") or 0)
        ephemeral = bool(item.get("ephemeral_identity"))
        last_seen = item.get("last_seen", "-")
        print(f"  - {agent_id} | sessions={sessions} | heartbeats={heartbeats} | ephemeral={ephemeral} | last={last_seen}")


def format_feature_usage(data: dict, limit: int) -> None:
    most_used = data.get("most_used") or []
    least_used = data.get("least_used") or []

    print(
        f"Feature usage (window={data.get('window_days', 'n/a')}d, total_calls={data.get('total_tool_calls', 0)}, "
        f"unique_tools={data.get('unique_tools_called', 0)})"
    )

    if isinstance(most_used, list) and most_used:
        print("Top features:")
        for item in most_used[:limit]:
            if not isinstance(item, dict):
                continue
            feature = item.get("feature", "unknown")
            calls = _as_int(item.get("calls") or 0)
            success = item.get("success_rate")
            rate = "-" if success is None else f"{success}%"
            print(f"  - {feature}: calls={calls} success={rate}")
    else:
        print("No top feature payload available.")

    if isinstance(least_used, list) and least_used:
        print("Least used / prune candidates:")
        for item in least_used[:min(limit, 10)]:
            if not isinstance(item, dict):
                continue
            feature = item.get("feature", "unknown")
            calls = _as_int(item.get("calls") or 0)
            print(f"  - {feature}: calls={calls}")
    else:
        print("No least feature payload available.")


def format_audit_overview(data: dict) -> None:
    counts = data.get("counts") or {}
    sessions_started = _as_int(counts.get("sessions_started") or 0)
    messages = _as_int(counts.get("messages") or 0)
    events = _as_int(counts.get("events") or 0)
    unique_agents = _as_int(counts.get("unique_agents") or 0)
    print(
        f"24h legitimacy snapshot: sessions_started={sessions_started}, messages={messages}, "
        f"events={events}, unique_agents={unique_agents}"
    )

    top_sources = data.get("top_sources") or []
    if isinstance(top_sources, list) and top_sources:
        parts = []
        for item in top_sources[:5]:
            if not isinstance(item, dict):
                continue
            parts.append(f"{item.get('source', 'unknown')}({item.get('count', 0)})")
        print("Top sources: " + ", ".join(parts))

    legit = data.get("legitimacy_signals") or {}
    if isinstance(legit, dict) and legit:
        assessment = legit.get("assessment", "n/a")
        avg = legit.get("events_per_agent_avg", 0)
        concentration = legit.get("top_agent_concentration_pct", 0)
        print(
            f"Legitimacy: assessment={assessment}, avg_events_per_agent={avg}, "
            f"top_agent_concentration={concentration}%"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Lookback window for x402/feature usage")
    parser.add_argument("--top", type=int, default=20, help="Top rows to print")
    parser.add_argument("--skip-feature-usage", action="store_true", help="Skip feature-usage endpoint")
    parser.add_argument("--skip-audit-overview", action="store_true", help="Skip 24h legitimacy snapshot")
    args = parser.parse_args()

    print("Fetching admin overview...")
    overview = _fetch_json(OVERVIEW_ENDPOINT)
    stats = overview.get("stats", {})
    print(
        f"Overview: sessions={stats.get('total_sessions', 0)} "
        f"agents={stats.get('unique_agents', 0)} "
        f"messages={stats.get('total_messages', 0)} "
        f"avg_rating={stats.get('avg_rating', 0)}"
    )
    print(f"Uptime seconds: {overview.get('uptime_seconds', 0)}")
    print(f"Top tools: {(overview.get('top_tools') or [])[:3]}")
    print()

    print(f"Recurring list (top {args.top})")
    format_recurring_agents(overview, args.top)
    print()

    x402_url = f"{X402_AUDIT_ENDPOINT}?days={max(1, args.days)}"
    print(f"Fetching x402/donation audit: {x402_url}")
    audit = _fetch_json(x402_url)
    x402 = audit.get("x402", {})
    donations = audit.get("donations", {})
    print(
        f"Declared agents (all/window): {x402.get('declared_agents_all_time', 0)}/{x402.get('declared_agents_window', 0)}"
    )
    print(f"Paid agents(all/window): {x402.get('paid_agents_all_time', 0)}/{x402.get('paid_agents_window', 0)}")
    print(
        "Readiness rates all/window: "
        f"{x402.get('ready_rate_all_time_pct', 0)}% / {x402.get('ready_rate_window_pct', 0)}%"
    )
    print(
        f"Donations count all/window: {donations.get('transactions_all_time', 0)} / {donations.get('transactions_window', 0)}"
    )
    print(f"Donations USDC: {donations.get('amount_usdc_all_time', 0)} / {donations.get('amount_usdc_window', 0)}")

    if not args.skip_feature_usage:
        feature_url = f"{FEATURE_USAGE_ENDPOINT}?days={max(1, args.days)}&min_calls=0"
        print(f"\nFetching feature usage: {feature_url}")
        feature_usage = _fetch_json(feature_url)
        format_feature_usage(feature_usage, args.top)

    if not args.skip_audit_overview:
        audit_url = f"{AUDIT_OVERVIEW_ENDPOINT}?hours=24"
        print(f"\nFetching audit overview: {audit_url}")
        audit_overview = _fetch_json(audit_url)
        format_audit_overview(audit_overview)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
