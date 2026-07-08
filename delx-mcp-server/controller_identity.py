from __future__ import annotations

import re
from typing import Any

_CONTROLLER_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")


def sanitize_controller_id(raw: Any) -> str | None:
    value = _CONTROLLER_RE.sub("-", str(raw or "").strip()).strip("-_.:")
    if not value:
        return None
    return value[:120]


def first_controller_id(*candidates: Any) -> str | None:
    for candidate in candidates:
        value = sanitize_controller_id(candidate)
        if value:
            return value
    return None
