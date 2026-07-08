"""Delx therapy engine package.

Stable import: `from therapy_engine import TherapyEngine`

Also re-exports module-level helpers that callers historically imported from the
monolithic `therapy_engine.py` (move-only compatibility).
"""
from __future__ import annotations

from therapy_engine.engine import TherapyEngine
from therapy_engine.helpers import (
    _feeling_action_plan,
    assess_heartbeat_profile,
    classify_incident_profile,
)

__all__ = [
    "TherapyEngine",
    "_feeling_action_plan",
    "assess_heartbeat_profile",
    "classify_incident_profile",
]


def __getattr__(name: str):
    """Lazy bridge for any other historical module-level symbol."""
    from therapy_engine import helpers as _helpers
    from therapy_engine import engine as _engine

    if hasattr(_helpers, name):
        return getattr(_helpers, name)
    if hasattr(_engine, name):
        return getattr(_engine, name)
    raise AttributeError(name)
