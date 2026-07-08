from __future__ import annotations

import json
import re
from typing import Any

from config import settings

_SECRET_KEY_RE = re.compile(
    r"(authorization|cookie|set-cookie|token|secret|api[_-]?key|signature|sig|proof|credential|jwt)",
    re.IGNORECASE,
)


def trace_capture_enabled() -> bool:
    return bool(getattr(settings, "TRACE_CAPTURE_ENABLED", True))


def _sanitize_trace_value(value: Any, key_path: tuple[str, ...] = ()) -> Any:
    current_key = key_path[-1] if key_path else ""
    if current_key and _SECRET_KEY_RE.search(current_key):
        return "[REDACTED]"

    if isinstance(value, dict):
        return {
            str(k): _sanitize_trace_value(v, key_path + (str(k),))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_trace_value(v, key_path) for v in value]
    if isinstance(value, tuple):
        return [_sanitize_trace_value(v, key_path) for v in value]
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return repr(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
        except Exception:
            return repr(value)
        return _sanitize_trace_value(dumped, key_path)
    return repr(value)


def sanitize_trace_payload(payload: Any) -> Any:
    return _sanitize_trace_value(payload)


def trace_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return repr(value)
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(sanitize_trace_payload(value), ensure_ascii=False, sort_keys=True)
        except Exception:
            return repr(value)
    if hasattr(value, "model_dump"):
        try:
            return json.dumps(sanitize_trace_payload(value.model_dump()), ensure_ascii=False, sort_keys=True)
        except Exception:
            return repr(value)
    return str(value)


async def persist_interaction_trace(store: Any, **payload: Any) -> None:
    if not trace_capture_enabled():
        return
    saver = getattr(store, "save_interaction_trace", None)
    if not callable(saver):
        return
    await saver(**sanitize_trace_payload(payload))


async def persist_protocol_trace(store: Any, **payload: Any) -> None:
    if not trace_capture_enabled():
        return
    saver = getattr(store, "save_protocol_trace", None)
    if not callable(saver):
        return
    await saver(**sanitize_trace_payload(payload))
