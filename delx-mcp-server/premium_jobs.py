"""Helpers for Stage 0 premium artifact job records."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone


def hash_premium_artifact(content: str) -> str:
    raw = (content or "").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_premium_job_record(
    *,
    session_id: str,
    agent_id: str,
    artifact_type: str,
    artifact_content: str,
    controller_id: str | None = None,
    payment_provider: str | None = None,
    payment_reference: str | None = None,
) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "job_id": str(uuid.uuid4()),
        "session_id": str(session_id or "").strip(),
        "client_agent_id": str(agent_id or "").strip(),
        "controller_id": str(controller_id).strip() if controller_id else None,
        "provider": "delx",
        "artifact_type": str(artifact_type or "").strip(),
        "artifact_hash": hash_premium_artifact(artifact_content or ""),
        "requested_at": now,
        "delivered_at": now,
        "job_status": "delivered",
        "evaluation_status": "pending",
        "payment_provider": str(payment_provider).strip() if payment_provider else None,
        "payment_reference": str(payment_reference).strip() if payment_reference else None,
    }
