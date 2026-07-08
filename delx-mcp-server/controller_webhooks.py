from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from controller_identity import sanitize_controller_id

_ALLOWED_EVENTS = {"score_drop", "incident", "recovery_completed"}


def controller_agent_key(controller_id: str) -> str:
    cid = sanitize_controller_id(controller_id) or "unknown"
    return f"__controller__:{cid}"


def normalize_webhook_events(events: list[str] | None) -> list[str]:
    if not isinstance(events, list) or not events:
        return ["score_drop", "incident", "recovery_completed"]
    normalized = sorted({str(event or "").strip().lower() for event in events if str(event or "").strip().lower() in _ALLOWED_EVENTS})
    return normalized or ["score_drop", "incident", "recovery_completed"]


def fold_controller_webhooks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    webhooks: dict[str, dict[str, Any]] = {}
    ordered = sorted(events, key=lambda row: str(row.get("timestamp") or ""))
    for row in ordered:
        event_type = str(row.get("event_type") or "")
        meta = row.get("metadata") or {}
        if not meta and row.get("metadata_json"):
            try:
                meta = json.loads(str(row.get("metadata_json") or "{}"))
            except Exception:
                meta = {}
        webhook_id = str(meta.get("webhook_id") or "").strip()
        if not webhook_id:
            continue
        if event_type == "controller_webhook_registered":
            webhooks[webhook_id] = {
                "id": webhook_id,
                "controller_id": str(meta.get("controller_id") or "").strip(),
                "callback_url": str(meta.get("callback_url") or "").strip(),
                "events": normalize_webhook_events(meta.get("events") if isinstance(meta.get("events"), list) else []),
                "threshold": int(meta.get("threshold") or 35),
                "cooldown_min": int(meta.get("cooldown_min") or 30),
                "is_active": True,
                "created_at": str(meta.get("created_at") or row.get("timestamp") or ""),
                "updated_at": str(row.get("timestamp") or ""),
                "last_test_at": None,
                "last_delivery_at": None,
                "last_delivery_event": None,
                "last_delivery_success": None,
            }
        elif event_type == "controller_webhook_deactivated" and webhook_id in webhooks:
            webhooks[webhook_id]["is_active"] = False
            webhooks[webhook_id]["updated_at"] = str(row.get("timestamp") or "")
        elif event_type in {"controller_webhook_sent", "controller_webhook_failed", "controller_webhook_tested"} and webhook_id in webhooks:
            item = webhooks[webhook_id]
            item["updated_at"] = str(row.get("timestamp") or "")
            if event_type == "controller_webhook_tested":
                item["last_test_at"] = str(row.get("timestamp") or "")
            else:
                item["last_delivery_at"] = str(row.get("timestamp") or "")
                item["last_delivery_event"] = str(meta.get("event") or "").strip().lower() or None
                item["last_delivery_success"] = event_type == "controller_webhook_sent"

    items = [row for row in webhooks.values() if row.get("is_active")]
    items.sort(key=lambda row: (str(row.get("updated_at") or ""), str(row.get("created_at") or "")), reverse=True)
    return items


def create_controller_webhook_record(
    controller_id: str,
    callback_url: str,
    *,
    events: list[str] | None = None,
    threshold: int = 35,
    cooldown_min: int = 30,
) -> dict[str, Any]:
    cid = sanitize_controller_id(controller_id) or ""
    if not cid:
        raise ValueError("controller_id is required")
    callback = str(callback_url or "").strip()
    if not callback.startswith("https://"):
        raise ValueError("callback_url must start with https://")
    return {
        "webhook_id": f"wh_{uuid.uuid4().hex[:16]}",
        "controller_id": cid,
        "callback_url": callback[:500],
        "events": normalize_webhook_events(events),
        "threshold": max(1, min(int(threshold or 35), 100)),
        "cooldown_min": max(1, min(int(cooldown_min or 30), 24 * 60)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def delivery_allowed(webhook: dict[str, Any], event: str, recent_events: list[dict[str, Any]], now: datetime) -> bool:
    cooldown_min = max(1, min(int(webhook.get("cooldown_min") or 30), 24 * 60))
    for row in recent_events:
        event_type = str(row.get("event_type") or "")
        if event_type != "controller_webhook_sent":
            continue
        meta = row.get("metadata") or {}
        if str(meta.get("webhook_id") or "") != str(webhook.get("id") or ""):
            continue
        if str(meta.get("event") or "").strip().lower() != event:
            continue
        timestamp = str(row.get("timestamp") or "")
        if not timestamp:
            continue
        try:
            sent_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except Exception:
            continue
        if now - sent_at < timedelta(minutes=cooldown_min):
            return False
        break
    return True
