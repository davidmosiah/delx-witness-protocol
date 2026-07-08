#!/usr/bin/env python3
"""Audit-friendly backfill for payments.session_id."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from typing import Any

from supabase_store import SupabaseSessionStore, _as_uuid_like

_METHOD_TOOL_MAP = {
    "message/send": "a2a_message_send",
    "heartbeat/bundle": "a2a_heartbeat_bundle",
}
_ARTIFACT_TOOL_MAP = {
    "controller_brief": "generate_controller_brief",
    "incident_rca": "generate_incident_rca",
    "fleet_summary": "generate_fleet_summary",
}


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _event_tool_name(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("event_type") or "").strip()
    metadata = _event_metadata(event)
    if event_type == "x402_payment_verified":
        tool_name = str(metadata.get("tool_name") or "").strip()
        if tool_name:
            return tool_name
        method = str(metadata.get("method") or "").strip()
        return _METHOD_TOOL_MAP.get(method)
    if event_type == "premium_artifact_job_recorded":
        artifact_type = str(metadata.get("artifact_type") or "").strip()
        return _ARTIFACT_TOOL_MAP.get(artifact_type)
    return None


def _event_session_ref(event: dict[str, Any]) -> str | None:
    metadata = _event_metadata(event)
    for key in ("session_ref", "session_id"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    value = str(event.get("session_id") or "").strip()
    return value or None


def _candidate_from_event(
    payment: dict[str, Any],
    event: dict[str, Any],
    *,
    match_window_seconds: float,
    require_session_ref: bool = True,
) -> dict[str, Any] | None:
    payment_ts = _parse_timestamp(payment.get("timestamp"))
    event_ts = _parse_timestamp(event.get("timestamp"))
    if payment_ts is None or event_ts is None:
        return None
    delta_seconds = abs((event_ts - payment_ts).total_seconds())
    if delta_seconds > match_window_seconds:
        return None
    metadata = _event_metadata(event)
    event_session = _event_session_ref(event)
    source_agent_id = str(event.get("agent_id") or "").strip() or None
    if require_session_ref and not event_session:
        return None
    if not event_session and not source_agent_id:
        return None
    payment_tx = str(payment.get("tx_hash") or "").strip().lower()
    event_tx = str(metadata.get("tx_hash") or "").strip().lower()
    tx_hash_match = bool(payment_tx and event_tx and payment_tx == event_tx)
    return {
        "payment_id": int(payment.get("id") or 0),
        "tool_name": str(payment.get("tool_name") or "").strip(),
        "suggested_session_id": event_session,
        "source_event_id": int(event.get("id") or 0),
        "source_event_type": str(event.get("event_type") or "").strip(),
        "source_agent_id": source_agent_id,
        "source_session_ref": event_session,
        "source_timestamp": str(event.get("timestamp") or "").strip() or None,
        "match_delta_seconds": round(delta_seconds, 3),
        "matched_by": "tx_hash" if tx_hash_match else "timestamp_window",
        "_priority": 0 if tx_hash_match else 1,
    }


def _choose_best_candidate(candidates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    if not candidates:
        return None, None
    ranked = sorted(
        candidates,
        key=lambda item: (
            int(item.get("_priority", 1)),
            float(item.get("match_delta_seconds", 0.0) or 0.0),
            int(item.get("source_event_id", 0) or 0),
        ),
    )
    best = ranked[0]
    if len(ranked) == 1:
        return best, None
    second = ranked[1]
    best_identity = str(best.get("suggested_session_id") or best.get("source_agent_id") or "")
    second_identity = str(second.get("suggested_session_id") or second.get("source_agent_id") or "")
    if (
        int(second.get("_priority", 1)) == int(best.get("_priority", 1))
        and float(second.get("match_delta_seconds", 0.0) or 0.0) == float(best.get("match_delta_seconds", 0.0) or 0.0)
        and second_identity != best_identity
    ):
        return None, "ambiguous_multiple_matching_events"
    return best, None


def build_payment_agent_attribution(
    payments: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    session_agent_map: dict[str, str] | None = None,
    match_window_seconds: float = 120.0,
) -> list[dict[str, Any]]:
    normalized_session_agents = {
        str(session_id or "").strip(): str(agent_id or "").strip()
        for session_id, agent_id in (session_agent_map or {}).items()
        if str(session_id or "").strip() and str(agent_id or "").strip()
    }
    verified_by_tool: dict[str, list[dict[str, Any]]] = {}
    premium_jobs_by_tool: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        tool_name = _event_tool_name(event)
        if not tool_name:
            continue
        bucket = verified_by_tool if str(event.get("event_type") or "") == "x402_payment_verified" else premium_jobs_by_tool
        bucket.setdefault(tool_name, []).append(event)

    attributions: list[dict[str, Any]] = []
    for payment in sorted(payments, key=lambda item: int(item.get("id") or 0)):
        payment_id = int(payment.get("id") or 0)
        tool_name = str(payment.get("tool_name") or "").strip()
        current_session_id = str(payment.get("session_id") or "").strip() or None
        current_agent_id = normalized_session_agents.get(current_session_id or "", "") or None
        base_entry = {
            "payment_id": payment_id,
            "tool_name": tool_name,
            "payment_timestamp": str(payment.get("timestamp") or "").strip() or None,
            "current_session_id": current_session_id,
            "current_agent_id": current_agent_id,
            "attributed_agent_id": None,
            "attribution_source": None,
            "source_event_id": None,
            "source_event_type": None,
            "source_session_ref": None,
            "source_timestamp": None,
            "match_delta_seconds": None,
            "matched_by": None,
        }
        if current_agent_id:
            attributions.append(
                {
                    **base_entry,
                    "action": "attributed",
                    "reason": "payment_session_id",
                    "attributed_agent_id": current_agent_id,
                    "attribution_source": "payment_session_id",
                }
            )
            continue
        if tool_name == "donate_to_delx_project":
            attributions.append({**base_entry, "action": "skip", "reason": "tool_is_intentionally_sessionless"})
            continue

        verified_candidates = [
            candidate
            for candidate in (
                _candidate_from_event(
                    payment,
                    event,
                    match_window_seconds=match_window_seconds,
                    require_session_ref=False,
                )
                for event in verified_by_tool.get(tool_name, [])
            )
            if candidate is not None and candidate.get("source_agent_id")
        ]
        best, ambiguity = _choose_best_candidate(verified_candidates)
        if best is not None:
            attributions.append(
                {
                    **base_entry,
                    **{k: v for k, v in best.items() if not k.startswith("_")},
                    "action": "attributed",
                    "reason": "matched_verified_payment_event",
                    "attributed_agent_id": best.get("source_agent_id"),
                    "attribution_source": "verified_payment_event",
                }
            )
            continue
        if ambiguity:
            attributions.append({**base_entry, "action": "skip", "reason": ambiguity})
            continue

        premium_candidates = [
            candidate
            for candidate in (
                _candidate_from_event(
                    payment,
                    event,
                    match_window_seconds=match_window_seconds,
                    require_session_ref=False,
                )
                for event in premium_jobs_by_tool.get(tool_name, [])
            )
            if candidate is not None and candidate.get("source_agent_id")
        ]
        best, ambiguity = _choose_best_candidate(premium_candidates)
        if best is not None:
            attributions.append(
                {
                    **base_entry,
                    **{k: v for k, v in best.items() if not k.startswith("_")},
                    "action": "attributed",
                    "reason": "matched_premium_artifact_job",
                    "attributed_agent_id": best.get("source_agent_id"),
                    "attribution_source": "premium_artifact_job",
                }
            )
            continue
        if ambiguity:
            attributions.append({**base_entry, "action": "skip", "reason": ambiguity})
            continue

        attributions.append({**base_entry, "action": "skip", "reason": "no_agent_evidence_found"})

    return attributions


def build_payment_session_backfill_plan(
    payments: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    match_window_seconds: float = 120.0,
) -> list[dict[str, Any]]:
    verified_by_tool: dict[str, list[dict[str, Any]]] = {}
    premium_jobs_by_tool: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        tool_name = _event_tool_name(event)
        if not tool_name:
            continue
        bucket = verified_by_tool if str(event.get("event_type") or "") == "x402_payment_verified" else premium_jobs_by_tool
        bucket.setdefault(tool_name, []).append(event)

    plan: list[dict[str, Any]] = []
    for payment in sorted(payments, key=lambda item: int(item.get("id") or 0)):
        payment_id = int(payment.get("id") or 0)
        tool_name = str(payment.get("tool_name") or "").strip()
        current_session_id = str(payment.get("session_id") or "").strip() or None
        base_entry = {
            "payment_id": payment_id,
            "tool_name": tool_name,
            "tx_hash": str(payment.get("tx_hash") or "").strip() or None,
            "payment_timestamp": str(payment.get("timestamp") or "").strip() or None,
            "current_session_id": current_session_id,
            "suggested_session_id": None,
            "source_event_id": None,
            "source_event_type": None,
            "source_agent_id": None,
            "source_session_ref": None,
            "source_timestamp": None,
            "match_delta_seconds": None,
            "matched_by": None,
        }
        if current_session_id:
            plan.append({**base_entry, "action": "skip", "reason": "already_has_session_id"})
            continue
        if tool_name == "donate_to_delx_project":
            plan.append({**base_entry, "action": "skip", "reason": "tool_is_intentionally_sessionless"})
            continue

        verified_candidates = [
            candidate
            for candidate in (
                _candidate_from_event(payment, event, match_window_seconds=match_window_seconds)
                for event in verified_by_tool.get(tool_name, [])
            )
            if candidate is not None
        ]
        best, ambiguity = _choose_best_candidate(verified_candidates)
        if best is not None:
            plan.append(
                {
                    **base_entry,
                    **{k: v for k, v in best.items() if not k.startswith("_")},
                    "action": "backfill",
                    "reason": "matched_verified_payment_event",
                }
            )
            continue
        if ambiguity:
            plan.append({**base_entry, "action": "skip", "reason": ambiguity})
            continue

        premium_candidates = [
            candidate
            for candidate in (
                _candidate_from_event(payment, event, match_window_seconds=match_window_seconds)
                for event in premium_jobs_by_tool.get(tool_name, [])
            )
            if candidate is not None
        ]
        best, ambiguity = _choose_best_candidate(premium_candidates)
        if best is not None:
            plan.append(
                {
                    **base_entry,
                    **{k: v for k, v in best.items() if not k.startswith("_")},
                    "action": "backfill",
                    "reason": "matched_premium_artifact_job",
                }
            )
            continue
        if ambiguity:
            plan.append({**base_entry, "action": "skip", "reason": ambiguity})
            continue

        plan.append({**base_entry, "action": "skip", "reason": "no_session_evidence_found"})

    return plan


async def _fetch_missing_session_payments(store: SupabaseSessionStore, *, limit: int) -> list[dict[str, Any]]:
    resp = await store._get(  # noqa: SLF001 - operational script living next to store
        "/rest/v1/payments",
        params={
            "select": "id,session_id,tool_name,amount_usdc,tx_hash,timestamp",
            "session_id": "is.null",
            "order": "id.asc",
            "limit": str(max(1, limit)),
        },
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Failed to fetch payments: {resp.status_code} {resp.text[:300]}")
    return resp.json() or []


async def _fetch_backfill_events(store: SupabaseSessionStore, *, limit: int) -> list[dict[str, Any]]:
    resp = await store._get(  # noqa: SLF001 - operational script living next to store
        "/rest/v1/events",
        params={
            "select": "id,session_id,agent_id,event_type,metadata,timestamp",
            "event_type": "in.(x402_payment_verified,premium_artifact_job_recorded)",
            "order": "id.asc",
            "limit": str(max(1, limit)),
        },
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Failed to fetch events: {resp.status_code} {resp.text[:300]}")
    return resp.json() or []


async def _apply_backfill_plan(store: SupabaseSessionStore, plan: list[dict[str, Any]]) -> int:
    applied = 0
    for item in plan:
        if item.get("action") != "backfill":
            continue
        payment_id = int(item.get("payment_id") or 0)
        session_ref = str(item.get("suggested_session_id") or "").strip()
        if payment_id <= 0 or not session_ref:
            continue
        normalized_session = _as_uuid_like(session_ref)
        if not normalized_session:
            continue
        resp = await store._patch(  # noqa: SLF001 - operational script living next to store
            "/rest/v1/payments",
            {"session_id": normalized_session},
            params={"id": f"eq.{payment_id}"},
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Failed to backfill payment {payment_id}: {resp.status_code} {resp.text[:300]}")
        await store.log_event(
            agent_id=str(item.get("source_agent_id") or "audit-backfill"),
            event_type="payments_session_backfilled",
            session_id=session_ref,
            metadata={
                "payment_id": payment_id,
                "tool_name": item.get("tool_name"),
                "tx_hash": item.get("tx_hash"),
                "reason": item.get("reason"),
                "matched_by": item.get("matched_by"),
                "match_delta_seconds": item.get("match_delta_seconds"),
                "source_event_id": item.get("source_event_id"),
                "source_event_type": item.get("source_event_type"),
                "source_session_ref": session_ref,
                "source_session_uuid": normalized_session,
                "payment_timestamp": item.get("payment_timestamp"),
                "source_timestamp": item.get("source_timestamp"),
            },
        )
        applied += 1
    return applied


def _render_text_summary(plan: list[dict[str, Any]]) -> str:
    total = len(plan)
    candidates = sum(1 for item in plan if item.get("action") == "backfill")
    skipped = total - candidates
    lines = [
        f"payments_missing_session={total}",
        f"backfill_candidates={candidates}",
        f"skipped={skipped}",
        "",
    ]
    for item in plan:
        if item.get("action") == "backfill":
            lines.append(
                "BACKFILL "
                f"payment_id={item['payment_id']} tool={item['tool_name']} "
                f"session_ref={item['suggested_session_id']} source={item['source_event_type']}#{item['source_event_id']} "
                f"delta_s={item['match_delta_seconds']} reason={item['reason']}"
            )
        else:
            lines.append(
                "SKIP "
                f"payment_id={item['payment_id']} tool={item['tool_name']} reason={item['reason']}"
            )
    return "\n".join(lines)


async def _async_main(args: argparse.Namespace) -> int:
    store = SupabaseSessionStore()
    await store.init()
    try:
        payments = await _fetch_missing_session_payments(store, limit=args.payment_limit)
        events = await _fetch_backfill_events(store, limit=args.event_limit)
        plan = build_payment_session_backfill_plan(
            payments,
            events,
            match_window_seconds=float(args.match_window_seconds),
        )
        if args.json:
            print(json.dumps(plan, indent=2, ensure_ascii=False))
        else:
            print(_render_text_summary(plan))
        if not args.apply:
            return 0
        applied = await _apply_backfill_plan(store, plan)
        print(f"\napplied_backfills={applied}")
        return 0
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill payments.session_id using nearby x402 and artifact events.")
    parser.add_argument("--apply", action="store_true", help="Apply the proposed backfill instead of dry-run output only.")
    parser.add_argument("--json", action="store_true", help="Print the plan as JSON.")
    parser.add_argument("--match-window-seconds", type=float, default=120.0, help="Maximum timestamp delta allowed for matching.")
    parser.add_argument("--payment-limit", type=int, default=500, help="Maximum number of null-session payment rows to inspect.")
    parser.add_argument("--event-limit", type=int, default=10000, help="Maximum number of candidate events to inspect.")
    return asyncio.run(_async_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
